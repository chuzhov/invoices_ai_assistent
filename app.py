"""
app.py – Streamlit UI for the Invoice Analyzer AI assistant.

Run with:
    streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Invoice Analyzer AI",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

from modules.db import get_schema_description          # noqa: E402
from modules.agent import Agent                        # noqa: E402
from modules.logger import SessionLogger               # noqa: E402

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@300;400;600&display=swap');

    html, body, [class*="css"] {
        font-family: 'IBM Plex Sans', sans-serif;
    }

    /* ── theme-adaptive CSS variables ───────────────────
       Streamlit exposes --background-color and --text-color on :root,
       so color-mix() lets us derive tints that work in both themes.   */
    :root {
        --ia-msg-bg:      color-mix(in srgb, var(--background-color, #fff) 93%, var(--text-color, #000) 7%);
        --ia-msg-border:  color-mix(in srgb, var(--background-color, #fff) 78%, var(--text-color, #000) 22%);
        --ia-sql-bg:      color-mix(in srgb, var(--background-color, #fff) 96%, var(--text-color, #000) 4%);
        --ia-sql-border:  color-mix(in srgb, var(--background-color, #fff) 74%, var(--text-color, #000) 26%);
        --ia-sql-color:   #1a6fbd;
        --ia-badge-color: color-mix(in srgb, var(--text-color, #000) 45%, transparent 55%);
        --ia-accent:      #4a90e2;
    }

    /* In dark theme Streamlit sets data-theme="dark" on <html> */
    [data-theme="dark"] {
        --ia-sql-color: #79c0ff;
    }

    .block-container { padding-top: 1.5rem; }

    /* ── chat messages ───────────────────────────────── */
    [data-testid="stChatMessage"] {
        background: var(--ia-msg-bg);
        border: 1px solid var(--ia-msg-border);
        border-radius: 10px;
        margin-bottom: 0.6rem;
        padding: 0.8rem 1rem;
    }

    /* ── user bubble accent ──────────────────────────── */
    [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
        border-left: 3px solid var(--ia-accent);
    }

    /* ── SQL expander ────────────────────────────────── */
    .sql-block {
        font-family: 'IBM Plex Mono', monospace;
        font-size: 0.82rem;
        background: var(--ia-sql-bg);
        color: var(--ia-sql-color);
        border: 1px solid var(--ia-sql-border);
        border-radius: 6px;
        padding: 0.7rem 1rem;
        overflow-x: auto;
        white-space: pre-wrap;
    }

    /* ── token badge ─────────────────────────────────── */
    .token-badge {
        font-size: 0.7rem;
        color: var(--ia-badge-color);
        font-family: 'IBM Plex Mono', monospace;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Session-state initialisation
# ---------------------------------------------------------------------------

def _init_session() -> None:
    """Bootstrap agent, logger, and message history on first load."""
    if "agent" not in st.session_state:
        with st.spinner("🔗 Connecting to database and loading schema…"):
            try:
                schema = get_schema_description()
            except Exception as exc:
                st.error(f"❌ Could not connect to database:\n\n```\n{exc}\n```")
                st.stop()

        logger = SessionLogger()
        agent = Agent(logger=logger, schema=schema)

        st.session_state.agent = agent
        st.session_state.logger = logger
        st.session_state.schema = schema
        st.session_state.messages: list[dict] = []  # display-only history
        st.session_state.sql_log: list[str] = []    # SQLs executed this session


_init_session()


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown("## 🧾 Invoice Analyzer")
    st.markdown("---")

    st.markdown("### Session info")
    log_path: Path = st.session_state.logger.path
    st.markdown(f"**Log file:**\n`{log_path.name}`")
    st.markdown(f"**Messages:** {len(st.session_state.messages)}")

    if st.session_state.sql_log:
        st.markdown("---")
        st.markdown("### SQL queries this session")
        for i, sql in enumerate(st.session_state.sql_log, 1):
            with st.expander(f"Query {i}"):
                st.markdown(f'<div class="sql-block">{sql}</div>', unsafe_allow_html=True)

    st.markdown("---")
    with st.expander("📐 DB Schema", expanded=False):
        st.code(st.session_state.schema, language="text")

    if st.button("🗑️ Clear chat", use_container_width=True):
        # Re-init without reloading schema
        schema = st.session_state.schema
        logger = SessionLogger()
        agent = Agent(logger=logger, schema=schema)
        st.session_state.agent = agent
        st.session_state.logger = logger
        st.session_state.messages = []
        st.session_state.sql_log = []
        st.rerun()


# ---------------------------------------------------------------------------
# Main chat area
# ---------------------------------------------------------------------------

st.markdown("# 🧾 Invoice Analyzer AI")
st.markdown(
    "<span style='color:#58657e'>Powered by GPT-4o · Connected to Neon PostgreSQL</span>",
    unsafe_allow_html=True,
)
st.markdown("---")

# ── render existing messages ───────────────────────────────────────────────
for entry in st.session_state.messages:
    role = entry["role"]
    with st.chat_message(role):
        st.markdown(entry["content"])
        if entry.get("sql"):
            with st.expander("🔍 SQL executed"):
                st.markdown(
                    f'<div class="sql-block">{entry["sql"]}</div>',
                    unsafe_allow_html=True,
                )
        if entry.get("tokens"):
            pt = entry.get("prompt_tokens")
            ct = entry.get("completion_tokens")
            tt = entry["tokens"]
            breakdown = f"↑ {pt} prompt · ↓ {ct} completion · " if pt else ""
            st.markdown(
                f'<span class="token-badge">{breakdown}{tt} total tokens</span>',
                unsafe_allow_html=True,
            )


# ── example prompts (only when chat is empty) ──────────────────────────────
if not st.session_state.messages:
    st.markdown("#### 💡 Try asking:")
    examples = [
        "Show me the top 10 vendors by total invoice amount",
        "How many overdue invoices do we have, and what is the total amount?",
        "What is the average payment cycle time per vendor?",
        "List all unpaid invoices older than 30 days",
        "Show monthly invoice totals for the current year",
    ]
    cols = st.columns(len(examples))
    for col, example in zip(cols, examples):
        if col.button(example, use_container_width=True):
            st.session_state._prefill = example
            st.rerun()


# ── chat input ─────────────────────────────────────────────────────────────
prefill = st.session_state.pop("_prefill", None)
prompt = st.chat_input("Ask anything about your invoices…") or prefill

if prompt:
    # Show user message immediately
    with st.chat_message("user"):
        st.markdown(prompt)

    st.session_state.messages.append({"role": "user", "content": prompt})

    # Call the agent
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                answer = st.session_state.agent.chat(prompt)
            except Exception as exc:
                answer = f"⚠️ An error occurred:\n\n```\n{exc}\n```"

        st.markdown(answer)

        # Peek at the latest logged SQL from the agent's tool calls
        last_sql: str | None = None
        log_file = st.session_state.logger.path
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            for raw in reversed(lines):
                try:
                    rec = json.loads(raw)
                    if rec.get("sql"):
                        last_sql = rec["sql"]
                        break
                except Exception:
                    pass

        if last_sql and last_sql not in st.session_state.sql_log:
            st.session_state.sql_log.append(last_sql)
            with st.expander("🔍 SQL executed"):
                st.markdown(
                    f'<div class="sql-block">{last_sql}</div>',
                    unsafe_allow_html=True,
                )

        # Token count from the last assistant log entry
        tokens = 0
        rec = {}
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8").strip().splitlines()
            for raw in reversed(lines):
                try:
                    rec = json.loads(raw)
                    if rec.get("role") == "assistant":
                        tokens = rec.get("total_tokens") or 0
                        break
                except Exception:
                    pass

        if tokens:
            st.markdown(
                f'<span class="token-badge">↑ {rec.get("prompt_tokens", "?")} prompt · ↓ {rec.get("completion_tokens", "?")} completion · {tokens} total</span>',
                unsafe_allow_html=True,
            )

    st.session_state.messages.append(
        {
            "role": "assistant",
            "content": answer,
            "sql": last_sql,
            "prompt_tokens": rec.get("prompt_tokens") if 'rec' in dir() else None,
            "completion_tokens": rec.get("completion_tokens") if 'rec' in dir() else None,
            "tokens": tokens,
        }
    )
    st.rerun()