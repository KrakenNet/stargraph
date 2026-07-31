# SPDX-License-Identifier: Apache-2.0
"""``postgres.execute`` -- gated mutating SQL."""

from __future__ import annotations

import asyncio
from typing import Any

from stargraph.tools import _saas
from stargraph.tools.decorator import tool
from stargraph.tools.postgres import _conn
from stargraph.tools.spec import SideEffects

__all__ = ["pg_execute"]

_NAMESPACE = "postgres"


@tool(
    name="execute",
    namespace=_NAMESPACE,
    version="1",
    side_effects=SideEffects.write,
    requires_capability="tools:postgres:write",
    description=(
        "Execute a mutating SQL statement against STARGRAPH_POSTGRES_DSN. "
        "Dry-run by default; set STARGRAPH_POSTGRES_LIVE=1 to commit. Needs "
        "a caller-supplied dedupe_key (stamped into application_name for "
        "attribution -- PostgreSQL has no server-side dedupe, so make the "
        "statement itself idempotent, e.g. ON CONFLICT DO NOTHING)."
    ),
)
async def pg_execute(
    statement: str,
    dedupe_key: str,
    params: list[Any] | None = None,
) -> dict[str, Any]:
    """Execute + commit ``statement``; returns ``{status, rowcount}``."""
    key = _saas.require_dedupe_key(dedupe_key, "postgres.execute")
    if not _saas.live_enabled(_NAMESPACE):
        return _saas.dry_run_envelope(
            _NAMESPACE, key, {"statement": statement, "params": params or []}
        )

    def _run() -> dict[str, Any]:
        app_name = f"stargraph:{key}"
        with _conn.connect(read_only=False, application_name=app_name) as conn:
            cur = conn.execute(statement, params or [])
            rowcount = int(cur.rowcount)
            conn.commit()
        return {
            "status": "ok",
            "rowcount": rowcount,
            "__stargraph_provenance__": {
                "origin": "tool",
                "source": _NAMESPACE,
                "external_id": key,
            },
        }

    return await asyncio.to_thread(_run)
