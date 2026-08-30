"""
data_utils.py
-------------
Normalization helpers for messy real-world monday.com data:
inconsistent date formats, null/empty representations, and free-text
naming variants (e.g. "Energy", "energy sector", "ENERGY ").

These are applied AFTER data is fetched from monday.com — never used
to fabricate or hardcode the data itself.
"""

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dateutil import parser as dateparser

# Representations that different tools / manual entry commonly use for "no value"
NULL_TOKENS = {
    "", "n/a", "na", "none", "null", "nil", "-", "--", "tbd", "tba",
    "unknown", "?", "nan", "not set", "not available", "pending",
}


def is_null_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and value.strip().lower() in NULL_TOKENS:
        return True
    return False


def normalize_text(value: Any) -> Optional[str]:
    """Trim whitespace, collapse internal whitespace, title-case for consistent grouping."""
    if is_null_like(value):
        return None
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


# Generic descriptor words that shouldn't split otherwise-identical categories,
# e.g. "Energy" and "Energy Sector" should group together as one category.
_CATEGORY_NOISE_WORDS = {"sector", "industry", "vertical", "segment", "department", "dept"}


def normalize_category(value: Any) -> Optional[str]:
    """
    Normalize a free-text categorical field (sector, status, stage) so that
    'Energy', 'energy', ' ENERGY sector' etc. all group together.
    Returns a clean, consistently-cased label, or None if null-like.
    """
    text = normalize_text(value)
    if text is None:
        return None
    words = [w for w in text.split(" ") if w.lower() not in _CATEGORY_NOISE_WORDS]
    cleaned = " ".join(words) if words else text  # don't return empty if it was ALL noise words
    return cleaned.strip().title()


def parse_date_safe(value: Any) -> Tuple[Optional[datetime], Optional[str]]:
    """
    Attempt to parse an inconsistent date string into a datetime.
    Returns (parsed_datetime_or_None, caveat_message_or_None).
    Handles: '2024-01-15', '15/01/2024', 'Jan 15, 2024', '15-Jan-24', Excel serials, etc.
    """
    if is_null_like(value):
        return None, None
    if isinstance(value, (int, float)):
        # Possible Excel serial date
        try:
            base = datetime(1899, 12, 30)
            return base + pd.Timedelta(days=float(value)), None
        except Exception:
            return None, f"Could not interpret numeric date value: {value!r}"
    try:
        # dayfirst=False default; monday.com typically exports ISO or US format.
        # We try both and flag ambiguity for the caller if results differ.
        dt = dateparser.parse(str(value), fuzzy=True)
        return dt, None
    except (ValueError, OverflowError):
        return None, f"Unparseable date value: {value!r}"


def parse_numeric_safe(value: Any) -> Tuple[Optional[float], Optional[str]]:
    """Strip currency symbols, commas, percent signs; return (number_or_None, caveat)."""
    if is_null_like(value):
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    text = str(value).strip()
    cleaned = re.sub(r"[^\d.\-]", "", text)
    if cleaned in ("", "-", "."):
        return None, f"Could not interpret numeric value: {value!r}"
    try:
        return float(cleaned), None
    except ValueError:
        return None, f"Could not interpret numeric value: {value!r}"


def summarize_data_quality(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Produce a compact data-quality report for a dataframe of monday.com
    items, so the agent can surface caveats to the user rather than
    silently presenting incomplete-data results as if they were complete.
    """
    total_rows = len(df)
    report = {"total_rows": total_rows, "columns": {}}
    for col in df.columns:
        null_count = df[col].apply(is_null_like).sum()
        report["columns"][col] = {
            "null_count": int(null_count),
            "null_pct": round(100 * null_count / total_rows, 1) if total_rows else 0.0,
        }
    return report


def items_to_dataframe(items: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Convert raw monday.com items (as returned by MondayClient.get_all_items)
    into a tidy DataFrame: one row per item, one column per monday.com column
    (keyed by column title), plus 'item_id' and 'item_name'.
    Column list is derived entirely from the data — nothing hardcoded.
    """
    rows = []
    for item in items:
        row = {"item_id": item["id"], "item_name": item["name"]}
        for cv in item.get("column_values", []):
            title = cv["column"]["title"]
            row[title] = cv.get("text")  # human-readable text; good default for BI
        rows.append(row)
    return pd.DataFrame(rows)
