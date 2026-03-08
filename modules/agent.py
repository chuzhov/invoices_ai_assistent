"""
agent.py – Azure OpenAI agent with tool-use for database queries.

The agent is given:
  • A system prompt that describes the DB schema in detail.
  • A single tool: ``run_sql_query`` that it can call to read data.

The caller drives the conversation by repeatedly calling :meth:`Agent.chat`.
"""

import json
import os
from typing import Any

from openai import AzureOpenAI
from dotenv import load_dotenv

from modules.db import execute_query
from modules.sanitizer import SQLSanitizationError, sanitize
from modules.logger import SessionLogger

load_dotenv()

DEPLOYMENT_MODEL = "gpt-4o"

# ---------------------------------------------------------------------------
# Tool definition (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "run_sql_query",
            "description": (
                "Execute a read-only SQL SELECT statement against the vendor "
                "invoice PostgreSQL database and return the results as a JSON "
                "array of objects. Use this whenever you need actual data to "
                "answer the user's question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "A valid PostgreSQL SELECT (DQL) statement. "
                            "Do NOT include any DML/DDL keywords such as "
                            "INSERT, UPDATE, DELETE, DROP, etc."
                        ),
                    }
                },
                "required": ["query"],
            },
        },
    }
]


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are an expert data analyst assistant specialised in vendor invoice management.
You have direct access to a PostgreSQL database through the `run_sql_query` tool.

## Your responsibilities
1. Answer the user's questions about vendor invoices, payments, and related data.
2. When you need data, call `run_sql_query` with a well-formed SELECT statement.
3. Interpret query results and present insights clearly.
4. If a question is ambiguous, ask a clarifying question before querying.
5. Format monetary values with currency symbols and thousands separators. Always check currency types and never aggregate invoices issued in different currencies
6. Present tabular data as Markdown tables when there are multiple rows/columns.

## Rules
- ONLY use SELECT statements – no INSERT, UPDATE, DELETE, DROP, or DDL.
- Always LIMIT large result sets (e.g. LIMIT 100) unless the user asks for all rows.
- Never expose raw connection strings or credentials.
- When grouping or aggregating, label columns meaningfully (use AS aliases).

## Domain knowledge
- "Vendor" = supplier or service provider who issues invoices.
- "Invoice" = a bill from a vendor requesting payment.
- Common statuses: PAID, UNPAID. Do not summarize invoices with different statuses if it was not requested by the user. If not stated the status is assumed to be UNPAID.
- Overdue invoices: invoice_due_date < CURRENT_DATE AND status != 'PAID'.

## Database schema
{schema}
"""


def build_system_prompt(schema: str) -> str:
    return _SYSTEM_TEMPLATE.format(schema=schema)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Agent:
    """
    Stateful conversational agent that wraps Azure OpenAI with tool-calling.

    Parameters
    ----------
    logger   : SessionLogger instance for this session.
    schema   : Pre-fetched schema description string (avoids repeated DB calls).
    """

    def __init__(self, logger: SessionLogger, schema: str) -> None:
        self._client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version="2025-04-01-preview",
            azure_endpoint="https://ai-proxy.lab.epam.com",
        )
        self._logger = logger
        self._history: list[dict[str, Any]] = []

        system_prompt = build_system_prompt(schema)
        system_msg = {"role": "system", "content": system_prompt}
        self._history.append(system_msg)
        self._logger.log("system", system_prompt)

    # ------------------------------------------------------------------ #
    # Public                                                             #
    # ------------------------------------------------------------------ #

    def chat(self, user_message: str) -> str:
        """
        Send *user_message*, process any tool calls, and return the final
        assistant response as a plain string.
        """
        user_msg = {"role": "user", "content": user_message}
        self._history.append(user_msg)
        self._logger.log("user", user_message)

        # Agentic loop: keep going while the model wants to call tools
        while True:
            response = self._client.chat.completions.create(
                model=DEPLOYMENT_MODEL,
                messages=self._history, # type: ignore
                tools=_TOOLS, # type: ignore
                tool_choice="auto",
            )

            choice = response.choices[0]
            assistant_msg = choice.message

            # Convert to plain dict for history storage
            msg_dict = self._to_dict(assistant_msg)
            self._history.append(msg_dict)
            # Log with exact token counts straight from the API response
            self._logger.log_assistant_response(msg_dict, response.usage)

            # ── no tool call → we have the final answer ────────────────
            if choice.finish_reason != "tool_calls" or not assistant_msg.tool_calls:
                return assistant_msg.content or ""

            # ── process each tool call ─────────────────────────────────
            for tc in assistant_msg.tool_calls:
                tool_result = self._handle_tool_call(tc)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                }
                self._history.append(tool_msg)
                self._logger.log(
                    "tool",
                    tool_msg["content"],
                    sql=tool_result.get("_sql_executed"),
                    extra={"tool_call_id": tc.id, "error": tool_result.get("error")},
                )

    # ------------------------------------------------------------------ #
    # Private                                                            #
    # ------------------------------------------------------------------ #

    def _handle_tool_call(self, tc: Any) -> dict[str, Any]:
        """
        Execute the requested tool and return a result dict.
        On sanitization or execution errors the error message is returned
        instead of raising, so the model can relay it to the user.
        """
        if tc.function.name != "run_sql_query":
            return {"error": f"Unknown tool: {tc.function.name!r}"}

        try:
            args = json.loads(tc.function.arguments)
        except json.JSONDecodeError as exc:
            return {"error": f"Failed to parse tool arguments: {exc}"}

        raw_sql: str = args.get("query", "")

        # ── sanitize ──────────────────────────────────────────────────
        try:
            safe_sql = sanitize(raw_sql)
        except SQLSanitizationError as exc:
            return {
                "error": str(exc),
                "_sql_attempted": raw_sql,
            }

        # ── execute ───────────────────────────────────────────────────
        try:
            rows = execute_query(safe_sql)
            return {
                "rows": rows,
                "row_count": len(rows),
                "_sql_executed": safe_sql,
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "error": f"Database error: {exc}",
                "_sql_executed": safe_sql,
            }

    @staticmethod
    def _to_dict(msg: Any) -> dict[str, Any]:
        """Convert an OpenAI message object to a plain dict."""
        d: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ]
        return d