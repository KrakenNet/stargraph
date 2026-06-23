# SPDX-License-Identifier: Apache-2.0
"""Triggersmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real
in every test — these assert the "always works" contract end-to-end: a bogus
passing test cannot get a trigger whose ``enqueue`` does not actually delegate to
the scheduler (or whose ``init`` is a no-op) recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.triggersmith import _ledger, gate
from stargraph.skills.triggersmith.nodes.build import Build
from stargraph.skills.triggersmith.nodes.recall import Recall
from stargraph.skills.triggersmith.nodes.record import RecordBuild
from stargraph.skills.triggersmith.nodes.triage import TriageGate
from stargraph.skills.triggersmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

GOOD_TRIGGER = """\
from typing import Any

from stargraph.errors import StargraphRuntimeError


class ManualEnqueueTrigger:
    def __init__(self) -> None:
        self._scheduler: Any = None

    def init(self, deps: dict[str, Any]) -> None:
        scheduler = deps.get("scheduler")
        if scheduler is None:
            raise StargraphRuntimeError("init requires deps['scheduler']")
        self._scheduler = scheduler

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def routes(self) -> list[Any]:
        return []

    def enqueue(
        self,
        graph_id: str,
        params: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        if self._scheduler is None:
            raise StargraphRuntimeError("enqueue requires init(deps)")
        handle = self._scheduler.enqueue(
            graph_id=graph_id, params=params, idempotency_key=idempotency_key
        )
        return handle.run_id
"""
GOOD_TRIGGER_TEST = """\
from trigger import ManualEnqueueTrigger


class _Handle:
    def __init__(self, run_id):
        self.run_id = run_id


class _RecScheduler:
    def __init__(self):
        self.calls = []

    def enqueue(self, graph_id, params, idempotency_key=None):
        self.calls.append({"graph_id": graph_id, "params": params})
        return _Handle("run-1")


def test_enqueue_delegates():
    rec = _RecScheduler()
    t = ManualEnqueueTrigger()
    t.init({"scheduler": rec})
    assert t.enqueue("graph:demo", {"alpha": 1}) == "run-1"
    assert rec.calls[0]["graph_id"] == "graph:demo"
"""
GOOD_FIXTURE: dict[str, Any] = {"graph_id": "graph:demo", "params": {"alpha": 1}}
GOOD_GEN: dict[str, Any] = {
    "class_name": "ManualEnqueueTrigger",
    "fixture": GOOD_FIXTURE,
    "trigger_source": GOOD_TRIGGER,
    "test_source": GOOD_TRIGGER_TEST,
}

# Adversarial: enqueue returns a hardcoded run_id WITHOUT calling the scheduler.
# Its own trivial test passes, but the contract tier catches it (0 recorded calls
# + wrong run_id).
FAKE_ENQUEUE_TRIGGER = """\
from typing import Any

from stargraph.errors import StargraphRuntimeError


class FakeTrigger:
    def __init__(self) -> None:
        self._scheduler: Any = None

    def init(self, deps: dict[str, Any]) -> None:
        scheduler = deps.get("scheduler")
        if scheduler is None:
            raise StargraphRuntimeError("init requires deps['scheduler']")
        self._scheduler = scheduler

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def routes(self) -> list[Any]:
        return []

    def enqueue(
        self,
        graph_id: str,
        params: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        # Never touches the scheduler — fabricates a run_id.
        return "run-FIXED-123"
"""

# Adversarial: init is a no-op (never captures the scheduler) → enqueue can't
# delegate. The driver's init({}) guard catches this too.
NOOP_INIT_TRIGGER = """\
from typing import Any


class NoopTrigger:
    def __init__(self) -> None:
        self._scheduler: Any = None

    def init(self, deps: dict[str, Any]) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def routes(self) -> list[Any]:
        return []

    def enqueue(
        self,
        graph_id: str,
        params: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        return "run-FIXED-123"
"""

TRIVIAL_TEST = "def test_x():\n    assert True\n"

# Syntax error → fails every tier fast; the repair loop can never fix a constant stub.
BAD_GEN: dict[str, Any] = {
    "class_name": "Broken",
    "fixture": {"graph_id": "g", "params": {}},
    "trigger_source": "class Oops(:\n    pass\n",
    "test_source": TRIVIAL_TEST,
}


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable floor)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_trigger(tmp_path: Path) -> None:
    files = {gate.TRIGGER_FILE: GOOD_TRIGGER, gate.TEST_FILE: GOOD_TRIGGER_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    assert gate.all_passed(results)


async def test_gate_contract_catches_faked_enqueue(tmp_path: Path) -> None:
    # The cheat: enqueue returns the right-looking run_id but never delegates.
    # Its own test (TRIVIAL_TEST) passes, yet the contract tier rejects it.
    files = {gate.TRIGGER_FILE: FAKE_ENQUEUE_TRIGGER, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed  # a trivially-passing test did NOT save it
    assert not gate.all_passed(results)


async def test_gate_contract_catches_noop_init(tmp_path: Path) -> None:
    files = {gate.TRIGGER_FILE: NOOP_INIT_TRIGGER, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    assert not next(r for r in results if r.kind == "contract").passed


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="manual enqueue trigger"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["class_name"] == "ManualEnqueueTrigger"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="manual enqueue trigger", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["class_name"] == "ManualEnqueueTrigger"
    assert pairs[0]["variant"] == "manual"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"
    assert final.landed_path
    assert (out_dir / "manual_enqueue_trigger.py").exists()
    assert (out_dir / "test_manual_enqueue_trigger.py").exists()


async def test_failure_logs_lesson_and_records_no_pair() -> None:
    state = State(brief="broken thing")
    await drive([stub_build(Build, BAD_GEN), RecordBuild()], state)

    assert _ledger.load_trainset() == []  # no false-positive training data
    lessons = _ledger._read_jsonl(_ledger.home() / _ledger.LESSONS_FILE)  # pyright: ignore[reportPrivateUsage]
    assert any(le["failed_kind"] == "escalate" for le in lessons)


# --------------------------------------------------------------------------- #
# Reflexion recall + drift (idea 1 / idea 2 substrate)
# --------------------------------------------------------------------------- #
async def test_recall_surfaces_relevant_lesson() -> None:
    _ledger.append_lesson(
        brief="a manual trigger that enqueues a run",
        failed_kind="contract",
        finding="enqueue did not delegate to the scheduler",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated cidr summary tool thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="manual enqueue trigger"), CTX)
    assert out["recalled_lessons"]
    assert "delegate" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
