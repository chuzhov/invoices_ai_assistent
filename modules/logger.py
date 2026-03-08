"""
logger.py – Persistent chat log writer.

Each session creates one JSONL file in  log/<timestamp>_session.jsonl
Every record is a JSON object:
    {
      "seq":               <int>,       # message index in this session
      "ts":                <ISO-8601>,  # wall-clock timestamp
      "role":              <str>,       # "system" | "user" | "assistant" | "tool"
      "content":           <str>,       # text or JSON-serialised tool payload
      "prompt_tokens":     <int|null>,  # exact value from the API response usage
      "completion_tokens": <int|null>,  # exact value from the API response usage
      "total_tokens":      <int|null>,  # exact value from the API response usage
      "sql":               <str|null>,  # SQL extracted from a tool call, if any
      "extra":             <dict>       # arbitrary metadata
    }

Token counts come directly from the OpenAI response `usage` object and are
therefore exact.  For messages that have no corresponding API call (system
prompt, user turns, tool results) the token fields are null.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).parent.parent / "log"
LOG_DIR.mkdir(parents=True, exist_ok=True)


class SessionLogger:
    """Append-only JSONL writer for one conversation session."""

    def __init__(self) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._path = LOG_DIR / f"{ts}_session.jsonl"
        self._seq = 0

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    @property
    def path(self) -> Path:
        return self._path

    def log(
        self,
        role: str,
        content: str,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        sql: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        Append one record to the JSONL log.

        Parameters
        ----------
        role               : "system" | "user" | "assistant" | "tool"
        content            : The text or serialised payload to log.
        prompt_tokens      : From response.usage.prompt_tokens (API calls only).
        completion_tokens  : From response.usage.completion_tokens (API calls only).
        total_tokens       : From response.usage.total_tokens (API calls only).
        sql                : Optional SQL string extracted from a tool call.
        extra              : Any additional metadata to store alongside the record.
        """
        self._seq += 1
        record = {
            "seq": self._seq,
            "ts": datetime.now(timezone.utc).isoformat(),
            "role": role,
            "content": content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "sql": sql,
            "extra": extra or {},
        }
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def log_assistant_response(
        self,
        message: dict[str, Any],
        usage: Any,  # openai.types.CompletionUsage
    ) -> None:
        """
        Log an assistant message together with the exact token counts
        taken from the API response's ``usage`` object.

        Parameters
        ----------
        message : The assistant message dict (role + content + optional tool_calls).
        usage   : ``response.usage`` from the OpenAI completion response.
        """
        role = message.get("role", "assistant")
        raw_content = message.get("content") or ""

        if isinstance(raw_content, list):
            content_str = json.dumps(raw_content, ensure_ascii=False, default=str)
        else:
            content_str = str(raw_content)

        # Extract SQL from any tool calls present in this message
        sql_found: str | None = None
        for tc in message.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"]["arguments"])
                if "query" in args:
                    sql_found = args["query"]
            except Exception:
                pass

        self.log(
            role,
            content_str,
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            sql=sql_found,
        )