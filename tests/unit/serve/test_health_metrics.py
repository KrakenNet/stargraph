# SPDX-License-Identifier: Apache-2.0
"""Unit: ``GET /health`` + ``GET /metrics`` (W2 ops surface).

Covers, against a real :class:`RunHistory` over a temp aiosqlite DB and
a real JSONL audit file (no stubs):

* ``/health`` 200 + per-component ``ok`` when store/registry/artifact
  deps are wired and the CLIPS engine constructs.
* ``/health`` 200 with ``unconfigured`` components on an empty deps
  container (permissive POC default — unconfigured is not an error).
* ``/health`` 503 + component ``error`` when the store probe fails
  (closed DB connection).
* ``/metrics`` Prometheus text exposition: runs-by-status gauge,
  run-duration summary, audit chain height, transition count.
* ``/metrics`` under a default-deny profile without a ``metrics:read``
  grant -> 403; ``/health`` stays reachable (probe endpoints must not
  require credentials).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import aiosqlite
import httpx
import pytest

from stargraph.serve.api import create_app
from stargraph.serve.history import RunHistory
from stargraph.serve.profiles import OssDefaultProfile, Profile

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.unit, pytest.mark.serve, pytest.mark.api]


async def _seeded_history(
    tmp_path: Path,
    *,
    audit_path: Path | None = None,
) -> tuple[RunHistory, aiosqlite.Connection]:
    """Real RunHistory with 2 finished + 1 failed + 1 pending rows."""
    db = await aiosqlite.connect(tmp_path / "history.db")
    history = RunHistory(db, jsonl_audit_path=audit_path)
    await history.bootstrap()
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    for run_id, status, duration in (
        ("r-1", "done", 120),
        ("r-2", "done", 80),
        ("r-3", "failed", 40),
    ):
        await history.insert_pending(run_id, "hash-a", "manual")
        await history.update_status(run_id, status, finished_at=now, duration_ms=duration)
    await history.insert_pending("r-4", "hash-a", "manual")
    return history, db


def _write_audit_log(path: Path) -> None:
    """Bare-format JSONL: 2 transition events + 1 result event."""
    events: list[dict[str, Any]] = [
        {"type": "transition", "run_id": "r-1", "step": 1, "payload": {}},
        {"type": "transition", "run_id": "r-1", "step": 2, "payload": {}},
        {"type": "result", "run_id": "r-1", "step": 3, "payload": {}},
    ]
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


async def _get(app: Any, route: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(route)


async def test_health_ok_with_wired_deps(tmp_path: Path) -> None:
    history, db = await _seeded_history(tmp_path)
    try:
        deps: dict[str, Any] = {
            "run_history": history,
            "artifact_store": object(),
            "registry": {"tools": None, "stores": None},
        }
        app = create_app(OssDefaultProfile(), deps=deps)
        resp = await _get(app, "/health")
    finally:
        await db.close()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["components"]["store"]["status"] == "ok"
    assert body["components"]["artifact_store"]["status"] == "ok"
    assert body["components"]["registry"]["status"] == "ok"
    # CLIPS engine construction is probed for real; in this environment
    # fathom is an installed hard dependency, so it must construct.
    assert body["components"]["fathom"]["status"] == "ok"


async def test_health_unconfigured_deps_is_still_ok(tmp_path: Path) -> None:
    del tmp_path
    app = create_app(OssDefaultProfile(), deps={})
    resp = await _get(app, "/health")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["components"]["store"]["status"] == "unconfigured"
    assert body["components"]["artifact_store"]["status"] == "unconfigured"
    assert body["components"]["registry"]["status"] == "unconfigured"


async def test_health_store_probe_failure_is_503(tmp_path: Path) -> None:
    history, db = await _seeded_history(tmp_path)
    # Close the connection under the wired RunHistory: the count() probe
    # must fail and fold to a component error.
    await db.close()
    app = create_app(OssDefaultProfile(), deps={"run_history": history})
    resp = await _get(app, "/health")

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["components"]["store"]["status"] == "error"
    # /health is deliberately unauthenticated, so a failing probe must report
    # up/down and nothing else -- no exception text, no paths, no versions.
    assert body["components"]["store"]["detail"] == "probe failed"


async def test_metrics_exposition(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.jsonl"
    _write_audit_log(audit_path)
    history, db = await _seeded_history(tmp_path, audit_path=audit_path)
    try:
        app = create_app(OssDefaultProfile(), deps={"run_history": history})
        resp = await _get(app, "/metrics")
    finally:
        await db.close()

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/plain")
    text = resp.text
    assert 'stargraph_runs_total{status="done"} 2' in text
    assert 'stargraph_runs_total{status="failed"} 1' in text
    assert 'stargraph_runs_total{status="pending"} 1' in text
    assert "stargraph_run_duration_milliseconds_count 3" in text
    assert "stargraph_run_duration_milliseconds_sum 240" in text
    assert "stargraph_audit_chain_height 3" in text
    assert "stargraph_rule_transitions_total 2" in text
    # Exposition-format hygiene: HELP/TYPE headers precede each family.
    assert "# TYPE stargraph_runs_total gauge" in text
    assert "# TYPE stargraph_run_duration_milliseconds summary" in text


async def test_metrics_without_history_is_empty_but_valid(tmp_path: Path) -> None:
    del tmp_path
    app = create_app(OssDefaultProfile(), deps={})
    resp = await _get(app, "/metrics")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/plain")
    assert "stargraph_runs_total" not in resp.text


async def test_metrics_default_deny_403_health_stays_open(tmp_path: Path) -> None:
    del tmp_path
    # Ad-hoc default-deny profile: create_app falls back to
    # BypassAuthProvider, whose grant set lacks ``metrics:read`` -> 403
    # under default-deny. /health is ungated by design (probes).
    profile = Profile(
        name="test-deny",
        tls_required=False,
        signature_verify_mandatory=False,
        default_deny_capabilities=True,
        audit_required=False,
    )
    app = create_app(profile, deps={})
    metrics_resp = await _get(app, "/metrics")
    health_resp = await _get(app, "/health")

    assert metrics_resp.status_code == 403, metrics_resp.text
    assert health_resp.status_code == 200, health_resp.text
