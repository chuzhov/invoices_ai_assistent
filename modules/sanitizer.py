"""
sanitizer.py – SQL safety layer: only DQL (SELECT) statements are allowed.
"""

from __future__ import annotations

import re

# Statements that mutate state – never allowed
_FORBIDDEN_KEYWORDS = re.compile(
    r"""
    \b(
        INSERT | UPDATE | DELETE | DROP   | CREATE | ALTER |
        TRUNCATE | REPLACE | MERGE | UPSERT |
        GRANT  | REVOKE | EXECUTE | EXEC  |
        CALL   | DO     | COPY   | VACUUM |
        ANALYZE| COMMENT| LOCK   | SET    |
        BEGIN  | COMMIT | ROLLBACK | SAVEPOINT |
        DECLARE| FETCH  | MOVE   | CLOSE  | OPEN
    )\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Only these DQL-leading keywords are accepted
_ALLOWED_STARTS = re.compile(
    r"^\s*(SELECT|WITH|EXPLAIN|TABLE|VALUES)\b",
    re.IGNORECASE,
)

# Detect semicolons that could chain multiple statements
_MULTI_STATEMENT = re.compile(r";.+", re.DOTALL)


class SQLSanitizationError(ValueError):
    """Raised when a query fails safety validation."""


def sanitize(sql: str) -> str:
    """
    Validate *sql* and return it unchanged if it is a safe DQL statement.

    Rules
    -----
    1. Must start with SELECT / WITH / EXPLAIN / TABLE / VALUES.
    2. Must not contain any DML/DDL/DCL keywords.
    3. Must not contain a second statement after a semicolon.

    Raises
    ------
    SQLSanitizationError  if any rule is violated.
    """
    if not sql or not sql.strip():
        raise SQLSanitizationError("Empty SQL query.")

    # Strip trailing semicolon (harmless) then check for chained statements
    cleaned = sql.strip().rstrip(";")
    if _MULTI_STATEMENT.search(cleaned):
        raise SQLSanitizationError(
            "Multiple statements are not allowed. "
            "Only a single DQL statement is permitted."
        )

    if not _ALLOWED_STARTS.match(cleaned):
        raise SQLSanitizationError(
            f"Query must begin with SELECT, WITH, EXPLAIN, TABLE, or VALUES. "
            f"Got: {cleaned[:60]!r}"
        )

    match = _FORBIDDEN_KEYWORDS.search(cleaned)
    if match:
        raise SQLSanitizationError(
            f"Forbidden keyword detected: {match.group(0).upper()!r}. "
            "Only read-only (DQL) statements are allowed."
        )

    return cleaned