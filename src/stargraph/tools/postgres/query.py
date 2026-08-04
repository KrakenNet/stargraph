# SPDX-License-Identifier: Apache-2.0
"""``postgres.query`` -- read-only SQL (server-enforced)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from stargraph.tools.decorator import tool
from stargraph.tools.postgres import _conn
from stargraph.tools.spec import SideEffects

__all__ = ["pg_query"]

_MAX_ROWS = 10_000


def _coerce(value: Any) -> Any:
    """JSON-safe cell: primitives pass, everything else stringifies."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@tool(
    name="query",
    namespace="postgres",
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:postgres:read",
    description=(
        "Run a read-only SQL query against STARGRAPH_POSTGRES_DSN (the "
        "session forces default_transaction_read_only=on, so writes fail "
        "server-side). Needs stargraph[tools-saas]."
    ),
)
async def pg_query(
    query: str,
    params: list[Any] | None = None,
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Return ``{columns, rows, row_count, truncated}`` (same shape as std.sql_query)."""
    cap = max(1, min(max_rows, _MAX_ROWS))
    args: list[Any] = params or []

    def _run() -> dict[str, Any]:
        with _conn.connect(read_only=True, application_name="stargraph:query") as conn:
            cur = conn.execute(query, args)
            desc = cast("list[Any]", cur.description or [])
            # psycopg columns expose .name; DB-API tuples index 0.
            columns = [
                str(cast("Any", col[0] if isinstance(col, tuple) else col.name)) for col in desc
            ]
            fetched = cast("list[Any]", cur.fetchmany(cap + 1))
            rows = [[_coerce(cell) for cell in row] for row in fetched[:cap]]
            return {
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": len(fetched) > cap,
            }

    return await asyncio.to_thread(_run)
