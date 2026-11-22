"""SQLite helpers for QueryCraft's in-memory, read-only SQL playground."""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Mapping

import pandas as pd


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS_PER_TABLE = 50_000
MAX_RESULT_ROWS = 5_000
QUERY_TIMEOUT_SECONDS = 5
DEFAULT_QUERY = """SELECT
    p.category,
    SUM(o.quantity * p.unit_price) AS revenue,
    COUNT(DISTINCT o.order_id) AS orders
FROM orders AS o
JOIN products AS p ON p.product_id = o.product_id
GROUP BY p.category
ORDER BY revenue DESC;"""


class QueryCraftError(ValueError):
    """Expected user-facing input or SQL error."""


def normalize_identifier(value: object) -> str:
    """Normalize a table or column name to a safe SQLite identifier."""
    cleaned = re.sub(r"[^a-zA-Z0-9_]+", "_", str(value).strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        cleaned = "table"
    if cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    return cleaned[:60]


def unique_name(base: str, existing: set[str]) -> str:
    candidate = normalize_identifier(base)
    suffix = 2
    while candidate in existing:
        candidate = f"{normalize_identifier(base)[:54]}_{suffix}"
        suffix += 1
    return candidate


def normalize_dataframe(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize column labels and keep duplicate labels unique."""
    if frame is None:
        raise QueryCraftError("The uploaded table is empty.")
    result = frame.copy()
    seen: set[str] = set()
    columns = []
    for original in result.columns:
        base = normalize_identifier(original)
        name = unique_name(base, seen)
        seen.add(name)
        columns.append(name)
    if not columns:
        raise QueryCraftError("The uploaded file must contain at least one column.")
    result.columns = columns
    if len(result) > MAX_ROWS_PER_TABLE:
        raise QueryCraftError(
            f"Each table is limited to {MAX_ROWS_PER_TABLE:,} rows. "
            "Please upload a smaller file or filter it before uploading."
        )
    return result


def read_uploaded_file(uploaded) -> tuple[str, pd.DataFrame]:
    """Read a Streamlit uploaded CSV or Excel file; Excel uses its first sheet."""
    name = getattr(uploaded, "name", "uploaded_table.csv")
    size = getattr(uploaded, "size", None)
    if size is not None and size > MAX_FILE_BYTES:
        raise QueryCraftError(f"{name} is larger than the 10 MB upload limit.")
    suffix = Path(name).suffix.lower()
    try:
        if suffix == ".csv":
            frame = pd.read_csv(uploaded)
        elif suffix in {".xlsx", ".xls"}:
            frame = pd.read_excel(uploaded)
        else:
            raise QueryCraftError(f"Unsupported file type for {name}. Use CSV or Excel.")
    except QueryCraftError:
        raise
    except Exception as exc:
        raise QueryCraftError(f"Could not read {name}: {exc}") from exc
    table_base = Path(name).stem
    return normalize_identifier(table_base), normalize_dataframe(frame)


def create_database(tables: Mapping[str, pd.DataFrame]) -> sqlite3.Connection:
    """Create an in-memory SQLite database from named pandas tables."""
    if not tables:
        raise QueryCraftError("Add at least one CSV or Excel table.")
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys = ON")
    existing: set[str] = set()
    total_rows = 0
    try:
        for requested_name, frame in tables.items():
            normalized = normalize_dataframe(frame)
            table_name = unique_name(requested_name, existing)
            existing.add(table_name)
            total_rows += len(normalized)
            if total_rows > 150_000:
                raise QueryCraftError("The combined upload is limited to 150,000 rows.")
            normalized.to_sql(table_name, connection, index=False, if_exists="fail")
        return connection
    except Exception:
        connection.close()
        raise


def table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [row[0] for row in rows]


def get_schema(connection: sqlite3.Connection, table: str) -> pd.DataFrame:
    """Return schema metadata for a known table."""
    if table not in table_names(connection):
        raise QueryCraftError(f"Unknown table: {table}")
    escaped = table.replace('"', '""')
    rows = connection.execute(f'PRAGMA table_info("{escaped}")').fetchall()
    return pd.DataFrame(
        [{"column": row[1], "type": row[2] or "TEXT", "nullable": not bool(row[3]), "primary_key": bool(row[5])}
         for row in rows]
    )


def get_table_preview(connection: sqlite3.Connection, table: str, limit: int = 10) -> pd.DataFrame:
    """Show a small preview from one validated table."""
    if table not in table_names(connection):
        raise QueryCraftError(f"Unknown table: {table}")
    escaped = table.replace('"', '""')
    return pd.read_sql_query(f'SELECT * FROM "{escaped}" LIMIT {int(limit)}', connection)


def _first_keyword(sql: str) -> str:
    """Get the first SQL keyword after leading whitespace and comments."""
    text = sql.lstrip()
    while text:
        if text.startswith("--"):
            newline = text.find("\n")
            text = "" if newline == -1 else text[newline + 1:].lstrip()
        elif text.startswith("/*"):
            end = text.find("*/", 2)
            if end == -1:
                raise QueryCraftError("SQL contains an unfinished block comment.")
            text = text[end + 2:].lstrip()
        else:
            break
    match = re.match(r"([A-Za-z]+)", text)
    return match.group(1).upper() if match else ""


def _readonly_authorizer(action, arg1, arg2, database, trigger):
    """Allow reads and ordinary SELECT expressions; deny every write/admin action."""
    allowed = {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_READ,
        sqlite3.SQLITE_FUNCTION,
    }
    recursive = getattr(sqlite3, "SQLITE_RECURSIVE", None)
    if recursive is not None:
        allowed.add(recursive)
    if action not in allowed:
        return sqlite3.SQLITE_DENY
    function_name = (arg2 or arg1 or "").lower()
    if action == sqlite3.SQLITE_FUNCTION and function_name in {
        "load_extension", "readfile", "writefile"
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def execute_readonly_query(
    connection: sqlite3.Connection,
    sql: str,
    timeout_seconds: int = QUERY_TIMEOUT_SECONDS,
    max_rows: int = MAX_RESULT_ROWS,
) -> tuple[pd.DataFrame, float, bool]:
    """Execute one read-only SELECT/WITH statement with timeout and row cap.

    Returns (dataframe, execution_seconds, truncated).
    """
    if not isinstance(sql, str) or not sql.strip():
        raise QueryCraftError("Enter a SQL query first.")
    if _first_keyword(sql) not in {"SELECT", "WITH"}:
        raise QueryCraftError("Only read-only SELECT or WITH queries are allowed.")

    start = time.perf_counter()
    deadline = start + max(1, timeout_seconds)
    connection.set_authorizer(_readonly_authorizer)
    connection.set_progress_handler(lambda: int(time.perf_counter() > deadline), 1000)
    try:
        cursor = connection.execute(sql)
        if cursor.description is None:
            raise QueryCraftError("The query did not return a result table.")
        columns = [item[0] for item in cursor.description]
        fetched = cursor.fetchmany(max_rows + 1)
        truncated = len(fetched) > max_rows
        result = pd.DataFrame.from_records(fetched[:max_rows], columns=columns)
        elapsed = time.perf_counter() - start
        return result, elapsed, truncated
    except QueryCraftError:
        raise
    except sqlite3.OperationalError as exc:
        message = str(exc)
        if "interrupted" in message.lower():
            raise QueryCraftError(
                f"Query stopped after the {timeout_seconds}-second execution limit."
            ) from exc
        raise QueryCraftError(f"SQL error: {message}") from exc
    except sqlite3.ProgrammingError as exc:
        raise QueryCraftError(f"SQL error: {exc}") from exc
    finally:
        connection.set_progress_handler(None, 0)
        connection.set_authorizer(None)
