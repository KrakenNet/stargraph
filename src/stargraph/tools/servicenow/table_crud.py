# SPDX-License-Identifier: Apache-2.0
"""``servicenow.table_query`` and ``servicenow.table_create`` -- generic SN table CRUD."""

from __future__ import annotations

import os
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

from ._auth import client_kwargs, resolve_base_url

_NAMESPACE = "servicenow"


def _live_enabled() -> bool:
    return os.environ.get("STARGRAPH_SERVICENOW_LIVE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@tool(
    name="table_query",
    namespace=_NAMESPACE,
    version="1",
    side_effects=SideEffects.read,
    requires_capability="tools:servicenow:read",
    description="Query a ServiceNow table via GET /api/now/table/{table_name}.",
)
async def table_query(
    *,
    table_name: str,
    query: str = "",
    fields: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    if not _live_enabled():
        return {
            "status": "dry-run",
            "table_name": table_name,
            "results": [],
            "__stargraph_provenance__": {
                "origin": "tool",
                "source": "servicenow",
                "external_id": f"dry-run:query:{table_name}",
            },
        }

    import httpx

    ck, headers = client_kwargs()
    base_url = resolve_base_url()
    url = f"{base_url}/api/now/table/{table_name}"
    params: dict[str, str] = {"sysparm_limit": str(limit)}
    if query:
        params["sysparm_query"] = query
    if fields:
        params["sysparm_fields"] = fields

    try:
        async with httpx.AsyncClient(timeout=15.0, **ck) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json() or {}
            results: list[dict[str, Any]] = payload.get("result", [])
            return {
                "status": "ok",
                "table_name": table_name,
                "results": results,
                "__stargraph_provenance__": {
                    "origin": "tool",
                    "source": "servicenow",
                    "external_id": f"query:{table_name}",
                },
            }
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "results": []}


@tool(
    name="table_create",
    namespace=_NAMESPACE,
    version="1",
    side_effects=SideEffects.write,
    requires_capability="tools:servicenow:write",
    description="Create a record in a ServiceNow table via POST /api/now/table/{table_name}.",
)
async def table_create(
    *,
    table_name: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    if not _live_enabled():
        return {
            "status": "dry-run",
            "table_name": table_name,
            "request_body": body,
            "sys_id": "",
            "__stargraph_provenance__": {
                "origin": "tool",
                "source": "servicenow",
                "external_id": f"dry-run:create:{table_name}",
            },
        }

    import httpx

    ck, headers = client_kwargs()
    base_url = resolve_base_url()
    headers["Content-Type"] = "application/json"
    url = f"{base_url}/api/now/table/{table_name}"

    try:
        async with httpx.AsyncClient(timeout=15.0, **ck) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json() or {}
            result: dict[str, Any] = payload.get("result", {})
            return {
                "status": "ok",
                "table_name": table_name,
                "sys_id": str(result.get("sys_id", "")),
                "result": result,
                "__stargraph_provenance__": {
                    "origin": "tool",
                    "source": "servicenow",
                    "external_id": str(result.get("sys_id", "")),
                },
            }
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "sys_id": ""}
