# SPDX-License-Identifier: Apache-2.0
"""Integration: ``stargraph.bosun.budgets@1.0`` round-trip (FR-34, design §7.1).

Loads the pack rules into a fresh Fathom engine, asserts synthetic
``bosun.budget`` facts, runs the rule engine to quiescence, and reads
the resulting ``bosun.violation`` facts. Three cases cover the design
contract:

1. **Negative**: budget under allowance → no violations.
2. **Token overrun**: ``consumed >= allowed`` for ``kind="tokens"`` →
   one ``bosun.violation severity=halt kind=budget-exhausted``.
3. **Cost overrun**: same shape for ``kind="cost"`` (proves the rule
   set covers each kind, not just one).
"""

from __future__ import annotations

import pytest
from fathom import Engine

from ._helpers import load_pack_rules

pytestmark = pytest.mark.serve


def _fresh_engine() -> Engine:
    eng = Engine(default_decision="deny")
    load_pack_rules(eng, "budgets")
    return eng


def test_budget_under_allowance_emits_no_violation() -> None:
    """Consumed below allowed → no ``bosun.violation``."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(bosun.budget (kind "tokens") (allowed 100) (consumed 50) (run_id "r-under"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = list(eng._env.find_template("bosun.violation").facts())  # pyright: ignore[reportPrivateUsage]
    assert viols == [], f"expected no violations under-budget; got {[dict(v) for v in viols]}"


def test_budget_token_exhaustion_emits_halt_violation() -> None:
    """Tokens consumed >= allowed → one halt-severity violation."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(bosun.budget (kind "tokens") (allowed 100) (consumed 150) (run_id "r-tokens"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = [dict(v) for v in eng._env.find_template("bosun.violation").facts()]  # pyright: ignore[reportPrivateUsage]
    assert len(viols) == 1, f"expected exactly one violation; got {viols}"
    v = viols[0]
    assert v["kind"] == "budget-exhausted"
    assert v["severity"] == "halt"
    assert v["run_id"] == "r-tokens"
    assert "token" in v["reason"].lower()


def test_budget_cost_exhaustion_emits_halt_violation() -> None:
    """Cost consumed >= allowed → one halt-severity violation (cost branch)."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(bosun.budget (kind "cost") (allowed 10) (consumed 25) (run_id "r-cost"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = [dict(v) for v in eng._env.find_template("bosun.violation").facts()]  # pyright: ignore[reportPrivateUsage]
    assert len(viols) == 1, f"expected exactly one violation; got {viols}"
    v = viols[0]
    assert v["kind"] == "budget-exhausted"
    assert v["severity"] == "halt"
    assert v["run_id"] == "r-cost"
    assert "cost" in v["reason"].lower()


def test_budget_latency_exhaustion_emits_halt_violation() -> None:
    """Latency consumed >= allowed → one halt-severity violation."""
    eng = _fresh_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(bosun.budget (kind "latency") (allowed 1000) (consumed 1500) (run_id "r-lat"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    viols = [dict(v) for v in eng._env.find_template("bosun.violation").facts()]  # pyright: ignore[reportPrivateUsage]
    assert len(viols) == 1
    assert viols[0]["kind"] == "budget-exhausted"
    assert "latency" in viols[0]["reason"].lower()
