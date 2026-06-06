# SPDX-License-Identifier: Apache-2.0
"""Phase 1 POC checkpoint: end-to-end Fathom adapter smoke test (FR-33).

Drives a fresh ``fathom.Engine`` through ``FathomAdapter`` end-to-end:

1. Loads five rules from ``tests/fixtures/fathom/rules/poc_5rules.yaml`` (one
   per Stargraph action verb in {goto, halt, parallel, retry, assert}, with one
   ``when`` clause referencing the provenance slot ``_source``).
2. Asserts a single ``evidence`` fact via :py:meth:`FathomAdapter.assert_with_provenance`
   carrying a populated :class:`stargraph.fathom.ProvenanceBundle`.
3. Calls :py:meth:`FathomAdapter.evaluate`, which fires the ruleset and reads
   the resulting ``stargraph_action`` facts back through :func:`stargraph.fathom.extract_actions`.
4. Asserts the exact ordered Action sequence (descending salience) and prints
   ``POC ALL GREEN`` so the verify gate (``grep -q``) can detect success.

Maps directly to AC-17.1 -- AC-17.4 (POC checkpoint acceptance).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from stargraph.fathom import (
    AssertAction,
    FathomAdapter,
    GotoAction,
    HaltAction,
    ParallelAction,
    ProvenanceBundle,
    RetryAction,
)

RULES_PATH: Path = (
    Path(__file__).parent.parent / "fixtures" / "fathom" / "rules" / "poc_5rules.yaml"
)


def test_poc_smoke_five_rules_all_green(adapter: FathomAdapter) -> None:
    """Five rules, one provenance-bearing fact, five ordered Stargraph actions."""
    # Load the five-rule ruleset against the pre-seeded engine. Templates and
    # the ``poc`` module are wired in the ``adapter`` fixture (see conftest.py).
    adapter.engine.load_rules(str(RULES_PATH))

    # Build a complete provenance bundle. ``_source = "test"`` is what the
    # ``rule-goto-on-source`` rule reads in its ``when`` clause -- this proves
    # provenance slots survive sanitization and reach CLIPS pattern matching.
    provenance: ProvenanceBundle = {
        "origin": "user",
        "source": "test",
        "run_id": "r-test1",
        "step": 0,
        "confidence": Decimal("1.0"),
        "timestamp": datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    }

    # One assertion triggers all five rules (each matches ``field = "phase"``).
    adapter.assert_with_provenance(
        template="evidence",
        slots={"field": "phase", "value": "poc"},
        provenance=provenance,
    )

    actions = adapter.evaluate()

    # Five rules → five actions. CLIPS fires by descending salience (50, 40,
    # 30, 20, 10) so query order is the assertion order, which matches the
    # rule ordering in poc_5rules.yaml.
    assert len(actions) == 5, f"expected 5 actions, got {len(actions)}: {actions!r}"

    kinds = [a.kind for a in actions]
    assert kinds == ["goto", "halt", "parallel", "retry", "assert"], (
        f"unexpected kind sequence: {kinds!r}"
    )

    # Per-action structural checks: every variant carries the slots set in
    # the rule RHS and round-trips correctly through ``extract_actions``.
    goto, halt, parallel, retry, assert_action = actions
    assert isinstance(goto, GotoAction) and goto.target == "node_a"
    assert isinstance(halt, HaltAction) and halt.reason == "done"
    assert isinstance(parallel, ParallelAction)
    assert parallel.targets == ["a", "b", "c"]
    assert parallel.join == "join_node"
    assert parallel.strategy == "all"
    assert isinstance(retry, RetryAction)
    assert retry.target == "foo" and retry.backoff_ms == 100
    assert isinstance(assert_action, AssertAction)
    assert assert_action.fact == "marker"
    assert assert_action.slots == '{"k":"v"}'

    # Verify gate: this exact phrase is what the task's verify command greps.
    print("POC ALL GREEN")
