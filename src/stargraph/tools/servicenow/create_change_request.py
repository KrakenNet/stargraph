# SPDX-License-Identifier: Apache-2.0
"""``servicenow.create_change_request`` -- gated POST to ``/api/now/table/change_request``.

Out-of-band of Nautilus (read-only adapter surface, see module docstring
on ``stargraph.tools.servicenow``). This is the documented path for a
write-side ServiceNow integration in Stargraph v1; the long-term home is
the v2 Nautilus write surface (see
``nautilus/design-docs/05-ecosystem-roadmap.md`` -- "Weeks 22.5").

Execution model:

* **Dry-run** (default): no network call. Returns a synthetic envelope
  with ``status="dry-run"`` and the resolved request body so callers
  can inspect what *would* have been posted.
* **Live**: ``STARGRAPH_SERVICENOW_LIVE=1`` flips the tool into live mode.
  POSTs to ``${SERVICENOW_BASE_URL}/api/now/table/change_request`` with
  the configured auth (basic / bearer) and returns the API response
  body plus a Stargraph-provenance envelope.

Auth resolution (live mode only):

* ``SERVICENOW_AUTH_KIND=bearer`` -> ``Authorization: Bearer ${SERVICENOW_BEARER_TOKEN}``
* ``SERVICENOW_AUTH_KIND=basic`` (default) ->
  ``HTTPBasicAuth(SERVICENOW_USERNAME, SERVICENOW_PASSWORD)``
* mTLS unsupported in v1; raises :class:`StargraphRuntimeError` if requested.

Idempotency:

* Caller supplies ``correlation_id``. The body's ``correlation_id``
  field is set verbatim. ServiceNow's table API treats matching
  correlation IDs as the same logical record (deduped on the server
  side when the caller adds the matching system property; documented in
  the runbook).
* The tool itself does NOT cache; it relies on the server-side
  dedupe.

Capability:

* ``tools:servicenow:write`` -- absent on every graph by default;
  the engine's tool-registry filter must explicitly add it before this
  tool resolves.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["create_change_request"]


_LIVE_ENV = "STARGRAPH_SERVICENOW_LIVE"
_NAMESPACE = "servicenow"
_NAME = "create_change_request"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:servicenow:write"
_DEFAULT_TIMEOUT_S = 30.0
_PATH = "/api/now/table/change_request"


def _live_enabled() -> bool:
    """``True`` iff ``STARGRAPH_SERVICENOW_LIVE`` is set to a truthy string."""
    return os.environ.get(_LIVE_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _resolve_auth() -> dict[str, Any]:
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
    if kind == "mtls":
        raise StargraphRuntimeError(
            "SERVICENOW_AUTH_KIND=mtls is not supported by stargraph.tools.servicenow in v1",
            kind=kind,
        )
    raise StargraphRuntimeError(
        f"unknown SERVICENOW_AUTH_KIND={kind!r}; expected one of bearer/basic/mtls",
        kind=kind,
    )


def _resolve_base_url() -> str:
    base = os.environ.get("SERVICENOW_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise StargraphRuntimeError(
            "SERVICENOW_BASE_URL is unset; cannot dispatch live ServiceNow call",
        )
    return base


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.write,
    requires_capability=_REQUIRED_CAPABILITY,
    description=(
        "Create a ServiceNow change request via /api/now/table/change_request. "
        "Dry-run by default; set STARGRAPH_SERVICENOW_LIVE=1 to dispatch the real "
        "POST. Caller MUST pass a correlation_id for idempotent retries."
    ),
)
async def create_change_request(
    *,
    short_description: str,
    description: str,
    correlation_id: str,
    assignment_group: str | None = None,
    priority: int = 4,
    urgency: int = 4,
    impact: int = 4,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a ServiceNow CR.

    Parameters
    ----------
    short_description
        ServiceNow ``short_description`` (max 160 chars per platform).
    description
        Long-form description posted to the CR.
    correlation_id
        Caller-supplied idempotency key. Required: empty / whitespace
        rejected. Sent as the ``correlation_id`` field so server-side
        dedupe can collapse retries (assuming the operator has wired
        the matching ServiceNow system property).
    assignment_group
        Optional ``assignment_group`` sys_id or display value.
    priority, urgency, impact
        Standard ServiceNow severity dials (1=critical .. 4=low). Default
        is the "low" lane to keep automated fillings out of the on-call
        path until an operator escalates.
    extra_fields
        Additional table columns to set verbatim. Caller is responsible
        for ServiceNow-side validity (no pre-flight schema check).

    Returns
    -------
    dict[str, Any]
        Live mode: ``{"status": "ok", "result": <ServiceNow body>,
        "request_body": <posted body>, "__stargraph_provenance__":
        {"origin": "tool", "source": "servicenow", "external_id":
        <sys_id>}}``.
        Dry-run: ``{"status": "dry-run", "request_body": <body>,
        "__stargraph_provenance__": {"origin": "tool", "source":
        "servicenow", "external_id": "dry-run:<correlation_id>"}}``.

    Raises
    ------
    StargraphRuntimeError
        For unset env auth, unknown auth kind, or missing
        ``correlation_id``.
    httpx.HTTPStatusError
        Non-2xx ServiceNow response in live mode (caller decides retry).
    """
    if not correlation_id or not correlation_id.strip():
        raise StargraphRuntimeError(
            "create_change_request requires a non-empty correlation_id for idempotency",
        )

    body: dict[str, Any] = {
        "short_description": short_description,
        "description": description,
        "correlation_id": correlation_id,
        "priority": str(priority),
        "urgency": str(urgency),
        "impact": str(impact),
    }
    if assignment_group is not None:
        body["assignment_group"] = assignment_group
    if extra_fields:
        body.update(extra_fields)

    if not _live_enabled():
        return {
            "status": "dry-run",
            "request_body": body,
            "__stargraph_provenance__": {
                "origin": "tool",
                "source": "servicenow",
                "external_id": f"dry-run:{correlation_id}",
            },
        }

    auth_resolved = _resolve_auth()
    base_url = _resolve_base_url()
    url = f"{base_url}{_PATH}"
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    headers.update(auth_resolved.get("headers_extra", {}))
    client_kwargs: dict[str, Any] = {"timeout": _DEFAULT_TIMEOUT_S}
    if "auth" in auth_resolved:
        client_kwargs["auth"] = auth_resolved["auth"]

    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.post(url, headers=headers, json=body)
        resp.raise_for_status()
        result = resp.json().get("result", {})

    sys_id = result.get("sys_id") or "unknown"
    return {
        "status": "ok",
        "result": result,
        "request_body": body,
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": "servicenow",
            "external_id": sys_id,
        },
    }
