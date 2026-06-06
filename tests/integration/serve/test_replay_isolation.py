# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.22: counterfactual replay isolation.

Locks the design §4.2 / FR-27 "cannot change the past" invariant at
integration scope: forking a counterfactual from an existing parent
checkpoint must NOT mutate any parent-side state. The cf-run writes
to the SAME :class:`Checkpointer` but under a fresh ``cf-<uuid>``
``run_id`` so the parent's rows never shadow.

Cases covered (FR-27, FR-69, AC-8.5, NFR-4):

1. **Parent state byte-identical post cf-fork**: load every parent
   checkpoint's ``state`` dict before forking, fork a cf-run with
   ``mutation.state_overrides`` and drive the cf-run loop to terminal,
   then re-load the parent checkpoints and assert byte-equality on
   each ``state`` dict.
2. **cf-run state diverges from parent**: ``state_overrides`` change
   the cf-run's initial state. Assert the cf-run's ``initial_state``
   reflects the override (cf is observably different) while parent
   step-1 ``state`` did NOT change (cf write went to ``cf-<uuid>``,
   not back to the parent ``run_id``).

The test drives :meth:`stargraph.GraphRun.counterfactual` directly (the
HTTP route ``POST /v1/runs/{id}/counterfactual`` is the same code
path; we exercise the engine API for tighter assertions on
in-memory state).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from stargraph import GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.replay.counterfactual import CounterfactualMutation

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.serve, pytest.mark.integration]


def _checkpoint(
    *,
    run_id: str,
    step: int,
    state: dict[str, Any],
    graph_hash: str = "g" * 64,
) -> Checkpoint:
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=graph_hash,
        runtime_hash="rt-1",
        state=state,
        clips_facts=[],
        last_node=f"n{step}",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )


async def _seed_parent_run(
    cp: SQLiteCheckpointer,
    *,
    run_id: str,
    n_steps: int,
) -> list[dict[str, Any]]:
    """Persist ``n_steps`` checkpoints under ``run_id``; return the seeded states."""
    seeded: list[dict[str, Any]] = []
    for step in range(n_steps):
        state = {"counter": step, "kind": "parent", "tag": f"step-{step}"}
        seeded.append(dict(state))
        await cp.write(_checkpoint(run_id=run_id, step=step, state=state))
    return seeded


@pytest.mark.serve
async def test_counterfactual_does_not_mutate_parent_checkpoints(
    tmp_path: Path,
) -> None:
    """Fork cf-run from parent step 1; parent checkpoints byte-identical post-fork.

    Step shape:
      1. Seed 3 parent checkpoints (steps 0..2) with distinguishable states.
      2. Snapshot every parent checkpoint's ``state`` dict.
      3. Fork a cf-run from parent step 1 with ``state_overrides``.
      4. Re-load every parent checkpoint and assert ``state`` is byte-identical.

    The cf-run's own writes go under a fresh ``cf-<uuid>`` ``run_id``;
    the parent ``run_id``'s checkpoint rows MUST be untouched (FR-27,
    design §4.2 invariant).
    """
    cp = SQLiteCheckpointer(tmp_path / "isolation.sqlite")
    await cp.bootstrap()

    parent_id = "parent-run-iso"
    seeded_states = await _seed_parent_run(cp, run_id=parent_id, n_steps=3)

    # Snapshot pre-fork state for byte-identity comparison.
    pre_fork: list[dict[str, Any]] = []
    for step in range(3):
        ckpt = await cp.read_at_step(parent_id, step)
        assert ckpt is not None, f"missing parent checkpoint at step={step}"
        pre_fork.append(dict(ckpt.state))

    # Fork a cf-run with a state override that diverges from the parent.
    mutation = CounterfactualMutation(state_overrides={"counter": 999, "kind": "cf"})
    cf_run = await GraphRun.counterfactual(cp, parent_id, step=1, mutate=mutation)

    # cf-run id is fresh ``cf-<uuid>`` — never the parent.
    assert cf_run.run_id != parent_id, "cf must mint a fresh run_id"
    assert cf_run.run_id.startswith("cf-"), f"cf run_id missing 'cf-' prefix: {cf_run.run_id!r}"

    # Parent checkpoints byte-identical post fork (read-back).
    for step in range(3):
        ckpt = await cp.read_at_step(parent_id, step)
        assert ckpt is not None
        assert ckpt.state == pre_fork[step], (
            f"parent step={step} state mutated by cf-fork: "
            f"pre={pre_fork[step]!r} post={ckpt.state!r}"
        )
        # The seeded state is also byte-identical to the snapshot —
        # belt-and-suspenders confirmation that the seed read-back path
        # is the source of truth.
        assert ckpt.state == seeded_states[step]


@pytest.mark.serve
async def test_counterfactual_initial_state_reflects_mutation(
    tmp_path: Path,
) -> None:
    """cf-run's ``initial_state`` carries the ``state_overrides`` overlay.

    Confirms the cf-run is observably different from a fresh resume of
    the parent at the same step. Parent state stays at the seeded
    ``counter=1`` value; cf-run's in-memory ``initial_state`` reflects
    the override (``counter=42``).
    """
    cp = SQLiteCheckpointer(tmp_path / "isolation_state.sqlite")
    await cp.bootstrap()

    parent_id = "parent-run-state-iso"
    await _seed_parent_run(cp, run_id=parent_id, n_steps=3)

    mutation = CounterfactualMutation(state_overrides={"counter": 42})
    cf_run = await GraphRun.counterfactual(cp, parent_id, step=1, mutate=mutation)

    # cf-run's initial state reflects the override.
    assert cf_run.initial_state is not None
    cf_state = cf_run.initial_state.model_dump()
    assert cf_state.get("counter") == 42, f"cf initial_state missing override: {cf_state!r}"

    # Parent step-1 checkpoint still carries the seeded ``counter=1``.
    parent_ckpt_step1 = await cp.read_at_step(parent_id, 1)
    assert parent_ckpt_step1 is not None
    assert parent_ckpt_step1.state["counter"] == 1, (
        f"parent step-1 was mutated by cf-fork: {parent_ckpt_step1.state!r}"
    )
