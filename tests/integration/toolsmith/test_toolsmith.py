# SPDX-License-Identifier: Apache-2.0
"""Toolsmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real
in every test — these assert the "always works" contract end-to-end: a bogus
passing test cannot get a tool with a schema-violating or crashing run recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.toolsmith import _ledger, gate
from stargraph.skills.toolsmith.nodes.build import Build
from stargraph.skills.toolsmith.nodes.recall import Recall
from stargraph.skills.toolsmith.nodes.record import RecordBuild
from stargraph.skills.toolsmith.nodes.triage import TriageGate
from stargraph.skills.toolsmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

GOOD_TOOL = """\
import ipaddress
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="cidr_summary",
    namespace="netutils",
    version="0.1.0",
    side_effects=SideEffects.none,
    description="Summarize an IPv4 CIDR block.",
)
def cidr_summary(*, cidr: str) -> dict[str, Any]:
    net = ipaddress.ip_network(cidr, strict=False)
    return {
        "network": str(net.network_address),
        "broadcast": str(net.broadcast_address),
        "num_addresses": int(net.num_addresses),
    }
"""
GOOD_TOOL_TEST = """\
from tool import cidr_summary


def test_cidr_summary():
    out = cidr_summary(cidr="10.0.0.0/24")
    assert out["network"] == "10.0.0.0"
    assert out["broadcast"] == "10.0.0.255"
    assert out["num_addresses"] == 256
"""
GOOD_GEN: dict[str, Any] = {
    "tool_name": "cidr_summary",
    "namespace": "netutils",
    "fixture": {"cidr": "10.0.0.0/24"},
    "tool_source": GOOD_TOOL,
    "test_source": GOOD_TOOL_TEST,
}

# Explicit output_schema requires "must_have", which the tool never returns.
SCHEMA_VIOLATION_TOOL = """\
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(
    name="bad_out",
    namespace="x",
    version="0.1.0",
    side_effects=SideEffects.none,
    output_schema={
        "type": "object",
        "required": ["must_have"],
        "properties": {"must_have": {"type": "string"}},
    },
)
def bad_out(*, v: int) -> dict[str, Any]:
    return {"other": v}
"""
CRASHING_TOOL = """\
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@tool(name="boom", namespace="x", version="0.1.0", side_effects=SideEffects.none)
def boom(*, v: int) -> dict:
    raise RuntimeError("boom")
"""
TRIVIAL_TEST = "def test_x():\n    assert True\n"

# Syntax error → fails every tier fast; the repair loop can never fix a constant stub.
BAD_GEN: dict[str, Any] = {
    "tool_name": "broken",
    "namespace": "x",
    "fixture": {},
    "tool_source": "def oops(:\n    pass\n",
    "test_source": TRIVIAL_TEST,
}


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable floor)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_tool(tmp_path: Path) -> None:
    files = {gate.TOOL_FILE: GOOD_TOOL, gate.TEST_FILE: GOOD_TOOL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture={"cidr": "10.0.0.0/24"})
    assert gate.all_passed(results)


async def test_gate_contract_catches_output_schema_violation(tmp_path: Path) -> None:
    files = {gate.TOOL_FILE: SCHEMA_VIOLATION_TOOL, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture={"v": 1})
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed  # a trivially-passing test did NOT save it
    assert not gate.all_passed(results)


async def test_gate_contract_catches_crashing_tool(tmp_path: Path) -> None:
    files = {gate.TOOL_FILE: CRASHING_TOOL, gate.TEST_FILE: TRIVIAL_TEST}
    results = gate.run_full_gate(tmp_path / "g", files, fixture={"v": 1})
    assert not next(r for r in results if r.kind == "contract").passed


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="cidr summarizer"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["tool_name"] == "cidr_summary"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="cidr summarizer", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["tool_name"] == "cidr_summary"
    assert pairs[0]["namespace"] == "netutils"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"
    assert final.landed_path
    assert (out_dir / "cidr_summary.py").exists()
    assert (out_dir / "test_cidr_summary.py").exists()


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
        brief="a tool that summarizes a cidr block",
        failed_kind="contract",
        finding="output missing num_addresses key",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated cron trigger thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="cidr summary tool"), CTX)
    assert out["recalled_lessons"]
    assert "num_addresses" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
