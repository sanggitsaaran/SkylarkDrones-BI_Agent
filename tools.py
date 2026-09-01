"""
tools.py
--------
Defines the tool set exposed to the Claude agent (Anthropic tool-use),
plus the Python functions that actually execute each tool call against
monday.com. Tool outputs are JSON-serializable dicts/strings — the
model consumes them and decides what to do next (multi-turn tool loop
lives in agent.py).

Two categories:
  1. Discovery tools  — list boards, get schema (agent should call
     these before assuming any column exists).
  2. Data tools       — fetch rows, compute aggregates. Real
     computation (sums, counts, grouping) happens in Python/pandas,
     not by asking the LLM to do arithmetic over raw text.
"""

import json
from typing import Any, Dict, List, Optional

import pandas as pd

from monday_client import MondayClient, MondayAPIError
from data_utils import (
    items_to_dataframe,
    normalize_category,
    parse_date_safe,
    parse_numeric_safe,
    summarize_data_quality,
    is_null_like,
)

# Simple in-process cache so repeated questions in one conversation don't
# re-fetch the whole board every time. Keyed by board_id, cleared per session
# by Streamlit's session_state (see app.py) rather than globally, to avoid
# stale cross-user data on a shared hosted instance.
_board_cache: Dict[str, pd.DataFrame] = {}


def _get_board_df(client: MondayClient, board_id: str, force_refresh: bool = False) -> pd.DataFrame:
    if force_refresh or board_id not in _board_cache:
        items = client.get_all_items(board_id)
        _board_cache[board_id] = items_to_dataframe(items)
    return _board_cache[board_id]


def clear_cache():
    _board_cache.clear()


# ---------------------------------------------------------------------- #
# Tool schema (Anthropic tool-use format)
# ---------------------------------------------------------------------- #
def to_openai_tool_format(tool_definitions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Convert our tool definitions (Anthropic-style: name/description/input_schema)
    into OpenAI/Groq-style (type:'function', function:{name,description,parameters}).
    Kept as a pure converter so TOOL_DEFINITIONS stays the single source of truth.
    """
    converted = []
    for t in tool_definitions:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
        )
    return converted


TOOL_DEFINITIONS = [
    {
        "name": "list_boards",
        "description": (
            "List all monday.com boards accessible to this integration, with their "
            "ids and names. Call this first if you don't already know the board ids "
            "for 'Work Orders' and 'Deals' (names may differ slightly from these)."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_board_schema",
        "description": (
            "Get the column names and types for a given board. ALWAYS call this before "
            "querying a board's data for the first time in a conversation, so you know "
            "the exact column names to filter/group by — never assume a column exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "board_name_or_id": {
                    "type": "string",
                    "description": "Board name (fuzzy match ok) or exact board id.",
                }
            },
            "required": ["board_name_or_id"],
        },
    },
    {
        "name": "query_board_data",
        "description": (
            "Fetch rows from a monday.com board, optionally filtered by a text match on "
            "one column, and optionally aggregated (count/sum/avg) grouped by another "
            "column. Use this for all data questions — never estimate numbers yourself. "
            "Text filters are matched case-insensitively and allow partial matches, since "
            "the underlying data has inconsistent casing/spacing (e.g. 'Energy' vs 'energy sector')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "board_name_or_id": {"type": "string"},
                "filter_column": {
                    "type": "string",
                    "description": "Column title to filter on (optional).",
                },
                "filter_value": {
                    "type": "string",
                    "description": "Substring to match within filter_column, case-insensitive (optional).",
                },
                "group_by_column": {
                    "type": "string",
                    "description": "Column title to group rows by before aggregating (optional).",
                },
                "aggregate": {
                    "type": "string",
                    "enum": ["count", "sum", "avg", "min", "max"],
                    "description": "Aggregation to compute. 'count' needs no numeric column.",
                },
                "aggregate_column": {
                    "type": "string",
                    "description": "Numeric column to aggregate (required unless aggregate is 'count').",
                },
                "date_column": {
                    "type": "string",
                    "description": "If filtering by a time period, the date column to use (optional).",
                },
                "date_from": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) lower bound, inclusive (optional).",
                },
                "date_to": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) upper bound, inclusive (optional).",
                },
                "limit_rows_returned": {
                    "type": "integer",
                    "description": "Cap on raw rows to return when NOT aggregating (default 12, max 20 — kept small to stay within API rate limits). Ignored when aggregating. Prefer 'aggregate' for any overview/summary question; only fetch raw rows when the user needs to see specific individual items.",
                },
            },
            "required": ["board_name_or_id"],
        },
    },
    {
        "name": "get_data_quality_report",
        "description": (
            "Get null/missing-value statistics for every column on a board, so you can "
            "warn the user about incomplete data underlying an answer (e.g. '12% of deals "
            "are missing a close date, so this trend may be understated')."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"board_name_or_id": {"type": "string"}},
            "required": ["board_name_or_id"],
        },
    },
]


# ---------------------------------------------------------------------- #
# Tool execution
# ---------------------------------------------------------------------- #
def _resolve_board_id(client: MondayClient, board_name_or_id: str) -> str:
    boards = client.list_boards()
    for b in boards:
        if b["id"] == board_name_or_id:
            return b["id"]
    board_id = client.find_board_id_by_name(board_name_or_id)
    if not board_id:
        available = ", ".join(b["name"] for b in boards)
        raise MondayAPIError(
            f"No board matching '{board_name_or_id}' found. Available boards: {available}"
        )
    return board_id


def execute_tool(client: MondayClient, tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Dispatch a tool call to its implementation. Always returns a JSON string
    (or a clear error string) — never raises, so the agent loop can hand the
    error back to the model and let it recover conversationally."""
    try:
        if tool_name == "list_boards":
            boards = client.list_boards()
            return json.dumps([{"id": b["id"], "name": b["name"]} for b in boards])

        if tool_name == "get_board_schema":
            board_id = _resolve_board_id(client, tool_input["board_name_or_id"])
            schema = client.get_board_schema(board_id)
            cols = [{"title": c["title"], "type": c["type"]} for c in schema["columns"]]
            return json.dumps({"board_id": board_id, "board_name": schema["name"], "columns": cols})

        if tool_name == "get_data_quality_report":
            board_id = _resolve_board_id(client, tool_input["board_name_or_id"])
            df = _get_board_df(client, board_id)
            return json.dumps(summarize_data_quality(df))

        if tool_name == "query_board_data":
            return _query_board_data(client, tool_input)

        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    except MondayAPIError as e:
        return json.dumps({"error": f"monday.com API error: {e}"})
    except Exception as e:  # noqa: BLE001 — surface any failure to the model gracefully
        return json.dumps({"error": f"Tool execution failed: {e}"})


def _query_board_data(client: MondayClient, params: Dict[str, Any]) -> str:
    board_id = _resolve_board_id(client, params["board_name_or_id"])
    df = _get_board_df(client, board_id).copy()
    caveats: List[str] = []

    if df.empty:
        return json.dumps({"row_count": 0, "note": "Board has no items."})

    # --- text filter (case-insensitive, partial match, null-tolerant) ---
    filter_col = params.get("filter_column")
    filter_val = params.get("filter_value")
    if filter_col and filter_val:
        if filter_col not in df.columns:
            return json.dumps(
                {"error": f"Column '{filter_col}' not found. Available: {list(df.columns)}"}
            )
        mask = df[filter_col].apply(
            lambda v: (not is_null_like(v)) and filter_val.strip().lower() in str(v).strip().lower()
        )
        matched = int(mask.sum())
        df = df[mask]
        if matched == 0:
            caveats.append(
                f"No rows matched '{filter_val}' in '{filter_col}'. Values present include: "
                f"{sorted(set(str(x) for x in df[filter_col].dropna().unique()))[:10]}"
            )

    # --- date range filter ---
    date_col = params.get("date_column")
    date_from = params.get("date_from")
    date_to = params.get("date_to")
    if date_col and (date_from or date_to):
        if date_col not in df.columns:
            return json.dumps(
                {"error": f"Column '{date_col}' not found. Available: {list(df.columns)}"}
            )
        parsed = df[date_col].apply(parse_date_safe)
        parsed_dates = parsed.apply(lambda t: t[0])
        unparsed_count = int(parsed.apply(lambda t: t[0] is None and t[1] is not None).sum())
        if unparsed_count:
            caveats.append(
                f"{unparsed_count} rows had an unparseable '{date_col}' value and were excluded "
                "from the date filter."
            )
        keep = pd.Series(True, index=df.index)
        if date_from:
            keep &= parsed_dates >= pd.Timestamp(date_from)
        if date_to:
            keep &= parsed_dates <= pd.Timestamp(date_to)
        keep &= parsed_dates.notna()
        df = df[keep]

    # --- aggregation ---
    aggregate = params.get("aggregate")
    group_by = params.get("group_by_column")
    agg_col = params.get("aggregate_column")

    if aggregate:
        if group_by and group_by not in df.columns:
            return json.dumps({"error": f"Column '{group_by}' not found. Available: {list(df.columns)}"})

        if aggregate != "count":
            if not agg_col or agg_col not in df.columns:
                return json.dumps(
                    {"error": f"'{agg_col}' not found or not provided for aggregate='{aggregate}'. "
                              f"Available: {list(df.columns)}"}
                )
            numeric_series = df[agg_col].apply(lambda v: parse_numeric_safe(v)[0])
            null_count = int(df[agg_col].apply(is_null_like).sum())
            unparseable = int(numeric_series.isna().sum() - null_count)
            if null_count > 0:
                caveats.append(
                    f"{null_count} rows had no value in '{agg_col}' (missing data) and were "
                    "excluded from the aggregation."
                )
            if unparseable > 0:
                caveats.append(
                    f"{unparseable} rows had a non-numeric '{agg_col}' value and were excluded "
                    "from the aggregation."
                )
            df = df.assign(_agg_val=numeric_series)

        if group_by:
            df = df.assign(_group_key=df[group_by].apply(lambda v: normalize_category(v) or "(blank)"))
            if aggregate == "count":
                result = df.groupby("_group_key").size().sort_values(ascending=False)
            else:
                result = df.groupby("_group_key")["_agg_val"].agg(aggregate).sort_values(ascending=False)
            return json.dumps(
                {
                    "aggregate": aggregate,
                    "grouped_by": group_by,
                    "results": {str(k): (None if pd.isna(v) else round(float(v), 2)) for k, v in result.items()},
                    "total_rows_considered": int(len(df)),
                    "caveats": caveats,
                }
            )
        else:
            if aggregate == "count":
                value = int(len(df))
            else:
                value = df["_agg_val"].agg(aggregate)
                value = None if pd.isna(value) else round(float(value), 2)
            return json.dumps({"aggregate": aggregate, "result": value, "row_count": int(len(df)), "caveats": caveats})

    # --- no aggregation: return raw rows (capped tightly to stay within LLM token/rate limits) ---
    limit = int(params.get("limit_rows_returned") or 12)
    limit = min(limit, 20)
    total_matched = len(df)
    df = df.head(limit)
    # Trim to a smaller set of columns if the row is very wide, to further reduce payload size.
    return json.dumps(
        {
            "row_count_returned": int(len(df)),
            "total_rows_matched": int(total_matched),
            "truncated": total_matched > limit,
            "rows": df.to_dict(orient="records"),
            "caveats": caveats,
        }
    )
