# SPDX-License-Identifier: Apache-2.0
"""``servicenow.cmdb_query_software`` -- read-side Software CI lookup.

GET ``/api/now/table/cmdb_ci`` with
``sysparm_query=sys_class_name=cmdb_ci_spkg^nameLIKE{name_like}`` plus
optional vendor narrowing. Returns raw rows so the correlation agent
can rank / filter; this tool deliberately does NOT score candidates —
that's an agent-side concern (so the rule pack can also intervene).

Capability: ``tools:servicenow:read`` (default-allow on remediation
capability profiles; gated on graphs that opt out).
"""

from __future__ import annotations

from typing import Any

import httpx

from harbor.errors import HarborRuntimeError
from harbor.tools.decorator import tool
from harbor.tools.servicenow._auth import client_kwargs, resolve_base_url
from harbor.tools.spec import SideEffects

__all__ = ["cmdb_query_software"]

_NAMESPACE = "servicenow"
_NAME = "cmdb_query_software"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:read"
_DEFAULT_TIMEOUT_S = 15.0
_PATH = "/api/now/table/cmdb_ci"


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability=_REQUIRED_CAPABILITY,
    description=(
        "Query CMDB Software CIs (cmdb_ci_spkg) matching a nameLIKE substring, "
        "optionally narrowed by vendor. Returns a list of raw rows with "
        "sys_id, name, version, vendor."
    ),
)
async def cmdb_query_software(
    *,
    name_like: str,
    vendor: str = "",
    limit: int = 25,
) -> dict[str, Any]:
    """Search CMDB for Software CIs.

    Parameters
    ----------
    name_like
        Substring matched against the CI ``name`` field. Required
        (empty rejects to keep agents honest — broad queries belong
        in a separate ``cmdb_query_ci`` tool, not here).
    vendor
        Optional ``vendor`` field substring; appended as
        ``^vendorLIKE{vendor}`` when non-empty.
    limit
        ``sysparm_limit`` (max rows returned). Cap raised to 100.

    Returns
    -------
    dict[str, Any]
        ``{"status": "ok", "rows": [...], "query": "<sysparm_query>",
        "__harbor_provenance__": {...}}``.

    Raises
    ------
    HarborRuntimeError
        Empty ``name_like`` or missing env / auth.
    httpx.HTTPStatusError
        Non-2xx ServiceNow response.
    """
    if not name_like or not name_like.strip():
        raise HarborRuntimeError(
            "cmdb_query_software requires a non-empty name_like",
        )
    base = resolve_base_url()
    kw, headers = client_kwargs()
    query = f"sys_class_name=cmdb_ci_spkg^nameLIKE{name_like.strip()}"
    if vendor and vendor.strip():
        query += f"^vendorLIKE{vendor.strip()}"
    params = {
        "sysparm_query": query,
        "sysparm_limit": str(min(max(int(limit), 1), 100)),
        "sysparm_fields": "sys_id,name,version,vendor",
    }
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S, **kw) as client:
        resp = await client.get(f"{base}{_PATH}", params=params, headers=headers)
        resp.raise_for_status()
        rows = resp.json().get("result") or []
    return {
        "status": "ok",
        "rows": rows,
        "query": query,
        "__harbor_provenance__": {
            "origin": "tool",
            "source": _NAMESPACE,
            "external_id": f"cmdb_query_software:{name_like}",
        },
    }
