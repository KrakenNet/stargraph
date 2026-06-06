# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.22: capability-deny + audit-emission integration.

Locks the design §5.4 / FR-65 / AC-8.5 contract: when the cleared
profile's default-deny gate refuses a capability, the gate emits a
:class:`~stargraph.runtime.events.BosunAuditEvent` with
``fact["kind"] == "capability_denied"`` to the wired audit sink before
raising the 403. The sink writes one canonical line to the JSONL audit
file (``deps["audit_sink"]``) so a downstream SIEM can correlate the
denial with the offending caller.

Cases covered (FR-65, FR-69, AC-8.5):

1. ``runs:cancel`` denied under cleared profile → 403 + audit entry
   with ``actor``, ``capability="runs:cancel"``, ``route`` matching the
   request path.
2. Same call under oss-default profile → 200/4xx (no 403 from the
   gate); no ``capability_denied`` audit entry emitted.
3. ``runs:pause`` denied under cleared profile → 403 + audit entry
   shape mirrors case 1 but with ``capability="runs:pause"``.

The audit sink is a real :class:`stargraph.audit.jsonl.JSONLAuditSink`
pointed at a tmp_path file so the JSONL bytes are inspected directly
(no double layer of mocks). The auth provider is a no-grant stub so
the cleared-vs-oss profile divergence is the only behavioral switch.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from stargraph.audit.jsonl import JSONLAuditSink
from stargraph.serve.api import create_app
from stargraph.serve.auth import AuthContext
from stargraph.serve.profiles import ClearedProfile, OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.serve, pytest.mark.integration]


class _NoGrantAuthProvider:
    """Auth provider that returns ``actor='alice'`` with NO capability grants."""

    async def authenticate(self, request: Any) -> AuthContext:
        del request
        return AuthContext(
            actor="alice",
            capability_grants=set(),
            session_id="test-session-1",
        )


def _read_audit_lines(path: Path) -> list[dict[str, Any]]:
    """Parse the JSONL audit file into a list of event dicts.

    Each line is one orjson-encoded ``BosunAuditEvent.model_dump()``
    payload (no signing envelope in this test harness). Returns
    ``[]`` when the file does not exist (sink never wrote).
    """
    if not path.exists():
        return []
    lines: list[dict[str, Any]] = []
    for raw in path.read_bytes().splitlines():
        if not raw.strip():
            continue
        lines.append(json.loads(raw))
    return lines


@pytest.mark.serve
async def test_cleared_cancel_without_grant_403_with_audit(tmp_path: Path) -> None:
    """Cleared + missing ``runs:cancel`` -> 403 + ``capability_denied`` audit."""
    audit_path = tmp_path / "audit.jsonl"
    sink = JSONLAuditSink(audit_path)
    deps: dict[str, Any] = {"runs": {}, "audit_sink": sink}

    app = create_app(ClearedProfile(), deps=deps)
    app.state.auth_provider = _NoGrantAuthProvider()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/run-x/cancel")
    await sink.close()

    assert resp.status_code == 403, resp.text

    entries = _read_audit_lines(audit_path)
    deny_entries = [e for e in entries if e.get("fact", {}).get("kind") == "capability_denied"]
    assert len(deny_entries) == 1, (
        f"expected exactly 1 capability_denied audit entry; got {len(deny_entries)} in {entries!r}"
    )
    fact = deny_entries[0]["fact"]
    assert fact["actor"] == "alice", f"unexpected actor: {fact!r}"
    assert fact["capability"] == "runs:cancel", f"unexpected capability: {fact!r}"
    assert fact["route"] == "/v1/runs/run-x/cancel", f"unexpected route: {fact!r}"
    assert deny_entries[0]["type"] == "bosun_audit"


@pytest.mark.serve
async def test_oss_default_cancel_without_grant_no_audit(tmp_path: Path) -> None:
    """OSS-default + missing ``runs:cancel`` -> permissive; no deny audit emitted.

    The route handler returns 404 (run not found) since the gate did
    not deny. The 404 (rather than 403) is the contract: gate
    permissive. No ``capability_denied`` entry should appear in the
    audit log.
    """
    audit_path = tmp_path / "audit.jsonl"
    sink = JSONLAuditSink(audit_path)
    deps: dict[str, Any] = {"runs": {}, "audit_sink": sink}

    app = create_app(OssDefaultProfile(), deps=deps)
    app.state.auth_provider = _NoGrantAuthProvider()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/run-x/cancel")
    await sink.close()

    assert resp.status_code == 404, resp.text

    entries = _read_audit_lines(audit_path)
    deny_entries = [e for e in entries if e.get("fact", {}).get("kind") == "capability_denied"]
    assert len(deny_entries) == 0, (
        f"expected NO capability_denied audit entries under oss-default; got {deny_entries!r}"
    )


@pytest.mark.serve
async def test_cleared_pause_without_grant_403_with_audit(tmp_path: Path) -> None:
    """Cleared + missing ``runs:pause`` -> 403 + ``capability_denied`` audit."""
    audit_path = tmp_path / "audit.jsonl"
    sink = JSONLAuditSink(audit_path)
    deps: dict[str, Any] = {"runs": {}, "audit_sink": sink}

    app = create_app(ClearedProfile(), deps=deps)
    app.state.auth_provider = _NoGrantAuthProvider()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/runs/run-x/pause")
    await sink.close()

    assert resp.status_code == 403, resp.text

    entries = _read_audit_lines(audit_path)
    deny_entries = [e for e in entries if e.get("fact", {}).get("kind") == "capability_denied"]
    assert len(deny_entries) == 1
    fact = deny_entries[0]["fact"]
    assert fact["actor"] == "alice"
    assert fact["capability"] == "runs:pause"
    assert fact["route"] == "/v1/runs/run-x/pause"
