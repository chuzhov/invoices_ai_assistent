"""
db.py – Database connectivity and schema introspection.
"""

import os
import textwrap
from typing import Any

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _get_connection() -> psycopg2.extensions.connection:
    """Create and return a new database connection."""
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.environ.get("DB_PORT", 5432)),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.environ.get("DB_SSLMODE", "require"),
    )


# ---------------------------------------------------------------------------
# Schema introspection
# ---------------------------------------------------------------------------

def get_schema_description() -> str:
    """
    Introspect every user table and return a rich, LLM-friendly description
    of the schema, including column names, types, nullability, constraints,
    foreign keys, and row counts.
    """
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # ── list user tables ──────────────────────────────────────────
            cur.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type   = 'BASE TABLE'
                ORDER BY table_name;
                """
            )
            tables = [row["table_name"] for row in cur.fetchall()]

            lines: list[str] = [
                "DATABASE SCHEMA – vendor invoice management system",
                "=" * 60,
            ]

            for table in tables:
                # ── columns ───────────────────────────────────────────────
                cur.execute(
                    """
                    SELECT
                        c.column_name,
                        c.data_type,
                        c.character_maximum_length,
                        c.numeric_precision,
                        c.numeric_scale,
                        c.is_nullable,
                        c.column_default,
                        pgd.description AS column_comment
                    FROM information_schema.columns c
                    LEFT JOIN pg_catalog.pg_statio_all_tables st
                           ON st.schemaname = c.table_schema
                          AND st.relname    = c.table_name
                    LEFT JOIN pg_catalog.pg_description pgd
                           ON pgd.objoid    = st.relid
                          AND pgd.objsubid  = c.ordinal_position
                    WHERE c.table_schema = 'public'
                      AND c.table_name   = %s
                    ORDER BY c.ordinal_position;
                    """,
                    (table,),
                )
                columns = cur.fetchall()

                # ── primary keys ──────────────────────────────────────────
                cur.execute(
                    """
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema    = 'public'
                      AND tc.table_name      = %s
                    ORDER BY kcu.ordinal_position;
                    """,
                    (table,),
                )
                pks = {row["column_name"] for row in cur.fetchall()}

                # ── foreign keys ──────────────────────────────────────────
                cur.execute(
                    """
                    SELECT
                        kcu.column_name,
                        ccu.table_name  AS foreign_table,
                        ccu.column_name AS foreign_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema    = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                      AND tc.table_schema    = 'public'
                      AND tc.table_name      = %s;
                    """,
                    (table,),
                )
                fks = {row["column_name"]: row for row in cur.fetchall()}

                # ── unique constraints ────────────────────────────────────
                cur.execute(
                    """
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema    = kcu.table_schema
                    WHERE tc.constraint_type = 'UNIQUE'
                      AND tc.table_schema    = 'public'
                      AND tc.table_name      = %s;
                    """,
                    (table,),
                )
                uniques = {row["column_name"] for row in cur.fetchall()}

                # ── row count ─────────────────────────────────────────────
                cur.execute(f'SELECT COUNT(*) AS n FROM "{table}";')
                row_count = cur.fetchone()["n"] # type: ignore

                # ── sample rows (up to 3) ─────────────────────────────────
                cur.execute(f'SELECT * FROM "{table}" LIMIT 3;')
                samples = cur.fetchall()

                # ── format table block ────────────────────────────────────
                lines.append(f"\nTABLE: {table}  ({row_count:,} rows)")
                lines.append("-" * 40)
                for col in columns:
                    name = col["column_name"]
                    dtype = col["data_type"]
                    if col["character_maximum_length"]:
                        dtype += f"({col['character_maximum_length']})"
                    elif col["numeric_precision"] and col["data_type"] in (
                        "numeric", "decimal"
                    ):
                        dtype += f"({col['numeric_precision']},{col['numeric_scale']})"

                    tags: list[str] = []
                    if name in pks:
                        tags.append("PK")
                    if name in uniques:
                        tags.append("UNIQUE")
                    if col["is_nullable"] == "NO":
                        tags.append("NOT NULL")
                    if name in fks:
                        fk = fks[name]
                        tags.append(
                            f"FK → {fk['foreign_table']}.{fk['foreign_column']}"
                        )
                    if col["column_default"]:
                        tags.append(f"DEFAULT {col['column_default']}")
                    if col["column_comment"]:
                        tags.append(f'"{col["column_comment"]}"')

                    tag_str = f"  [{', '.join(tags)}]" if tags else ""
                    lines.append(f"  {name:30s} {dtype}{tag_str}")

                # sample data
                if samples:
                    lines.append("  Sample rows:")
                    for s in samples:
                        lines.append(f"    {dict(s)}")

            lines.append("\n" + "=" * 60)
            return "\n".join(lines)

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------

def execute_query(sql: str) -> list[dict[str, Any]]:
    """
    Execute a pre-validated DQL statement and return results as a list of
    plain dicts (JSON-serialisable).
    """
    conn = _get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            return [dict(row) for row in rows]
    finally:
        conn.close()