# SPDX-License-Identifier: Apache-2.0
"""Shared auth + base-URL resolution for the servicenow read-side tools.

Mirrors ``create_change_request._resolve_auth`` / ``_resolve_base_url``
so all servicenow tools resolve env identically. Lives in ``_auth``
(not the public ``__init__``) so it stays an implementation detail.
"""

from __future__ import annotations

import os
from typing import Any

from stargraph.errors import StargraphRuntimeError


def resolve_auth() -> dict[str, Any]:
    """Build httpx auth kwargs from env. Raises if required vars missing."""
    kind = os.environ.get("SERVICENOW_AUTH_KIND", "basic").strip().lower()
    if kind == "bearer":
        token = os.environ.get("SERVICENOW_BEARER_TOKEN")
        if not token:
            raise StargraphRuntimeError(
                "SERVICENOW_AUTH_KIND=bearer requires SERVICENOW_BEARER_TOKEN",
                kind=kind,
            )
        return {"headers_extra": {"Authorization": f"Bearer {token}"}}
    if kind == "basic":
        user = os.environ.get("SERVICENOW_USERNAME")
        pw = os.environ.get("SERVICENOW_PASSWORD")
        if not user or not pw:
            raise StargraphRuntimeError(
                "SERVICENOW_AUTH_KIND=basic requires SERVICENOW_USERNAME and SERVICENOW_PASSWORD",
                kind=kind,
                user_present=bool(user),
                pw_present=bool(pw),
            )
        return {"auth": (user, pw)}
    raise StargraphRuntimeError(
        f"unknown SERVICENOW_AUTH_KIND={kind!r}; expected bearer/basic",
        kind=kind,
    )


def resolve_base_url() -> str:
    base = os.environ.get("SERVICENOW_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise StargraphRuntimeError(
            "SERVICENOW_BASE_URL is unset; cannot dispatch live ServiceNow call",
        )
    return base


def client_kwargs() -> tuple[dict[str, Any], dict[str, str]]:
    """Return ``(client_kwargs, default_headers)`` for an httpx.AsyncClient."""
    resolved = resolve_auth()
    headers = {"Accept": "application/json"}
    headers.update(resolved.get("headers_extra", {}))
    out: dict[str, Any] = {}
    if "auth" in resolved:
        out["auth"] = resolved["auth"]
    return out, headers
