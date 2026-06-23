# SPDX-License-Identifier: Apache-2.0
"""Graphsmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real
in every test — these assert the composite "always works" contract end-to-end: the
contract tier LOADS the assembled bundle into a real ``Graph`` and RUNS it, so a
bundle whose nodes do not actually wire together (a second node that ignores the
channel the first wrote) cannot land even when its own unit test passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.graphsmith import _ledger, gate
from stargraph.skills.graphsmith.nodes.build import Build
from stargraph.skills.graphsmith.nodes.recall import Recall
from stargraph.skills.graphsmith.nodes.record import RecordBuild
from stargraph.skills.graphsmith.nodes.triage import TriageGate
from stargraph.skills.graphsmith.seeds import (
    _NORMALIZE_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _NORMALIZE_NODES,  # pyright: ignore[reportPrivateUsage]
    _NORMALIZE_STATE,  # pyright: ignore[reportPrivateUsage]
    _NORMALIZE_TEST,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.graphsmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

# The good bundle (seed 1: normalize → classify, properly wired).
GOOD_STATE = _NORMALIZE_STATE
GOOD_NODES = _NORMALIZE_NODES
GOOD_TEST = _NORMALIZE_TEST
GOOD_FIXTURE = _NORMALIZE_FIXTURE
GOOD_GEN: dict[str, Any] = {
    "graph_id": "alert-normalizer",
    "node_classes": ["Normalize", "Classify"],
    "state_source": GOOD_STATE,
    "nodes_source": GOOD_NODES,
    "test_source": GOOD_TEST,
    "fixture": GOOD_FIXTURE,
}

# Adversarial: Classify reads the RAW input (case-sensitively) instead of the
# `normalized` channel Normalize wrote — so the two nodes never actually connect.
# Its own unit test could pass in isolation; the assembled run produces label="ok"
# where the fixture expects "alert", so the contract tier catches the broken wiring.
NON_WIRED_NODES = """\
from __future__ import annotations

from typing import Any

from stargraph.nodes.base import NodeBase


class Normalize(NodeBase):
    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {"normalized": str(state.raw).strip().lower()}


class Classify(NodeBase):
    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        # BUG: reads `raw`, not the `normalized` channel Normalize wrote.
        return {"label": "alert" if "error" in state.raw else "ok"}
"""

TRIVIAL_TEST = "def test_x() -> None:\n    assert True\n"

# Syntax error → fails the static tier fast; the repair loop can never fix a stub.
BAD_GEN: dict[str, Any] = {
    "graph_id": "broken",
    "node_classes": ["Oops"],
    "state_source": GOOD_STATE,
    "nodes_source": "class Oops(:\n    pass\n",
    "test_source": TRIVIAL_TEST,
    "fixture": {},
}


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable composite floor)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_bundle(tmp_path: Path) -> None:
    files = {
        gate.STATE_FILE: GOOD_STATE,
        gate.NODES_FILE: GOOD_NODES,
        gate.GRAPH_FILE: gate.assemble_graph_yaml("alert-normalizer", ["Normalize", "Classify"]),
        gate.TEST_FILE: GOOD_TEST,
    }
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    assert gate.all_passed(results), [r.findings for r in results if not r.passed]


async def test_gate_contract_catches_non_wired_graph(tmp_path: Path) -> None:
    files = {
        gate.STATE_FILE: GOOD_STATE,
        gate.NODES_FILE: NON_WIRED_NODES,
        gate.GRAPH_FILE: gate.assemble_graph_yaml("alert-normalizer", ["Normalize", "Classify"]),
        gate.TEST_FILE: TRIVIAL_TEST,
    }
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed  # a trivially-passing test did NOT save it
    assert "wire" in contract.findings[0]["msg"].lower()
    assert not gate.all_passed(results)


async def test_gate_contract_requires_declared_output_populated(tmp_path: Path) -> None:
    # Only Normalize is wired, so nothing writes `label`; the fixture declares it
    # must be populated (null) — the run cannot claim an output it never produces.
    files = {
        gate.STATE_FILE: GOOD_STATE,
        gate.NODES_FILE: GOOD_NODES,
        gate.GRAPH_FILE: gate.assemble_graph_yaml("partial", ["Normalize"]),
        gate.TEST_FILE: TRIVIAL_TEST,
    }
    fixture = {"inputs": {"raw": "  ERROR  "}, "expects": {"label": None}}
    results = gate.run_full_gate(tmp_path / "g", files, fixture=fixture)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "populated" in contract.findings[0]["msg"].lower()


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="alert normalizer graph"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["graph_id"] == "alert-normalizer"
    assert out["node_classes"] == ["Normalize", "Classify"]


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="alert normalizer graph", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["graph_id"] == "alert-normalizer"
    assert pairs[0]["node_classes"] == ["Normalize", "Classify"]
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"

    bundle = out_dir / "alert_normalizer"
    assert final.landed_path == str(bundle / "graph.yaml")
    for name in ("state.py", "nodes.py", "graph.yaml", "test_nodes.py"):
        assert (bundle / name).exists()


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
        brief="a graph that normalizes and classifies alerts",
        failed_kind="contract",
        finding="second node ignored the normalized channel",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated sqlite store thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="normalize and classify alert graph"), CTX)
    assert out["recalled_lessons"]
    assert "normalized channel" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
