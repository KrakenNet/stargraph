# SPDX-License-Identifier: Apache-2.0
"""``servicenow.poll_approval`` -- poll sysapproval_approver for CR approval."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from harbor.tools.decorator import tool
from harbor.tools.spec import SideEffects

from ._auth import client_kwargs, resolve_base_url

_NAMESPACE = "servicenow"
_NAME = "poll_approval"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:read"


def _live_enabled() -> bool:
    return os.environ.get("HARBOR_SERVICENOW_LIVE", "").strip().lower() in ("1", "true", "yes", "on")


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability=_REQUIRED_CAPABILITY,
    description="Poll ServiceNow sysapproval_approver for CR approval status.",
)
async def poll_approval(
    *,
    cr_sys_id: str,
    timeout_s: int = 30,
    interval_s: float = 2.0,
) -> dict[str, Any]:
    if not cr_sys_id or timeout_s <= 0:
        return {"approved": False, "approver_id": ""}

    if not _live_enabled():
        return {
            "approved": False,
            "approver_id": "",
            "status": "dry-run",
            "__harbor_provenance__": {
                "origin": "tool",
                "source": "servicenow",
                "external_id": f"dry-run:approval:{cr_sys_id}",
            },
        }

    import httpx

    ck, headers = client_kwargs()
    base_url = resolve_base_url()
    url = f"{base_url}/api/now/table/sysapproval_approver"

    deadline = asyncio.get_event_loop().time() + timeout_s
    while True:
        try:
            async with httpx.AsyncClient(timeout=10.0, **ck) as client:
                resp = await client.get(
                    url,
                    params={
                        "sysparm_query": f"document_id={cr_sys_id}^state=approved",
                        "sysparm_limit": "1",
                        "sysparm_fields": "sys_id,approver,state",
                    },
                    headers=headers,
                )
                if resp.status_code < 300:
                    rows = (resp.json() or {}).get("result", []) or []
                    if rows:
                        approver_field = rows[0].get("approver") or ""
                        approver_id = (
                            approver_field.get("value", "")
                            if isinstance(approver_field, dict)
                            else str(approver_field)
                        )
                        return {
                            "approved": True,
                            "approver_id": approver_id or "sn-approver",
                            "__harbor_provenance__": {
                                "origin": "tool",
                                "source": "servicenow",
                                "external_id": cr_sys_id,
                            },
                        }
        except Exception:  # noqa: BLE001
            pass
        if asyncio.get_event_loop().time() >= deadline:
            return {"approved": False, "approver_id": ""}
        await asyncio.sleep(interval_s)
