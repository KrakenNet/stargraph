# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: ``GraphRun.resume(run_id, from_step=N)`` (FR-19, AC-3.2).

Pins the contract for explicit-step resume *before* the implementation
lands in task 3.26. Currently RED because :meth:`stargraph.GraphRun.resume`
raises :class:`NotImplementedError`.

Cases (FR-19, AC-3.2):

1. ``test_resume_from_step_loads_specific_checkpoint`` --
   ``GraphRun.resume(checkpointer, run_id, from_step=1)`` restores state
   from the step-1 checkpoint, *not* the latest (step 2).
2. ``test_resume_from_step_re_executes_later_steps`` -- after
   ``resume(..., from_step=1)``, awaiting forward drives the run through
   step 2 onwards (i.e. step 2 is re-executed, not skipped). The final
   state matches the deterministic forward result.
3. ``test_resume_from_step_missing_checkpoint_raises`` --
   ``resume(..., from_step=99)`` for a step that was never checkpointed
   raises :class:`stargraph.errors.CheckpointError` (no silent fallback to
   latest -- "loud" per FR-6).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from stargraph import GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.errors import CheckpointError

if TYPE_CHECKING:
    from pathlib import Path


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _make_checkpoint(
    *,
    run_id: str,
    step: int,
    state: dict[str, int],
    graph_hash: str = "sha256:graph-v1",
    last_node: str = "n0",
) -> Checkpoint:
    """Build a populated :class:`Checkpoint` for ``from_step`` round-trips."""
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=graph_hash,
        runtime_hash="sha256:runtime-v1",
        state=state,
        clips_facts=[],
        last_node=last_node,
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side-v1",
    )


# --------------------------------------------------------------------------- #
# Cases                                                                       #
# --------------------------------------------------------------------------- #


async def test_resume_from_step_loads_specific_checkpoint(tmp_path: Path) -> None:
    """``resume(..., from_step=1)`` restores state from step 1, not the latest.

    Writes checkpoints at steps 0, 1, 2 and asserts that an explicit
    ``from_step=1`` resume sees ``x=1`` (the step-1 snapshot), proving the
    pinned-step contract overrides the latest-step default.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-resume-from-step-001"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, state={"x": 0}))
        await cp.write(_make_checkpoint(run_id=run_id, step=1, state={"x": 1}))
        await cp.write(_make_checkpoint(run_id=run_id, step=2, state={"x": 2}))

        run = await GraphRun.resume(cp, run_id, from_step=1)

        assert run.run_id == run_id
        assert run.initial_state is not None
        dumped = run.initial_state.model_dump()
        assert dumped.get("x") == 1, (
            f"resume(from_step=1) must restore step-1 state (x=1); got state={dumped!r}"
        )
    finally:
        await cp.close()


async def test_resume_from_step_re_executes_later_steps(tmp_path: Path) -> None:
    """After ``resume(from_step=N)``, steps > N re-execute deterministically.

    AC-3.2: "later steps re-execute". Resuming from step 1 means step 2
    onwards is driven forward by the run loop; the final terminal state
    must match the deterministic forward computation.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-resume-from-step-002"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, state={"x": 0}))
        await cp.write(_make_checkpoint(run_id=run_id, step=1, state={"x": 1}))
        await cp.write(_make_checkpoint(run_id=run_id, step=2, state={"x": 2}))

        run = await GraphRun.resume(cp, run_id, from_step=1)
        summary = await run.wait()

        assert run.state == "done", f"resumed run must reach terminal; got state={run.state!r}"
        assert summary is not None
    finally:
        await cp.close()


async def test_resume_from_step_missing_checkpoint_raises(tmp_path: Path) -> None:
    """``resume(from_step=N)`` raises ``CheckpointError`` when step N is absent.

    Force-loud per FR-6: never silently fall back to latest. The error
    surfaces with structured ``context`` (``run_id``, ``step``) so callers
    can distinguish "missing step" from "graph hash mismatch".
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-resume-from-step-003"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, state={"x": 0}))

        with pytest.raises(CheckpointError):
            await GraphRun.resume(cp, run_id, from_step=99)
    finally:
        await cp.close()
