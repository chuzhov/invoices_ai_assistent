# Invoice Analyzer AI

A Streamlit-powered chat application that lets you query your vendor invoice PostgreSQL database (Neon) using natural language, backed by Azure OpenAI GPT-4o with tool-calling.

---

## Project structure

```
invoice_analyzer/
├── app.py                  # Streamlit entry point
├── requirements.txt
├── .env.example            # copy → .env and fill in credentials
├── log/                    # auto-created; one JSONL file per session
└── modules/
    ├── __init__.py
    ├── agent.py            # Azure OpenAI agent + tool-call loop
    ├── db.py               # DB connection, schema introspection, query execution
    ├── logger.py           # Token-aware JSONL chat logger
    └── sanitizer.py        # SQL safety layer (DQL-only)
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env and fill in all values
```

**.env fields:**

| Variable | Description |
|---|---|
| `DB_HOST` | Neon PostgreSQL host (e.g. `ep-xxx.us-east-2.aws.neon.tech`) |
| `DB_PORT` | Port (default `5432`) |
| `DB_NAME` | Database name |
| `DB_USER` | Database user |
| `DB_PASSWORD` | Database password |
| `DB_SSLMODE` | SSL mode (default `require`) |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |

### 3. Run

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

---

## How it works

```
User message
     │
     ▼
  Agent.chat()
     │
     ├─► Azure OpenAI GPT-4o  ──tool_call──►  run_sql_query(query=…)
     │                                                │
     │                                        sanitizer.sanitize()
     │                                                │ (DQL only)
     │                                        db.execute_query()
     │                                                │
     │          ◄──── tool result (rows JSON) ────────┘
     │
     └─► Final natural-language answer
```

1. On startup, `db.get_schema_description()` introspects all public tables (columns, types, constraints, FK relationships, row counts, sample rows) and injects this as the system prompt.
2. The user sends a question in plain English.
3. GPT-4o decides whether it needs data; if so it emits a `run_sql_query` tool call.
4. `sanitizer.sanitize()` validates that the SQL is DQL-only (SELECT/WITH/EXPLAIN). Forbidden keywords (INSERT, UPDATE, DELETE, DROP, …) are rejected.
5. `db.execute_query()` runs the clean SQL on Neon and returns rows as a list of dicts.
6. The model receives the rows and composes a human-readable answer.
7. Every message (system, user, assistant, tool result) is appended to a JSONL log file under `log/` with its token count.

---

## Chat log format

Each session creates `log/<YYYYMMDDTHHMMSSZ>_session.jsonl`.

Every line is a JSON record:

```json
{
  "seq":     3,
  "ts":      "2025-06-01T14:22:11.123456+00:00",
  "role":    "tool",
  "content": "{\"rows\": [...], \"row_count\": 10, \"_sql_executed\": \"SELECT ...\"}",
  "tokens":  84,
  "sql":     "SELECT vendor_name, SUM(amount) FROM invoices GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
  "extra":   {"tool_call_id": "call_abc123", "error": null}
}
```

## SQL safety rules

The sanitizer (`modules/sanitizer.py`) enforces:

1. Query must **start with** `SELECT`, `WITH`, `EXPLAIN`, `TABLE`, or `VALUES`.
2. Query must **not contain** any of: `INSERT UPDATE DELETE DROP CREATE ALTER TRUNCATE REPLACE MERGE UPSERT GRANT REVOKE EXECUTE EXEC CALL DO COPY VACUUM ANALYZE COMMENT LOCK SET BEGIN COMMIT ROLLBACK SAVEPOINT DECLARE FETCH MOVE CLOSE OPEN`.
3. **No multi-statement** execution (no second statement after a semicolon).

---



---

## Extending the system prompt

Edit `agent.py → _SYSTEM_TEMPLATE` to add:
- Business rules (e.g., approval thresholds)
- Naming conventions used in your data
- Common queries the model should prefer
- Currency / locale preferences