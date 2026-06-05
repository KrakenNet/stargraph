# SPDX-License-Identifier: Apache-2.0
"""``servicenow.upload_attachment`` -- POST file attachment to any SN table record."""

from __future__ import annotations

import os
from typing import Any

from harbor.tools.decorator import tool
from harbor.tools.spec import SideEffects

from ._auth import client_kwargs, resolve_base_url

_NAMESPACE = "servicenow"
_NAME = "upload_attachment"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:write"


def _live_enabled() -> bool:
    return os.environ.get("HARBOR_SERVICENOW_LIVE", "").strip().lower() in ("1", "true", "yes", "on")


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.write,
    requires_capability=_REQUIRED_CAPABILITY,
    description="Upload a file attachment to a ServiceNow table record.",
)
async def upload_attachment(
    *,
    table_name: str,
    table_sys_id: str,
    file_name: str,
    content: bytes,
    content_type: str = "application/octet-stream",
) -> dict[str, Any]:
    if not table_sys_id:
        return {"status": "skipped", "reason": "empty table_sys_id"}

    if not _live_enabled():
        return {
            "status": "dry-run",
            "table_name": table_name,
            "table_sys_id": table_sys_id,
            "file_name": file_name,
            "__harbor_provenance__": {
                "origin": "tool",
                "source": "servicenow",
                "external_id": f"dry-run:attach:{table_sys_id}:{file_name}",
            },
        }

    import httpx

    ck, headers = client_kwargs()
    base_url = resolve_base_url()
    headers["Content-Type"] = content_type
    url = (
        f"{base_url}/api/now/attachment/file"
        f"?table_name={table_name}&table_sys_id={table_sys_id}"
        f"&file_name={file_name}"
    )

    try:
        async with httpx.AsyncClient(timeout=30.0, **ck) as client:
            resp = await client.post(url, content=content, headers=headers)
            resp.raise_for_status()
            result = (resp.json() or {}).get("result", {})
            return {
                "status": "ok",
                "sys_id": str(result.get("sys_id", "")),
                "__harbor_provenance__": {
                    "origin": "tool",
                    "source": "servicenow",
                    "external_id": str(result.get("sys_id", "")),
                },
            }
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "sys_id": ""}
