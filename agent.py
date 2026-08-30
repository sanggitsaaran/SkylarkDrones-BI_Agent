"""
agent.py
--------
The conversational BI agent. Wraps Groq's OpenAI-compatible tool-use loop:
the model reads the conversation, decides whether it needs data from
monday.com, calls tools.py to get it, and keeps looping until it has
enough to answer — then produces a founder-friendly natural-language
response (with caveats about data quality when relevant).

Uses Groq (https://groq.com) as the LLM provider — fast, OpenAI-compatible
chat-completions API with tool calling support on all hosted models.
"""

import json
from typing import Dict, List

from groq import Groq

from monday_client import MondayClient
from tools import TOOL_DEFINITIONS, execute_tool, to_openai_tool_format

# Llama 3.3 70B: strong tool-use accuracy + quality tradeoff among Groq's hosted models.
MODEL = "openai/gpt-oss-120b"
MAX_TOOL_ITERATIONS = 8

OPENAI_TOOLS = to_openai_tool_format(TOOL_DEFINITIONS)

SYSTEM_PROMPT = """You are the Skylark Drones Business Intelligence agent. You help \
founders and executives get quick, accurate answers by querying two live monday.com \
boards: "Work Orders" (project execution data) and "Deals" (sales pipeline data).

Rules you must follow:
1. NEVER invent or guess data. Every number in your answer must come from a tool call. \
If you haven't queried monday.com for something, you don't know it yet — call a tool.
2. Before filtering or aggregating on a board for the first time in a conversation, call \
get_board_schema so you use real column names (they will NOT always match this prompt's \
wording — e.g. the user might say "sector" but the column could be "Industry").
3. The underlying data is messy: inconsistent casing, spacing, date formats, and missing \
values. The query_board_data tool already normalizes/filters tolerantly and reports \
caveats — always read the "caveats" field and mention relevant ones to the user in plain \
language (e.g. "note: 4 deals had no close date and were excluded from this figure").
4. If a founder's question is ambiguous (e.g. "this quarter" — which quarter? "pipeline" \
— which stage counts as pipeline?), ask a brief clarifying question rather than guessing, \
UNLESS a reasonable default is obvious, in which case state the assumption you're making \
and proceed.
5. When answering, don't just dump numbers — give brief business context (e.g. compare to \
total, note if a segment looks concentrated/risky, flag if data quality limits confidence).
6. When asked to query "across both boards" (e.g. correlating work orders with deals), \
call query_board_data on each board separately and reason over both result sets yourself \
— there is no single cross-board tool.
7. Keep answers concise and founder-appropriate: lead with the answer, then 1-3 sentences \
of context/caveats. Avoid dumping raw tables unless asked.
"""


def run_agent_turn(
    client_secrets: Dict[str, str],
    conversation_history: List[Dict],
) -> str:
    """
    Run one full agent turn (may involve several internal tool-call round-trips)
    and return the final assistant text to show the user.

    conversation_history: list of {"role": "user"|"assistant", "content": str}
    in plain OpenAI/Groq chat format (no tool blocks — those are internal to
    this function and not persisted across turns).
    """
    groq_client = Groq(api_key=client_secrets["groq_api_key"])
    monday_client = MondayClient(api_token=client_secrets["monday_api_token"])

    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(conversation_history)

    for _ in range(MAX_TOOL_ITERATIONS):
        response = groq_client.chat.completions.create(
            model=MODEL,
            max_tokens=1500,
            tools=OPENAI_TOOLS,
            messages=messages,
        )

        message = response.choices[0].message
        tool_calls = message.tool_calls

        if not tool_calls:
            return (message.content or "").strip() or "(no response generated)"

        # Model wants to call one or more tools — execute each and feed results back
        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            try:
                tool_input = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                tool_input = {}
            result_str = execute_tool(monday_client, tc.function.name, tool_input)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                }
            )

    return (
        "I wasn't able to finish answering that within my tool-call budget — "
        "could you narrow the question (e.g. a specific sector or time period)?"
    )
