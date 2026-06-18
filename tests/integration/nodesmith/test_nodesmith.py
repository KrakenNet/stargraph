# SPDX-License-Identifier: Apache-2.0
"""Nodesmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for
real in every test — these assert the "always works" contract end-to-end:
a bogus passing test cannot get a non-running node recorded.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from stargraph.skills.nodesmith import _ledger, gate
from stargraph.skills.nodesmith.nodes.build import Build
from stargraph.skills.nodesmith.nodes.recall import Recall
from stargraph.skills.nodesmith.nodes.record import RecordBuild
from stargraph.skills.nodesmith.nodes.triage import TriageGate
from stargraph.skills.nodesmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

    from stargraph.nodes.base import ExecutionContext

pytestmark = pytest.mark.integration

_CTX = cast("ExecutionContext", SimpleNamespace(run_id="nm-test"))

GOOD_NODE = """\
from stargraph.nodes.base import NodeBase


class BandNode(NodeBase):
    async def execute(self, state, ctx):
        sev = float(getattr(state, "severity_raw", 0.0) or 0.0)
        return {"severity_band": "high" if sev >= 7 else "low"}
"""
GOOD_TEST = """\
import asyncio
from node import BandNode


class _S:
    severity_raw = 9.0


class _Ctx:
    run_id = "t"


def test_band():
    assert asyncio.run(BandNode().execute(_S(), _Ctx())) == {"severity_band": "high"}
"""
GOOD_GEN: dict[str, Any] = {
    "class_name": "BandNode",
    "reads": ["severity_raw"],
    "writes": ["severity_band"],
    "fixture": {"severity_raw": 9.0},
    "node_source": GOOD_NODE,
    "test_source": GOOD_TEST,
}

UNDECLARED_WRITE_NODE = """\
from stargraph.nodes.base import NodeBase


class N(NodeBase):
    async def execute(self, state, ctx):
        return {'surprise': 1}
"""
CRASHING_NODE = """\
from stargraph.nodes.base import NodeBase


class N(NodeBase):
    async def execute(self, state, ctx):
        raise RuntimeError('boom')
"""
TRIVIAL_TEST = "def test_x():\n    assert True\n"

# Syntax error → fails every tier fast; the repair loop can never fix a constant stub.
BAD_GEN: dict[str, Any] = {
    "class_name": "Broken",
    "reads": [],
    "writes": ["x"],
    "fixture": {},
    "node_source": "def oops(:\n    pass\n",
    "test_source": "def test_x():\n    assert True\n",
}


def _stub_build(gen: dict[str, Any]) -> Build:
    b = Build()
    b._program.generate = lambda brief, lessons, last_findings: gen  # type: ignore[assignment]
    return b


async def _drive(nodes: list[Any], state: State) -> State:
    for node in nodes:
        out = await node.execute(state, _CTX)
        state = state.model_copy(update=out)
    return state


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable floor)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_node(tmp_path: Path) -> None:
    files = {gate.NODE_FILE: GOOD_NODE, gate.TEST_FILE: GOOD_TEST}
    results = gate.run_full_gate(
        tmp_path / "g",
        files,
        reads=["severity_raw"],
        writes=["severity_band"],
        fixture={"severity_raw": 9.0},
    )
    assert gate.all_passed(results)


async def test_gate_contract_catches_undeclared_write(tmp_path: Path) -> None:
    files = {gate.NODE_FILE: UNDECLARED_WRITE_NODE, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, reads=[], writes=["declared"], fixture={})
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed  # a trivially-passing test did NOT save it
    assert not gate.all_passed(results)


async def test_gate_contract_catches_crashing_node(tmp_path: Path) -> None:
    files = {gate.NODE_FILE: CRASHING_NODE, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, reads=[], writes=["y"], fixture={})
    assert not next(r for r in results if r.kind == "contract").passed


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await _stub_build(GOOD_GEN).execute(State(brief="band classifier"), _CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["class_name"] == "BandNode"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await _stub_build(BAD_GEN).execute(State(brief="broken"), _CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # _MAX_ATTEMPTS — bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="band classifier", model_id="stub-model", output_dir=str(out_dir))
    final = await _drive([_stub_build(GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["class_name"] == "BandNode"
    assert pairs[0]["passed"] is True
    assert pairs[0]["attempts"] == 1
    assert pairs[0]["model_id"] == "stub-model"
    assert final.landed_path
    assert (out_dir / "band_node.py").exists()
    assert (out_dir / "test_band_node.py").exists()


async def test_failure_logs_lesson_and_records_no_pair() -> None:
    state = State(brief="broken thing")
    await _drive([_stub_build(BAD_GEN), RecordBuild()], state)

    assert _ledger.load_trainset() == []  # no false-positive training data
    lessons = _ledger._read_jsonl(_ledger.home() / _ledger.LESSONS_FILE)  # pyright: ignore[reportPrivateUsage]
    assert any(le["failed_kind"] == "escalate" for le in lessons)


# --------------------------------------------------------------------------- #
# Reflexion recall + drift (idea 1 / idea 2 substrate)
# --------------------------------------------------------------------------- #
async def test_recall_surfaces_relevant_lesson() -> None:
    _ledger.append_lesson(
        brief="a node that scores severity bands",
        failed_kind="contract",
        finding="execute() wrote undeclared field 'band'",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated cron trigger thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="severity band scorer node"), _CTX)
    assert out["recalled_lessons"]
    assert "undeclared field" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), _CTX)
