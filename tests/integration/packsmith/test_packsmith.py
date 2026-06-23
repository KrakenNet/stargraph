# SPDX-License-Identifier: Apache-2.0
"""Packsmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real in
every test — these assert the "always works" contract end-to-end: the contract tier
LOADS the generated CLIPS rules into a live Fathom engine, FIRES them on the fixture,
matches the action, and signs + verifies the assembled tree, so a pack whose rules
don't compile, don't fire, or produce the wrong action cannot land even when its own
unit test passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.packsmith import _ledger, gate
from stargraph.skills.packsmith.nodes.build import Build
from stargraph.skills.packsmith.nodes.recall import Recall
from stargraph.skills.packsmith.nodes.record import RecordBuild
from stargraph.skills.packsmith.nodes.triage import TriageGate
from stargraph.skills.packsmith.seeds import (
    _BUDGET_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _BUDGET_TEST,  # pyright: ignore[reportPrivateUsage]
    _RISK_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _RISK_RULES,  # pyright: ignore[reportPrivateUsage]
    _RISK_TEST,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.packsmith.seeds import (
    _BUDGET_RULES as _BUD_RULES,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.packsmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

GOOD_META = {
    "input_template": "alert.input",
    "output_template": "alert.action",
    "pack_name": "risk-escalation",
}
GOOD_GEN: dict[str, Any] = {
    "pack_name": "risk-escalation",
    "flavor": "governance",
    "input_template": "alert.input",
    "output_template": "alert.action",
    "rules_clp": _RISK_RULES,
    "fixture": _RISK_FIXTURE,
    "test_source": _RISK_TEST,
}

TRIVIAL_TEST = "def test_x() -> None:\n    assert True\n"

# Adversarial: only the escalate rule, so a low-risk input asserts NO action fact.
NO_FIRE_RULES = """\
(deftemplate alert.input
  (slot run_id)
  (slot risk_score (default 0)))

(deftemplate alert.action
  (slot run_id)
  (slot kind)
  (slot reason))

(defrule escalate-high-risk
  (alert.input (run_id ?r) (risk_score ?s&:(> ?s 7)))
  =>
  (assert (alert.action (run_id ?r) (kind "escalate") (reason "risk over threshold"))))
"""

# Syntax error → fails the contract compile; the repair loop can never fix a stub.
BAD_RULES = "(deftemplate alert.input (slot run_id)\n"  # unbalanced parens


def _files(
    *,
    rules: str,
    test: str,
    pack_name: str = "risk-escalation",
    output_template: str = "alert.action",
) -> dict[str, str]:
    return {
        gate.RULES_FILE: rules,
        gate.PACK_FILE: gate.assemble_pack_yaml(pack_name=pack_name, flavor="governance"),
        gate.MANIFEST_FILE: gate.assemble_manifest_yaml(
            pack_name=pack_name, output_template=output_template
        ),
        gate.TEST_FILE: test,
    }


def _contract(results: list[Any]) -> Any:
    return next(r for r in results if r.kind == "contract")


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable floor: rules compile + fire + cohere as a signed pack)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_pack(tmp_path: Path) -> None:
    files = _files(rules=_RISK_RULES, test=_RISK_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=_RISK_FIXTURE)
    assert gate.all_passed(results), [r.findings for r in results if not r.passed]


async def test_gate_passes_a_budget_pack(tmp_path: Path) -> None:
    files = _files(
        rules=_BUD_RULES,
        test=_BUDGET_TEST,
        pack_name="budget-guard",
        output_template="budget.decision",
    )
    meta = {
        "input_template": "budget.usage",
        "output_template": "budget.decision",
        "pack_name": "budget-guard",
    }
    results = gate.run_full_gate(tmp_path / "g", files, meta=meta, fixture=_BUDGET_FIXTURE)
    assert gate.all_passed(results), [r.findings for r in results if not r.passed]


async def test_gate_contract_catches_non_firing_rule(tmp_path: Path) -> None:
    # Only the escalate rule exists; a low-risk input asserts nothing.
    files = _files(rules=NO_FIRE_RULES, test=TRIVIAL_TEST)
    fixture = {"input": {"run_id": "r", "risk_score": 2}, "expects": {"kind": "escalate"}}
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=fixture)
    contract = _contract(results)
    assert not contract.passed
    assert "did not fire" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_wrong_action(tmp_path: Path) -> None:
    # Full rules, but a low-risk input fires "allow" while the fixture expects "escalate".
    files = _files(rules=_RISK_RULES, test=TRIVIAL_TEST)
    fixture = {"input": {"run_id": "r", "risk_score": 2}, "expects": {"kind": "escalate"}}
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=fixture)
    contract = _contract(results)
    assert not contract.passed
    assert "match" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_bad_clips(tmp_path: Path) -> None:
    files = _files(rules=BAD_RULES, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=_RISK_FIXTURE)
    contract = _contract(results)
    assert not contract.passed
    assert "compile" in contract.findings[0]["msg"].lower()


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="escalate risky alerts"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["pack_name"] == "risk-escalation"
    assert out["output_template"] == "alert.action"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    bad_gen: dict[str, Any] = {
        "pack_name": "broken",
        "flavor": "governance",
        "input_template": "a",
        "output_template": "b",
        "rules_clp": BAD_RULES,
        "fixture": {},
        "test_source": TRIVIAL_TEST,
    }
    out = await stub_build(Build, bad_gen).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="escalate risky alerts", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["pack_name"] == "risk-escalation"
    assert pairs[0]["flavor"] == "governance"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"

    bundle = out_dir / "risk_escalation"
    assert final.landed_path == str(bundle / "pack.yaml")
    for name in ("rules.clp", "pack.yaml", "manifest.yaml", "test_pack.py"):
        assert (bundle / name).exists()


async def test_failure_logs_lesson_and_records_no_pair() -> None:
    bad_gen: dict[str, Any] = {
        "pack_name": "broken",
        "flavor": "governance",
        "input_template": "a",
        "output_template": "b",
        "rules_clp": BAD_RULES,
        "fixture": {},
        "test_source": TRIVIAL_TEST,
    }
    await drive([stub_build(Build, bad_gen), RecordBuild()], State(brief="broken thing"))

    assert _ledger.load_trainset() == []  # no false-positive training data
    lessons = _ledger._read_jsonl(_ledger.home() / _ledger.LESSONS_FILE)  # pyright: ignore[reportPrivateUsage]
    assert any(le["failed_kind"] == "escalate" for le in lessons)


# --------------------------------------------------------------------------- #
# Reflexion recall + drift (idea 1 / idea 2 substrate)
# --------------------------------------------------------------------------- #
async def test_recall_surfaces_relevant_lesson() -> None:
    _ledger.append_lesson(
        brief="a governance pack that denies a run over its token budget",
        failed_kind="contract",
        finding="the deny rule never fired because the slot comparison was reversed",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated sqlite store thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="deny a run that exceeds its token budget"), CTX)
    assert out["recalled_lessons"]
    assert "deny rule never fired" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
