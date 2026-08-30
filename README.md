# Skylark Drones — monday.com BI Agent

A conversational agent that answers founder-level business questions by
querying two live monday.com boards (**Work Orders**, **Deals**) — no
hardcoded data, no local setup required to test.

## Architecture

```
┌─────────────┐      ┌──────────────┐      ┌───────────────────┐
│  Streamlit   │ ---> │  agent.py     │ ---> │  Groq (Llama 3.3 70B)  │
│  chat UI     │      │  (tool loop)  │      │  (Llama 3.3 70B, tool use)│
│  app.py      │ <--- │               │ <--- │                    │
└─────────────┘      └──────┬───────┘      └───────────────────┘
                             │  tool calls
                             v
                      ┌──────────────┐      ┌───────────────────┐
                      │  tools.py     │ ---> │  monday_client.py  │
                      │  (BI logic:   │      │  (GraphQL API,     │
                      │  filter/group/│ <--- │  schema discovery, │
                      │  aggregate,   │      │  pagination)       │
                      │  data-quality)│      └────────┬──────────┘
                      └──────────────┘               v
                                              ┌───────────────────┐
                                              │   monday.com API    │
                                              │  (Work Orders, Deals)│
                                              └───────────────────┘
```

**Flow for a query like "How's pipeline looking for energy this quarter?":**
1. Streamlit passes the message to `agent.py`, which calls the Groq-hosted model with the
   conversation + the tool definitions in `tools.py`.
2. The model decides it needs the `Deals` board schema first (`get_board_schema`)
   to find the right column names (e.g. "Sector" vs "Industry").
3. It then calls `query_board_data` with a filter (sector contains
   "energy") and an aggregation (sum of deal value, grouped by stage).
4. `tools.py` fetches the board via `monday_client.py` (paginated GraphQL),
   normalizes messy values (`data_utils.py`), computes the aggregation in
   pandas, and returns numbers **plus caveats** (e.g. rows excluded for
   unparseable dates/numbers).
5. It turns that into a founder-friendly answer, surfacing caveats.

### Why this design
- **No hardcoding**: the agent calls `get_board_schema`/`list_boards` at
  runtime rather than assuming column names — it adapts if boards are
  restructured.
- **Real computation, not LLM arithmetic**: all sums/counts/averages are
  computed in pandas (`tools.py`), not guessed by the model from raw text.
- **Data resilience**: `data_utils.py` centralizes null-token detection,
  fuzzy/case-insensitive category matching, flexible date parsing (handles
  Excel serials, multiple date formats), and numeric cleanup (currency
  symbols, commas). Every filter/aggregation reports what it had to skip
  and why, so the agent can be transparent about data quality instead of
  silently presenting partial data as complete.
- **Streamlit** for the UI: fastest path to a testable hosted link within a
  6-hour budget, with a built-in chat component.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit chat frontend (entry point for deployment) |
| `agent.py` | Groq tool-use loop, system prompt |
| `tools.py` | Tool definitions + BI logic (filter/group/aggregate/data-quality) |
| `monday_client.py` | Read-only monday.com GraphQL client, schema discovery, pagination |
| `data_utils.py` | Normalization: dates, numbers, categories, null detection |
| `import_helper.py` | Optional script to push the assignment's xlsx files into monday.com via API |
| `requirements.txt` | Python dependencies |
| `.streamlit/secrets.toml.example` | Template for required API keys |

## Setup: monday.com

1. Create a free monday.com account.
2. Get the two data files from the assignment (Deal funnel Data.xlsx,
   Work_Order_Tracker Data.xlsx).
3. Import them into two boards, either:
   - **UI**: In monday.com, create a board, use *File > Import*, upload the
     xlsx, review auto-detected column types; **or**
   - **Script**: `python import_helper.py --token YOUR_TOKEN --deals "Deal funnel Data.xlsx" --work-orders "Work_Order_Tracker Data.xlsx"`
4. Generate a personal API token: avatar → Developers → *My Access Tokens*.

## Setup: run locally (optional — hosted link is the primary deliverable)

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
# edit secrets.toml with your GROQ_API_KEY and MONDAY_API_TOKEN
streamlit run app.py
```

## Deploy to Streamlit Community Cloud

1. Push this folder to a GitHub repo.
2. Go to https://share.streamlit.io → "New app" → select the repo/branch,
   set main file to `app.py`.
3. In the app's *Settings → Secrets*, paste:
   ```toml
   GROQ_API_KEY = "gsk_..."
   MONDAY_API_TOKEN = "eyJhbGci..."
   ```
4. Deploy. The app is also usable without pre-set secrets — it'll prompt
   for keys in the sidebar (useful for a grader who wants to use their own
   monday.com boards).

## Known limitations (see Decision Log for full list)

- Board/column name matching is fuzzy but not infallible — very unusual
  naming may need the exact name.
- Cross-board reasoning (e.g. correlating work order delays with deal
  value) is done by the model reasoning over two separate tool calls,
  not a dedicated join tool.
- In-memory cache is per-session (Streamlit `session_state`-scoped
  clearing via sidebar button), not time-based auto-invalidation.
