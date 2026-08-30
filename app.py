"""
app.py
------
Streamlit chat frontend for the Skylark Drones monday.com BI agent.
Deployable as-is to Streamlit Community Cloud — no local setup needed
to test it once deployed.

Secrets required (set in Streamlit Cloud's "Secrets" panel, or locally
in .streamlit/secrets.toml — see .streamlit/secrets.toml.example):
    GROQ_API_KEY     = "gsk_..."
    MONDAY_API_TOKEN = "eyJhbGciOi..."
"""

import streamlit as st

from agent import run_agent_turn
from monday_client import MondayClient, MondayAPIError
from tools import clear_cache

st.set_page_config(page_title="Skylark Drones BI Agent", page_icon="🛰️", layout="centered")

st.title("🛰️ Skylark Drones — BI Agent")
st.caption(
    "Ask founder-level questions about the Work Orders and Deals boards on monday.com. "
    "Answers are computed live from monday.com — nothing here is hardcoded."
)

# ------------------------------------------------------------------ #
# Secrets / connection setup
# ------------------------------------------------------------------ #
def get_secret(key: str) -> str:
    # Prefer Streamlit secrets (cloud deploy); fall back to sidebar input for
    # quick local testing without a secrets.toml file.
    if key in st.secrets:
        return st.secrets[key]
    return st.session_state.get(f"manual_{key}", "")


with st.sidebar:
    st.subheader("Connection")
    if "GROQ_API_KEY" not in st.secrets:
        st.session_state["manual_GROQ_API_KEY"] = st.text_input(
            "Groq API key", type="password", value=st.session_state.get("manual_GROQ_API_KEY", "")
        )
    else:
        st.success("Groq API key loaded from secrets ✅")

    if "MONDAY_API_TOKEN" not in st.secrets:
        st.session_state["manual_MONDAY_API_TOKEN"] = st.text_input(
            "monday.com API token", type="password", value=st.session_state.get("manual_MONDAY_API_TOKEN", "")
        )
    else:
        st.success("monday.com token loaded from secrets ✅")

    st.divider()
    if st.button("🔄 Refresh data cache"):
        clear_cache()
        st.success("Cache cleared — next query re-fetches from monday.com.")

    st.divider()
    if st.button("🔌 Test monday.com connection"):
        token = get_secret("MONDAY_API_TOKEN")
        if not token:
            st.error("Enter a monday.com API token first.")
        else:
            try:
                mc = MondayClient(api_token=token)
                boards = mc.list_boards()
                st.success(f"Connected. Found {len(boards)} board(s):")
                for b in boards:
                    st.write(f"• {b['name']}")
            except MondayAPIError as e:
                st.error(f"Connection failed: {e}")

groq_key = get_secret("GROQ_API_KEY")
monday_token = get_secret("MONDAY_API_TOKEN")

if not groq_key or not monday_token:
    st.info("👈 Add your Groq API key and monday.com API token in the sidebar to start.")
    st.stop()

# ------------------------------------------------------------------ #
# Chat state
# ------------------------------------------------------------------ #
if "messages" not in st.session_state:
    st.session_state.messages = []  # display history: [{"role", "content"}]
if "agent_history" not in st.session_state:
    st.session_state.agent_history = []  # Anthropic-format history (includes tool blocks)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

example_qs = [
    "How's our pipeline looking for the energy sector this quarter?",
    "Which work orders are overdue or missing a completion date?",
    "What's our total deal value by stage?",
]
if not st.session_state.messages:
    st.write("Try asking:")
    for q in example_qs:
        st.write(f"- _{q}_")

prompt = st.chat_input("Ask a business question...")
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    st.session_state.agent_history.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Querying monday.com and analyzing..."):
            try:
                answer = run_agent_turn(
                    client_secrets={
                        "groq_api_key": groq_key,
                        "monday_api_token": monday_token,
                    },
                    conversation_history=st.session_state.agent_history,
                )
            except Exception as e:  # noqa: BLE001
                answer = (
                    f"Something went wrong talking to monday.com or the model: `{e}`. "
                    "Try 'Refresh data cache' in the sidebar, or check your API token."
                )
        st.markdown(answer)

    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.session_state.agent_history.append({"role": "assistant", "content": answer})
