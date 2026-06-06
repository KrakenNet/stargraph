# SPDX-License-Identifier: Apache-2.0
"""``servicenow.cmdb_traverse_runs_on`` -- read-side CI relationship walk.

Given a Software CI ``parent_sys_id``, fetches every ``cmdb_rel_ci`` row
with the ServiceNow OOTB ``Runs on::Runs`` relationship type
(``60bc4e22c0a8010e01f074cbe6bd73c3``) and returns the child host
sys_ids.

Capability: ``tools:servicenow:read``.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from harbor.errors import HarborRuntimeError
from harbor.tools.decorator import tool
from harbor.tools.servicenow._auth import client_kwargs, resolve_base_url
from harbor.tools.spec import SideEffects

__all__ = ["cmdb_traverse_runs_on"]

_NAMESPACE = "servicenow"
_NAME = "cmdb_traverse_runs_on"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:read"
_DEFAULT_TIMEOUT_S = 15.0
_PATH = "/api/now/table/cmdb_rel_ci"
_RUNS_ON_TYPE = "60bc4e22c0a8010e01f074cbe6bd73c3"


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability=_REQUIRED_CAPABILITY,
    description=(
        "Walk Runs-on::Runs relationships from a parent Software CI to its "
        "child host CIs. Returns a list of child sys_id strings."
    ),
)
async def cmdb_traverse_runs_on(
    *,
    parent_sys_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Return host sys_ids that ``Runs-on`` the parent Software CI.

    Parameters
    ----------
    parent_sys_id
        ``sys_id`` of the parent (Software) CI. Required.
    limit
        ``sysparm_limit`` on the relationship query (cap 500).

    Returns
    -------
    dict[str, Any]
        ``{"status": "ok", "child_sys_ids": [...], "raw_rows": [...]}``.
        ``child_sys_ids`` deduped + sorted; ``raw_rows`` preserved for
        callers that need the link/value structure.
    """
    pid = (parent_sys_id or "").strip()
    if not pid:
        raise HarborRuntimeError(
            "cmdb_traverse_runs_on requires a non-empty parent_sys_id",
        )
    base = resolve_base_url()
    kw, headers = client_kwargs()
    params = {
        "sysparm_query": f"parent={pid}^type={_RUNS_ON_TYPE}",
        "sysparm_limit": str(min(max(int(limit), 1), 500)),
        "sysparm_fields": "child,u_install_version",
    }
    async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S, **kw) as client:
        resp = await client.get(f"{base}{_PATH}", params=params, headers=headers)
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json() or {}
        rows: list[dict[str, Any]] = payload.get("result") or []
    sys_ids: list[str] = []
    version_by_sys_id: dict[str, str] = {}
    for row in rows:
        child: object = row.get("child")
        sid = (
            str(cast("dict[str, Any]", child).get("value") or "")
            if isinstance(child, dict)
            else str(child or "")
        )
        if not sid:
            continue
        sys_ids.append(sid)
        iv = str(row.get("u_install_version") or "").strip()
        if iv and sid not in version_by_sys_id:
            version_by_sys_id[sid] = iv
    sys_ids = sorted(set(sys_ids))
    return {
        "status": "ok",
        "child_sys_ids": sys_ids,
        "install_version_by_sys_id": version_by_sys_id,
        "raw_rows": rows,
        "__harbor_provenance__": {
            "origin": "tool",
            "source": _NAMESPACE,
            "external_id": f"cmdb_traverse_runs_on:{pid}",
        },
    }
