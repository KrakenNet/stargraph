# SPDX-License-Identifier: Apache-2.0
"""Compile-gate harness -- judge aggregation + statistical gate math.

Replaces the print-driven script that shipped with the harness: same
mocked-judge setup, but with real assertions on the eval loop's
aggregation and on both directions of the promotion gate. Everything is
exercised through the public surface (``run_eval_loop`` /
``evaluate_gate``); numpy/scipy come from the ``stargraph[rl]`` extra,
so the module is import-skipped where they are absent.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

np = pytest.importorskip("numpy", reason="compile_gate needs numpy (stargraph[rl] extra)")
pytest.importorskip("scipy", reason="compile_gate needs scipy (stargraph[rl] extra)")

from stargraph.fathom.eval.compile_gate import CompileGateHarness, EvalResult  # noqa: E402

pytestmark = pytest.mark.unit

_N = 10


def _judge(success: bool) -> MagicMock:
    judge = MagicMock()
    block = MagicMock()
    block.text = json.dumps({"passed_criteria_count": 5, "is_absolute_success": success})
    judge.messages.create.return_value = MagicMock(content=[block])
    return judge


def _harness(judge: MagicMock) -> CompileGateHarness:
    scenarios = [{"id": f"scenario_{i}", "intent": "mock"} for i in range(_N)]
    with patch.object(CompileGateHarness, "_load_local_scenarios", return_value=scenarios):
        return CompileGateHarness(scenarios_path="unused.json", cross_judge_client=judge)


def _runner(scenario: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    return [{"role": "user", "content": scenario["id"]}], {"hub_a": False}


def _arm(name: str, successes: Any, hub_failures: Any) -> EvalResult:
    return EvalResult(
        arm_name=name,
        task_success=float(np.mean(successes)),
        routing_failures_per_hub={"hub_a": int(np.sum(hub_failures))},
        raw_success_vector=np.array(successes),
        raw_routing_failures={"hub_a": np.array(hub_failures)},
    )


def test_run_eval_loop_aggregates_judge_verdicts() -> None:
    result = _harness(_judge(success=True)).run_eval_loop("compiled", _runner)

    assert result.arm_name == "compiled"
    assert result.task_success == 1.0
    assert result.routing_failures_per_hub == {"hub_a": 0}
    assert result.raw_success_vector.shape == (_N,)
    assert result.raw_routing_failures["hub_a"].shape == (_N,)


def test_judge_rejection_and_judge_error_both_count_as_failure() -> None:
    rejected = _harness(_judge(success=False)).run_eval_loop("compiled", _runner)
    assert rejected.task_success == 0.0

    broken = _judge(success=True)
    broken.messages.create.side_effect = RuntimeError("judge unreachable")
    errored = _harness(broken).run_eval_loop("compiled", _runner)
    assert errored.task_success == 0.0


def test_gate_identical_arms_do_not_block() -> None:
    harness = _harness(_judge(success=True))
    baseline = _arm("baseline", np.ones(_N), np.zeros(_N))
    compiled = _arm("compiled", np.ones(_N), np.zeros(_N))

    block, metrics = harness.evaluate_gate(baseline, compiled)

    assert block is False
    assert metrics["block_promotion"] is False
    # Identical vectors short-circuit Wilcoxon to p=1.0.
    assert metrics["task_success_p_value"] == 1.0
    # All-ones data bootstraps to a degenerate (1.0, 1.0) interval.
    assert metrics["compiled_bootstrap_95_ci"] == (1.0, 1.0)


def test_gate_blocks_on_task_regression() -> None:
    harness = _harness(_judge(success=True))
    baseline = _arm("baseline", np.ones(_N), np.zeros(_N))
    compiled = _arm("compiled", np.zeros(_N), np.zeros(_N))

    block, metrics = harness.evaluate_gate(baseline, compiled)

    assert block is True
    assert metrics["task_success_p_value"] < 0.05


def test_gate_blocks_on_hub_regression_with_holm_correction() -> None:
    harness = _harness(_judge(success=True))
    # Task success identical; the compiled arm regresses on routing only.
    baseline = _arm("baseline", np.ones(_N), np.zeros(_N))
    compiled = _arm("compiled", np.ones(_N), np.ones(_N))

    block, metrics = harness.evaluate_gate(baseline, compiled)

    assert block is True
    report = metrics["hub_evaluation"]["hub_a"]
    assert report["significant_regression"] is True
    assert 0.0 <= report["adjusted_p"] <= 1.0
    assert report["adjusted_p"] >= report["raw_p"]


def test_gate_ignores_hub_improvement() -> None:
    harness = _harness(_judge(success=True))
    # Fewer failures than baseline: significant shift, but the right direction.
    baseline = _arm("baseline", np.ones(_N), np.ones(_N))
    compiled = _arm("compiled", np.ones(_N), np.zeros(_N))

    block, metrics = harness.evaluate_gate(baseline, compiled)

    assert block is False
    assert metrics["hub_evaluation"]["hub_a"]["significant_regression"] is False
