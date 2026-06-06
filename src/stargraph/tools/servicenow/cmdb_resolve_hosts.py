# SPDX-License-Identifier: Apache-2.0
"""``servicenow.cmdb_resolve_hosts`` -- batch sys_id → host_name lookup.

GET ``/api/now/table/cmdb_ci?sysparm_query=sys_idIN<id,id,...>`` to
resolve a list of CI sys_ids to their ``name`` fields.

Capability: ``tools:servicenow:read``.
"""

from __future__ import annotations

from typing import Any

import httpx

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.servicenow._auth import client_kwargs, resolve_base_url
from stargraph.tools.spec import SideEffects

__all__ = ["cmdb_resolve_hosts"]

_NAMESPACE = "servicenow"
_NAME = "cmdb_resolve_hosts"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:read"
_DEFAULT_TIMEOUT_S = 15.0
_PATH = "/api/now/table/cmdb_ci"
_MAX_BATCH = 100


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability=_REQUIRED_CAPABILITY,
    description=(
        "Resolve a list of CI sys_ids to their CMDB name fields. Batches "
        "into <=100 sys_ids per request."
    ),
)
async def cmdb_resolve_hosts(
    *,
    sys_ids: list[str],
) -> dict[str, Any]:
    """Resolve host sys_ids to names.

    Parameters
    ----------
    sys_ids
        Non-empty list of CI sys_ids.

    Returns
    -------
    dict[str, Any]
        ``{"status": "ok", "name_by_sys_id": {sys_id: name, ...},
        "host_names": [<sorted unique names>]}``.
    """
    cleaned = [str(s).strip() for s in (sys_ids or []) if s and str(s).strip()]
    if not cleaned:
        raise StargraphRuntimeError(
            "cmdb_resolve_hosts requires a non-empty sys_ids list",
        )
    base = resolve_base_url()
    kw, headers = client_kwargs()
    name_by_sysid: dict[str, str] = {}
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S, **kw) as client:
        for i in range(0, len(cleaned), _MAX_BATCH):
            batch = cleaned[i : i + _MAX_BATCH]
            params = {
                "sysparm_query": "sys_idIN" + ",".join(batch),
                "sysparm_fields": "sys_id,name",
                "sysparm_limit": str(len(batch)),
            }
            resp = await client.get(f"{base}{_PATH}", params=params, headers=headers)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json() or {}
            rows: list[dict[str, Any]] = payload.get("result") or []
            for row in rows:
                sid = str(row.get("sys_id", ""))
                if sid:
                    name_by_sysid[sid] = str(row.get("name", "") or "")
    host_names = sorted({n for n in name_by_sysid.values() if n})
    return {
        "status": "ok",
        "name_by_sys_id": name_by_sysid,
        "host_names": host_names,
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": _NAMESPACE,
            "external_id": f"cmdb_resolve_hosts:{len(cleaned)}",
        },
    }
