# SPDX-License-Identifier: Apache-2.0
"""``std.sql_query`` -- read-only SQL over a local database file.

Two engines behind one tool:

* ``sqlite`` (default) -- stdlib :mod:`sqlite3`, opened with
  ``file:...?mode=ro`` so writes fail at the driver, not by convention.
* ``duckdb`` -- lazy import behind the ``stargraph[tools]`` extra, opened
  ``read_only=True``.

The database path resolves through the same filesystem jail as the
``std.file_*`` tools (``STARGRAPH_TOOLS_FS_ROOT``). Rows come back as
JSON-safe dicts (non-primitive values are stringified) and are capped at
``max_rows`` with a ``truncated`` flag.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal, cast

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects
from stargraph.tools.std._jail import resolve_jailed

__all__ = ["sql_query"]

_MAX_ROWS = 10_000


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _rows_to_dicts(columns: list[str], rows: list[Any]) -> list[dict[str, Any]]:
    return [{col: _coerce(val) for col, val in zip(columns, row, strict=True)} for row in rows]


def _query_sqlite(path: str, query: str, params: list[Any], limit: int) -> dict[str, Any]:
    db_path = resolve_jailed(path)
    if not db_path.is_file():
        raise StargraphRuntimeError(f"database {path!r} does not exist", database=path)
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise StargraphRuntimeError(f"sqlite open failed: {exc}", database=path) from exc
    try:
        cur = conn.execute(query, tuple(params))
        fetched = cur.fetchmany(limit + 1)
        columns = [d[0] for d in cur.description or []]
    except sqlite3.Error as exc:
        raise StargraphRuntimeError(f"sqlite query failed: {exc}", database=path) from exc
    finally:
        conn.close()
    truncated = len(fetched) > limit
    rows = _rows_to_dicts(columns, list(fetched[:limit]))
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}


def _query_duckdb(path: str, query: str, params: list[Any], limit: int) -> dict[str, Any]:
    try:
        import duckdb
    except ImportError as exc:
        raise StargraphRuntimeError(
            "std.sql_query engine 'duckdb' requires the duckdb package; "
            "install it with: pip install stargraph[tools]",
            database=path,
        ) from exc
    db_path = resolve_jailed(path)
    if not db_path.is_file():
        raise StargraphRuntimeError(f"database {path!r} does not exist", database=path)
    conn: Any = None
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
        cur: Any = conn.execute(query, params)
        raw_desc = cast("list[Any]", cur.description or [])
        columns: list[str] = [str(d[0]) for d in raw_desc]
        fetched: list[Any] = cur.fetchmany(limit + 1)
    except duckdb.Error as exc:
        raise StargraphRuntimeError(f"duckdb query failed: {exc}", database=path) from exc
    finally:
        if conn is not None:
            conn.close()
    truncated = len(fetched) > limit
    rows = _rows_to_dicts(columns, fetched[:limit])
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated}


@tool(
    name="sql_query",
    namespace="std",
    version="1",
    side_effects=SideEffects.read,
    description=(
        "Run a read-only SQL query against a local sqlite (default) or duckdb "
        "database file inside the tools filesystem jail. Supports positional "
        "query parameters; rows return as JSON dicts, capped at max_rows."
    ),
)
def sql_query(
    database: str,
    query: str,
    params: list[Any] | None = None,
    engine: Literal["sqlite", "duckdb"] = "sqlite",
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Query ``database``; return ``{columns, rows, row_count, truncated}``."""
    limit = max(1, min(max_rows, _MAX_ROWS))
    bound = params or []
    if engine == "sqlite":
        return _query_sqlite(database, query, bound, limit)
    return _query_duckdb(database, query, bound, limit)
