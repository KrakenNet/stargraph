# SPDX-License-Identifier: Apache-2.0
"""``servicenow.patch_cr_state`` -- transition CR state via sn_chg_rest API."""

from __future__ import annotations

import os
from typing import Any

from harbor.tools.decorator import tool
from harbor.tools.spec import SideEffects

from ._auth import client_kwargs, resolve_base_url

_NAMESPACE = "servicenow"
_NAME = "patch_cr_state"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:write"

_STATE_ORDER = ["-5", "-4", "-3", "-2", "-1", "0", "3"]


def _live_enabled() -> bool:
    return os.environ.get("HARBOR_SERVICENOW_LIVE", "").strip().lower() in ("1", "true", "yes", "on")


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.write,
    requires_capability=_REQUIRED_CAPABILITY,
    description="Transition a ServiceNow CR state via /api/sn_chg_rest/change.",
)
async def patch_cr_state(
    *,
    cr_sys_id: str,
    target_state: str,
    work_notes: str,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not cr_sys_id:
        return {"ok": False, "error": "no cr_sys_id"}

    if not _live_enabled():
        return {
            "ok": True,
            "status": "dry-run",
            "target_state": target_state,
            "__harbor_provenance__": {
                "origin": "tool",
                "source": "servicenow",
                "external_id": f"dry-run:state:{cr_sys_id}",
            },
        }

    import httpx

    ck, headers = client_kwargs()
    base_url = resolve_base_url()
    headers["Content-Type"] = "application/json"

    body: dict[str, Any] = {"state": target_state, "work_notes": work_notes}
    if extra_fields:
        body.update(extra_fields)

    chg_url = f"{base_url}/api/sn_chg_rest/change/{cr_sys_id}"
    tbl_url = f"{base_url}/api/now/table/change_request/{cr_sys_id}"

    try:
        idx_target = _STATE_ORDER.index(target_state)
    except ValueError:
        idx_target = len(_STATE_ORDER)

    try:
        async with httpx.AsyncClient(timeout=20.0, **ck) as client:
            resp = await client.patch(chg_url, json=body, headers=headers)
            patch_status = resp.status_code

            try:
                r = await client.get(
                    tbl_url,
                    params={"sysparm_fields": "state"},
                    headers={k: v for k, v in headers.items() if k != "Content-Type"},
                )
                if r.status_code < 300:
                    sv = (r.json() or {}).get("result", {}).get("state", {})
                    state_val = str(sv.get("value", sv) if isinstance(sv, dict) else sv)
                else:
                    state_val = ""
            except Exception:  # noqa: BLE001
                state_val = ""

            if state_val in _STATE_ORDER and _STATE_ORDER.index(state_val) >= idx_target:
                return {
                    "ok": True,
                    "state_after": state_val,
                    "__harbor_provenance__": {
                        "origin": "tool",
                        "source": "servicenow",
                        "external_id": cr_sys_id,
                    },
                }
            if patch_status >= 300:
                return {"ok": False, "error": f"patch={patch_status}:{resp.text[:120]}"}
            return {"ok": False, "error": f"state_after={state_val!r} target={target_state!r}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
