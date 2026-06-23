# SPDX-License-Identifier: Apache-2.0
"""Skillsmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real in
every test — these assert the composite "always works" contract end-to-end: the
contract tier LOADS + RUNS the assembled subgraph AND constructs the ``Skill``
manifest, so a bundle whose nodes don't wire, or whose manifest is not a valid
registerable skill (bad kind, replay-unsafe ``set`` state), cannot land even when
its own unit test passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.skillsmith import _ledger, gate
from stargraph.skills.skillsmith.nodes.build import Build
from stargraph.skills.skillsmith.nodes.recall import Recall
from stargraph.skills.skillsmith.nodes.record import RecordBuild
from stargraph.skills.skillsmith.nodes.triage import TriageGate
from stargraph.skills.skillsmith.seeds import (
    _TRIAGE_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _TRIAGE_NODES,  # pyright: ignore[reportPrivateUsage]
    _TRIAGE_STATE,  # pyright: ignore[reportPrivateUsage]
    _TRIAGE_TEST,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.skillsmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

# The good bundle (seed 1: normalize → classify workflow, properly wired).
GOOD_STATE = _TRIAGE_STATE
GOOD_NODES = _TRIAGE_NODES
GOOD_TEST = _TRIAGE_TEST
GOOD_FIXTURE = _TRIAGE_FIXTURE
GOOD_GEN: dict[str, Any] = {
    "skill_name": "alert-triage",
    "kind": "workflow",
    "description": "Normalize an alert string and label it alert or ok.",
    "node_classes": ["Normalize", "Classify"],
    "state_source": GOOD_STATE,
    "nodes_source": GOOD_NODES,
    "requires": [],
    "system_prompt": "",
    "fixture": GOOD_FIXTURE,
    "test_source": GOOD_TEST,
}

# Adversarial subgraph: Classify reads RAW (case-sensitively), not the `normalized`
# channel Normalize wrote — the nodes never connect, so the run yields label="ok".
NON_WIRED_NODES = """\
from __future__ import annotations

from typing import Any

from stargraph.nodes.base import NodeBase


class Normalize(NodeBase):
    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {"normalized": str(state.raw).strip().lower()}


class Classify(NodeBase):
    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {"label": "alert" if "error" in state.raw else "ok"}
"""

# Adversarial state: a `set`-typed field — the graph runs, but the Skill manifest
# validator rejects it (NFR-2: replay-safe state must use frozenset).
SET_STATE = """\
from __future__ import annotations

from pydantic import BaseModel, Field


class State(BaseModel):
    raw: str = ""
    normalized: str = ""
    label: str = ""
    tags: set[str] = Field(default_factory=set)
"""

TRIVIAL_TEST = "def test_x() -> None:\n    assert True\n"

# Syntax error → fails the static tier fast; the repair loop can never fix a stub.
BAD_GEN: dict[str, Any] = {
    "skill_name": "broken",
    "kind": "workflow",
    "description": "broken",
    "node_classes": ["Oops"],
    "state_source": GOOD_STATE,
    "nodes_source": "class Oops(:\n    pass\n",
    "requires": [],
    "system_prompt": "",
    "fixture": {},
    "test_source": TRIVIAL_TEST,
}


def _files(
    *,
    state: str,
    nodes: str,
    test: str,
    skill_name: str = "alert-triage",
    node_classes: list[str] | None = None,
    kind: str = "workflow",
    description: str = "desc",
    requires: list[str] | None = None,
    system_prompt: str = "",
) -> dict[str, str]:
    classes = node_classes if node_classes is not None else ["Normalize", "Classify"]
    return {
        gate.STATE_FILE: state,
        gate.NODES_FILE: nodes,
        gate.GRAPH_FILE: gate.assemble_graph_yaml(skill_name, classes),
        gate.MANIFEST_FILE: gate.assemble_manifest_yaml(
            skill_name, kind, description, requires or [], system_prompt
        ),
        gate.TEST_FILE: test,
    }


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable composite floor: subgraph runs + manifest is a valid skill)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_skill(tmp_path: Path) -> None:
    files = _files(state=GOOD_STATE, nodes=GOOD_NODES, test=GOOD_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    assert gate.all_passed(results), [r.findings for r in results if not r.passed]


async def test_gate_contract_catches_non_wired_subgraph(tmp_path: Path) -> None:
    files = _files(state=GOOD_STATE, nodes=NON_WIRED_NODES, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "wire" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_invalid_kind(tmp_path: Path) -> None:
    # Subgraph runs fine, but "daemon" is not a SkillKind → the manifest is not a
    # valid, registerable Skill.
    files = _files(state=GOOD_STATE, nodes=GOOD_NODES, test=TRIVIAL_TEST, kind="daemon")
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "skill" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_replay_unsafe_set_state(tmp_path: Path) -> None:
    # Subgraph runs fine, but a `set`-typed state field fails the Skill validator.
    files = _files(state=SET_STATE, nodes=GOOD_NODES, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "frozenset" in contract.findings[0]["msg"].lower()


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="alert triage skill"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["skill_name"] == "alert-triage"
    assert out["kind"] == "workflow"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="alert triage skill", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["skill_name"] == "alert-triage"
    assert pairs[0]["kind"] == "workflow"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"

    bundle = out_dir / "alert_triage"
    assert final.landed_path == str(bundle / "manifest.yaml")
    for name in ("state.py", "nodes.py", "graph.yaml", "manifest.yaml", "test_nodes.py"):
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
        brief="a workflow skill that normalizes and classifies alerts",
        failed_kind="contract",
        finding="manifest declared kind agent but had no system_prompt",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated sqlite store thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="normalize and classify alert skill"), CTX)
    assert out["recalled_lessons"]
    assert "system_prompt" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
