# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed packs — the trainset cold start.

Each entry is a verified ``(brief → rules.clp)`` pair: a CLIPS ``rules.clp`` (two
deftemplates + the defrules that assert a decision), its ``test_pack.py``, and the
``fixture`` the contract tier fires the loaded engine against. Both are governance
packs:

- Seed 1 escalates an alert whose ``risk_score`` crosses a threshold (else allows).
- Seed 2 denies a run that has spent its budget (else allows).

Both load into a real Fathom engine, fire on the fixture input to assert the expected
action, and sign + verify as a coherent tree — so the contract tier only passes if the
pack actually compiles, fires, and coheres. ``id`` is a fixed literal so
``seed_trainset`` is idempotent.

``tests/integration/packsmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any

# --- Seed 1: escalate high-risk alerts (governance) -------------------------- #
_RISK_RULES = """\
; Governance pack: escalate alerts whose risk crosses a threshold, else allow.
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

(defrule allow-low-risk
  (alert.input (run_id ?r) (risk_score ?s&:(<= ?s 7)))
  =>
  (assert (alert.action (run_id ?r) (kind "allow") (reason "risk within bounds"))))
"""

_RISK_TEST = """\
from pathlib import Path

from fathom import Engine


def _engine() -> Engine:
    eng = Engine(default_decision="deny")
    eng._env.load(str(Path(__file__).with_name("rules.clp")))
    return eng


def _kinds(eng: Engine) -> list[str]:
    eng._env.run()
    return [dict(f)["kind"] for f in eng._env.find_template("alert.action").facts()]


def test_escalates_high_risk() -> None:
    eng = _engine()
    eng._env.assert_string('(alert.input (run_id "r1") (risk_score 9))')
    assert "escalate" in _kinds(eng)


def test_allows_low_risk() -> None:
    eng = _engine()
    eng._env.assert_string('(alert.input (run_id "r2") (risk_score 2))')
    assert "allow" in _kinds(eng)
"""

_RISK_FIXTURE: dict[str, Any] = {
    "input": {"run_id": "r1", "risk_score": 9},
    "expects": {"kind": "escalate"},
}

# --- Seed 2: deny over-budget runs (governance) ------------------------------ #
_BUDGET_RULES = """\
; Governance pack: deny a run that has spent its budget, else allow.
(deftemplate budget.usage
  (slot run_id)
  (slot spent (default 0))
  (slot allowed (default 0)))

(deftemplate budget.decision
  (slot run_id)
  (slot kind)
  (slot reason))

(defrule deny-over-budget
  (budget.usage (run_id ?r) (spent ?s) (allowed ?a&:(>= ?s ?a)))
  =>
  (assert (budget.decision (run_id ?r) (kind "deny") (reason "budget exhausted"))))

(defrule allow-under-budget
  (budget.usage (run_id ?r) (spent ?s) (allowed ?a&:(< ?s ?a)))
  =>
  (assert (budget.decision (run_id ?r) (kind "allow") (reason "budget available"))))
"""

_BUDGET_TEST = """\
from pathlib import Path

from fathom import Engine


def _engine() -> Engine:
    eng = Engine(default_decision="deny")
    eng._env.load(str(Path(__file__).with_name("rules.clp")))
    return eng


def _kinds(eng: Engine) -> list[str]:
    eng._env.run()
    return [dict(f)["kind"] for f in eng._env.find_template("budget.decision").facts()]


def test_denies_over_budget() -> None:
    eng = _engine()
    eng._env.assert_string('(budget.usage (run_id "r1") (spent 150) (allowed 100))')
    assert "deny" in _kinds(eng)


def test_allows_under_budget() -> None:
    eng = _engine()
    eng._env.assert_string('(budget.usage (run_id "r2") (spent 10) (allowed 100))')
    assert "allow" in _kinds(eng)
"""

_BUDGET_FIXTURE: dict[str, Any] = {
    "input": {"run_id": "r1", "spent": 150, "allowed": 100},
    "expects": {"kind": "deny"},
}


def _pair(
    seed_id: str,
    brief: str,
    pack_name: str,
    input_template: str,
    output_template: str,
    rules_clp: str,
    test_source: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "pack_name": pack_name,
        "flavor": "governance",
        "input_template": input_template,
        "output_template": output_template,
        "rules_clp": rules_clp,
        "test_source": test_source,
        "fixture": fixture,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "d0010000001",
        "a governance pack that escalates alerts whose risk score exceeds 7, else allows them",
        "risk-escalation",
        "alert.input",
        "alert.action",
        _RISK_RULES,
        _RISK_TEST,
        _RISK_FIXTURE,
    ),
    _pair(
        "d0010000002",
        "a governance pack that denies a run when its spend reaches its budget, else allows",
        "budget-guard",
        "budget.usage",
        "budget.decision",
        _BUDGET_RULES,
        _BUDGET_TEST,
        _BUDGET_FIXTURE,
    ),
]
