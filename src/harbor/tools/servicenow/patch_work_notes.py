# SPDX-License-Identifier: Apache-2.0
"""``servicenow.patch_work_notes`` -- PATCH work_notes onto a change request."""

from __future__ import annotations

import os
from typing import Any

from harbor.tools.decorator import tool
from harbor.tools.spec import SideEffects

from ._auth import client_kwargs, resolve_base_url

_NAMESPACE = "servicenow"
_NAME = "patch_work_notes"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:write"


def _live_enabled() -> bool:
    return os.environ.get("HARBOR_SERVICENOW_LIVE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.write,
    requires_capability=_REQUIRED_CAPABILITY,
    description="PATCH work_notes onto an existing ServiceNow change request.",
)
async def patch_work_notes(
    *,
    cr_sys_id: str,
    work_notes: str,
) -> dict[str, Any]:
    if not cr_sys_id or not cr_sys_id.strip():
        return {"status": "skipped", "reason": "empty cr_sys_id"}

    if not _live_enabled():
        return {
            "status": "dry-run",
            "cr_sys_id": cr_sys_id,
            "__harbor_provenance__": {
                "origin": "tool",
                "source": "servicenow",
                "external_id": f"dry-run:work_notes:{cr_sys_id}",
            },
        }

    import httpx

    ck, headers = client_kwargs()
    base_url = resolve_base_url()
    url = f"{base_url}/api/now/table/change_request/{cr_sys_id}"
    headers["Content-Type"] = "application/json"

    try:
        async with httpx.AsyncClient(timeout=10.0, **ck) as client:
            resp = await client.patch(url, json={"work_notes": work_notes}, headers=headers)
            resp.raise_for_status()
            return {
                "status": "ok",
                "cr_sys_id": cr_sys_id,
                "__harbor_provenance__": {
                    "origin": "tool",
                    "source": "servicenow",
                    "external_id": cr_sys_id,
                },
            }
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
