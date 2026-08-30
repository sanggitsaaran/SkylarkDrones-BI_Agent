# Decision Log — Skylark Drones BI Agent

## Key Assumptions
- **Board naming**: I don't control exactly how the grader names their two
  boards during import, so board resolution is fuzzy-matched (`list_boards`
  + case-insensitive substring match) rather than hardcoded to "Deals" /
  "Work Orders" exactly. Same logic applies to columns — the agent calls
  `get_board_schema` before assuming a column name (e.g. "Sector" vs
  "Industry" vs "Vertical").
- **"Text" column values are the source of truth**: monday.com returns both
  a raw `value` (JSON) and a human-readable `text` per column. I use `text`
  for BI purposes since it's already normalized to monday.com's column-type
  formatting (dates, numbers, labels), and it's what a human reading the
  board would see — trading a small amount of raw-JSON fidelity for
  simplicity and reliability under time pressure.
- **"This quarter" and similar relative time phrases**: the agent is
  instructed to either ask a clarifying question or state an explicit
  assumption (e.g. "assuming Q3 2026 = Jul–Sep") rather than silently
  guessing, per the assignment's query-understanding requirement.
- **Null/messy tokens**: treated `"", "N/A", "TBD", "unknown", "-", "pending"`
  etc. as null-equivalent (see `data_utils.NULL_TOKENS`). This list is a
  reasonable starting set for real-world sales/ops data, not exhaustive.

## Trade-offs Chosen and Why
- **Groq (Llama 3.3 70B) as the LLM provider**: chosen for available API access and
  low-latency tool-calling inference. The agent talks to it entirely through
  the OpenAI-compatible chat-completions/tool-use interface (`agent.py`),
  so swapping to a different OpenAI-compatible provider (or Anthropic/OpenAI
  directly) later is a small, isolated change — the tool definitions and
  monday.com logic are provider-agnostic (`tools.py`, `monday_client.py`).
- **Streamlit over a custom React/FastAPI stack**: given the 6-hour budget,
  Streamlit gets a working conversational UI + one-click hosted deploy
  (Streamlit Community Cloud) without spending hours on frontend plumbing.
  Trade-off: less UI polish/control than a bespoke frontend.
- **Computation in pandas, not in the LLM**: all filtering/aggregation
  happens in Python (`tools.py`) using real arithmetic over the fetched
  data, with the LLM only choosing *which* tool calls to make and how to
  phrase the answer. This avoids the classic failure mode of an LLM
  "eyeballing" sums over a table of text and getting them subtly wrong —
  correctness mattered more here than letting the model be clever.
- **Dynamic schema discovery over a fixed data model**: costs a couple of
  extra tool calls per fresh conversation (schema lookup before querying),
  but directly satisfies "don't hardcode CSV data" and makes the agent
  resilient to the boards being restructured or renamed after setup.
- **In-process cache, not a database**: board data is cached in memory per
  Streamlit session after first fetch, with a manual "Refresh" button,
  rather than re-fetching on every message (slow) or standing up a
  database/cron sync (over-engineered for a 6-hour prototype and a
  read-only requirement).
- **No cross-board join tool**: rather than building a generic SQL-like
  join engine over two arbitrary schemas, cross-board questions are
  answered by the model making two separate `query_board_data` calls and
  reasoning over both result sets in natural language. Faster to build and
  transparent, at the cost of not handling very large joins efficiently.

## Interpretation of "Leadership Updates"
I interpreted this as: the agent should be able to produce a short,
structured summary suitable for pasting into a leadership deck/email —
not a separate reporting subsystem. Concretely, the agent's system prompt
instructs it to lead with the headline number, then 1–3 sentences of
context and caveats (data-quality flags, notable concentration/risk) —
the same shape a founder would want in a leadership update. A user can
ask "summarize this for a leadership update" and get that format applied
to whatever query preceded it, reusing the same tool infrastructure rather
than a separate code path. Given the "optional" tag and time budget, I
did not build a dedicated export-to-slide/PDF feature — that's the first
thing I'd add with more time (see below).

## What I'd Do Differently With More Time
- **Structured leadership-update export**: a "Generate leadership summary"
  button that produces a formatted one-pager (PDF/slide) covering both
  boards, not just a chat answer.
- **A real cross-board join tool**: e.g. matching Work Orders to Deals by
  a shared client/deal-ID column (once I know the real schema), so the
  agent can answer questions like "which delayed work orders are tied to
  our biggest deals" more precisely than free-form reasoning over two
  separate result sets.
- **Persistent cache with staleness awareness**: e.g. show "data as of
  [timestamp]" in the UI and auto-refresh boards older than N minutes,
  instead of a manual refresh button.
- **Column-type-aware querying**: right now dates/numbers are parsed from
  monday.com's `text` representation; using the typed `value` JSON
  directly for status/date columns would be more robust for edge cases
  (e.g. monday.com "status" columns with custom label colors).
- **Automated tests** against a small fixture board / mocked GraphQL
  responses — skipped entirely under the time constraint, which is the
  biggest quality risk in this submission.
- **Tighter clarifying-question UX**: currently the agent asks clarifying
  questions inline in chat; a quick-reply button UI (like "Q3 2026" /
  "trailing 90 days") would reduce back-and-forth.
