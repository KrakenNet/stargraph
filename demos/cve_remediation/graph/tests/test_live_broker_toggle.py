# SPDX-License-Identifier: Apache-2.0
"""Tests for the ``CVE_REM_LIVE_BROKER`` toggle (S6).

Three modes covered:

* **Offline (default)** -- ``broker_request_envelope`` is the
  :func:`broker_call_args` payload; no broker call.
* **Live + broker missing** -- env set, ``current_broker()`` returns
  ``None``; envelope falls back to offline shape with
  ``broker_unavailable=True``.
* **Live + broker present** -- env set, fake broker returns a fake
  :class:`BrokerResponse`; the helper unwraps the response into the
  envelope and stamps the ``__harbor_provenance__`` block.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

import pytest

import demos.cve_remediation.graph.real_nodes as rn_mod
from demos.cve_remediation.graph.intents import DriftWatchSpawnIntent
from demos.cve_remediation.graph.real_nodes import (
    CreateChangeRequestNode,
    DriftWatchSpawnNode,
    PublishDocPlusNode,
)
from demos.cve_remediation.graph.state import (
    CodeRuntime,
    CorrelatedAssets,
    CveRemState,
    SsvcTier,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeBrokerResponse:
    """Mimics the ``model_dump(mode='json')`` + ``request_id`` surface."""

    def __init__(self, request_id: str = "req-fake-1") -> None:
        self.request_id = request_id

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": "ok",
            "payload": {"echo": "fake"},
        }


class _FakeBroker:
    """Records the last arequest call and returns a fake response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def arequest(
        self,
        *,
        agent_id: str,
        intent: str,
        context: dict[str, Any] | None = None,
        fact_set_hash: str | None = None,
    ) -> _FakeBrokerResponse:
        self.calls.append(
            {
                "agent_id": agent_id,
                "intent": intent,
                "context": context,
                "fact_set_hash": fact_set_hash,
            }
        )
        return _FakeBrokerResponse()


@contextmanager
def _patched_broker(monkeypatch: pytest.MonkeyPatch, broker: Any | None) -> Any:
    """Patch ``current_broker()`` import inside the helper to return *broker*."""
    import harbor.serve.contextvars as cv_mod

    monkeypatch.setattr(cv_mod, "current_broker", lambda: broker)
    yield broker


def _ctx() -> object:
    return object()


# ---------------------------------------------------------------------------
# Offline mode (default)
# ---------------------------------------------------------------------------


def test_offline_mode_emits_envelope() -> None:
    state = CveRemState(cve_id="CVE-X", run_id="r-1")
    out = asyncio.run(DriftWatchSpawnNode().execute(state, _ctx()))
    env = out["broker_request_envelope"]
    assert env["intent"] == "cve_rem.drift_watch_spawn"
    assert env["agent_id"] == "cve-rem-pipeline"
    assert "context" in env
    assert "broker_unavailable" not in env  # offline = full envelope


# ---------------------------------------------------------------------------
# Live + missing broker
# ---------------------------------------------------------------------------


def test_live_mode_no_broker_marks_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVE_REM_LIVE_BROKER", "1")
    with _patched_broker(monkeypatch, None):
        state = CveRemState(cve_id="CVE-X", run_id="r-1")
        out = asyncio.run(DriftWatchSpawnNode().execute(state, _ctx()))
    env = out["broker_request_envelope"]
    assert env["broker_unavailable"] is True
    assert env["intent"] == "cve_rem.drift_watch_spawn"


# ---------------------------------------------------------------------------
# Live + broker present
# ---------------------------------------------------------------------------


def test_live_mode_with_broker_unwraps_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CVE_REM_LIVE_BROKER", "1")
    fake = _FakeBroker()
    with _patched_broker(monkeypatch, fake):
        state = CveRemState(cve_id="CVE-X", run_id="r-1")
        out = asyncio.run(DriftWatchSpawnNode().execute(state, _ctx()))
    # Broker was invoked exactly once with the right intent name.
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["intent"] == "cve_rem.drift_watch_spawn"
    assert call["agent_id"] == "cve-rem-pipeline"
    assert call["context"]["cve_id"] == "CVE-X"
    # Response was unwrapped + stamped with provenance.
    env = out["broker_request_envelope"]
    assert env["request_id"] == "req-fake-1"
    assert env["__harbor_provenance__"]["origin"] == "tool"
    assert env["__harbor_provenance__"]["source"] == "nautilus"
    assert env["__harbor_provenance__"]["external_id"] == "req-fake-1"


def test_live_mode_publish_docplus_no_ref_skips() -> None:
    """Live toggle does not affect early-return paths (no docx_artifact_ref)."""
    state = CveRemState(docx_artifact_ref="")
    out = asyncio.run(PublishDocPlusNode().execute(state, _ctx()))
    assert out == {"docplus_published": False}


def test_live_mode_create_cr_preserves_extras(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live broker response coexists with extra state fields the node sets."""
    monkeypatch.setenv("CVE_REM_LIVE_BROKER", "1")
    fake = _FakeBroker()
    with _patched_broker(monkeypatch, fake):
        state = CveRemState(
            cve_id="CVE-Y",
            plan_hash="p1",
            correlated=CorrelatedAssets(affected_assets=["host-1"]),
            code_runtime=CodeRuntime.ANSIBLE,
            ssvc_tier=SsvcTier.ATTEND,
        )
        out = asyncio.run(CreateChangeRequestNode().execute(state, _ctx()))
    assert out["cr_correlation_id"].startswith("CR-")
    assert out["cr_status"] == "draft"
    assert out["last_broker_intent"] == "cve_rem.create_change_request"
    assert "__harbor_provenance__" in out["broker_request_envelope"]


# ---------------------------------------------------------------------------
# Helper-direct tests
# ---------------------------------------------------------------------------


def test_dispatch_intent_returns_envelope_offline() -> None:
    intent = DriftWatchSpawnIntent(cve_id="CVE-Z", parent_run_id="r-1")
    out = asyncio.run(rn_mod._dispatch_intent(intent))
    assert out["last_broker_intent"] == "cve_rem.drift_watch_spawn"
    assert out["broker_request_envelope"]["context"]["cve_id"] == "CVE-Z"


@pytest.mark.parametrize("flag", ["1", "true", "TRUE", "yes", "on"])
def test_live_broker_enabled_truthy_values(
    flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CVE_REM_LIVE_BROKER", flag)
    assert rn_mod._live_broker_enabled() is True


@pytest.mark.parametrize("flag", ["0", "false", "off", "no", ""])
def test_live_broker_enabled_falsy_values(
    flag: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CVE_REM_LIVE_BROKER", flag)
    assert rn_mod._live_broker_enabled() is False
