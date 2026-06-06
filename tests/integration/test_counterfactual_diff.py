# SPDX-License-Identifier: Apache-2.0
"""Integration: ``stargraph.compare`` returns a RunDiff (FR-27, AC-4.4).

Per design §3.8.6, two runs that share steps 0..N-1 but diverge at
step N must produce a :class:`RunDiff` whose ``steps`` lists the
divergent step with ``diverged_at == "state"`` (state mutation
precedence) and whose ``state_diff`` is a non-empty JSONPatch RFC 6902
op-list. The diff also surfaces ``derived_hash`` (the cf-side
graph_hash) and ``final_state_diff`` between the last checkpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from stargraph.checkpoint import Checkpoint
from stargraph.replay.compare import RunDiff, StepDiff, compare
from stargraph.replay.history import RunHistory


def _ckpt(
    *,
    run_id: str,
    step: int,
    state: dict[str, Any],
    last_node: str = "n",
    graph_hash: str = "0" * 64,
    side_effects_hash: str = "0" * 64,
) -> Checkpoint:
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=graph_hash,
        runtime_hash="r" * 64,
        state=state,
        clips_facts=[],
        last_node=last_node,
        next_action=None,
        timestamp=datetime(2026, 4, 28, tzinfo=UTC),
        parent_run_id=None,
        side_effects_hash=side_effects_hash,
    )


def test_compare_lists_per_step_diff_with_diverged_at_state() -> None:
    """Two runs diverge at step 2 -- RunDiff must capture it (AC-4.4)."""
    orig_hash = "a" * 64
    cf_hash = "b" * 64

    orig = RunHistory(
        run_id="orig-001",
        checkpoints=[
            _ckpt(run_id="orig-001", step=0, state={"x": 1}, graph_hash=orig_hash),
            _ckpt(run_id="orig-001", step=1, state={"x": 2}, graph_hash=orig_hash),
            _ckpt(run_id="orig-001", step=2, state={"x": 3}, graph_hash=orig_hash),
        ],
    )
    cf = RunHistory(
        run_id="cf-001",
        checkpoints=[
            _ckpt(run_id="cf-001", step=0, state={"x": 1}, graph_hash=cf_hash),
            _ckpt(run_id="cf-001", step=1, state={"x": 2}, graph_hash=cf_hash),
            _ckpt(run_id="cf-001", step=2, state={"x": 99}, graph_hash=cf_hash),
        ],
    )

    diff = compare(orig, cf)

    assert isinstance(diff, RunDiff)
    assert diff.original_run_id == "orig-001"
    assert diff.counterfactual_run_id == "cf-001"
    assert diff.derived_hash == cf_hash

    # Only step 2 should appear (steps 0 and 1 match exactly).
    assert len(diff.steps) == 1
    sd = diff.steps[0]
    assert isinstance(sd, StepDiff)
    assert sd.step == 2
    assert sd.diverged_at == "state"

    # JSONPatch RFC 6902: replace /x from 3 -> 99.
    assert sd.state_diff == [{"op": "replace", "path": "/x", "value": 99}]

    # Final state diff mirrors the only diverging step here.
    assert diff.final_state_diff == [{"op": "replace", "path": "/x", "value": 99}]
    assert diff.final_status_diff is None  # last_node identical


def test_compare_identifies_node_output_divergence() -> None:
    """When state matches but ``last_node`` differs, diverged_at == node_output."""
    orig = RunHistory(
        run_id="orig-002",
        checkpoints=[
            _ckpt(run_id="orig-002", step=0, state={"x": 1}, last_node="alpha"),
        ],
    )
    cf = RunHistory(
        run_id="cf-002",
        checkpoints=[
            _ckpt(run_id="cf-002", step=0, state={"x": 1}, last_node="beta"),
        ],
    )

    diff = compare(orig, cf)
    assert len(diff.steps) == 1
    assert diff.steps[0].diverged_at == "node_output"
    assert diff.steps[0].state_diff == []
    assert diff.steps[0].output_diff == {"orig": "alpha", "cf": "beta"}
    assert diff.final_status_diff == ("alpha", "beta")
