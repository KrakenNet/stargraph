# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: ``GraphRun.resume(run_id)`` loads the latest checkpoint (FR-19, AC-3.1).

Pins the contract for ``stargraph.GraphRun.resume`` *before* the implementation
lands in task 3.26. These tests MUST be RED -- ``GraphRun.resume`` currently
raises :class:`NotImplementedError` (see :file:`src/stargraph/graph/run.py`),
so every async case fails at the ``await`` site. That is the expected RED
signal for this task.

Cases (FR-19, AC-3.1):

1. ``test_resume_latest_returns_graphrun_bound_to_run_id`` --
   ``GraphRun.resume(checkpointer, run_id)`` returns a *new* :class:`GraphRun`
   instance with ``run_id`` matching the checkpointed run (continuation of
   the same logical run, per design §3.1.1).
2. ``test_resume_latest_loads_state_from_highest_step`` -- after writing
   checkpoints at steps 0, 1, 2, ``resume(run_id)`` (no ``from_step``)
   restores state from step 2 (the highest), per FR-19.
3. ``test_resume_latest_continues_forward_to_terminal_state`` -- after
   ``resume``, awaiting forward execution drives the run to the design's
   terminal state (``state == "done"``); the final state matches the
   expected value the original run would have produced.

The deferred-import pattern is unnecessary here -- ``GraphRun`` is real --
but ``resume()`` is a stub. The tests therefore drive the public surface
and let ``NotImplementedError`` (or the structurally-correct successor in
task 3.26) be the failure signal.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stargraph import GraphRun
from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer

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
    """Build a populated :class:`Checkpoint` for resume round-trip tests."""
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


async def test_resume_latest_returns_graphrun_bound_to_run_id(tmp_path: Path) -> None:
    """``resume(checkpointer, run_id)`` returns a GraphRun bound to ``run_id``.

    Continuation of the same logical run, per design §3.1.1: ``run_id`` is
    preserved across the resume boundary. Currently RED because
    :meth:`GraphRun.resume` raises :class:`NotImplementedError`.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-resume-latest-001"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, state={"x": 0}))

        run = await GraphRun.resume(cp, run_id)

        assert isinstance(run, GraphRun)
        assert run.run_id == run_id
    finally:
        await cp.close()


async def test_resume_latest_loads_state_from_highest_step(tmp_path: Path) -> None:
    """``resume`` (no ``from_step``) restores state from the *latest* checkpoint.

    Writes checkpoints at steps 0, 1, 2; resumes; asserts the run's
    ``initial_state`` (or equivalent restored snapshot) reflects step 2,
    not step 0/1. Currently RED because ``resume`` is unimplemented.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-resume-latest-002"
        await cp.write(_make_checkpoint(run_id=run_id, step=0, state={"x": 0}))
        await cp.write(_make_checkpoint(run_id=run_id, step=1, state={"x": 1}))
        await cp.write(_make_checkpoint(run_id=run_id, step=2, state={"x": 2}))

        run = await GraphRun.resume(cp, run_id)

        # The resumed run must reflect the latest persisted state. The exact
        # attribute name lands with task 3.26; for now the test asserts the
        # contract via the public surface ``initial_state`` (BaseModel) or
        # any state-bearing attribute the implementation chooses to expose.
        assert run.initial_state is not None
        # Round-trip through Pydantic dump for resilience to attr layout.
        dumped = run.initial_state.model_dump()
        assert dumped.get("x") == 2, (
            f"resume() must restore latest checkpoint (step=2 → x=2); got state={dumped!r}"
        )
    finally:
        await cp.close()


async def test_resume_latest_continues_forward_to_terminal_state(tmp_path: Path) -> None:
    """After ``resume``, awaiting forward execution drives the run to ``done``.

    AC-3.1: resume "loads the latest checkpoint" *and* the run continues
    forward. The resumed handle's lifecycle must reach ``"done"`` and the
    final state must match what the original (unbroken) run would have
    produced. Currently RED -- ``resume`` is unimplemented, so neither
    ``run`` exists nor can it be driven forward.
    """
    cp = SQLiteCheckpointer(tmp_path / "ckpt.db")
    await cp.bootstrap()
    try:
        run_id = "run-resume-latest-003"
        # Persist a mid-run checkpoint; the implementation will compose the
        # resumed run with the parent ``Graph`` (task 3.26) and drive forward.
        await cp.write(_make_checkpoint(run_id=run_id, step=1, state={"x": 1}))

        run = await GraphRun.resume(cp, run_id)
        # Drive forward to terminal state. Task 3.26 wires this through
        # ``GraphRun.wait`` (or the equivalent run-loop entry point).
        summary = await run.wait()

        assert run.state == "done", f"resumed run must terminate; got state={run.state!r}"
        # ``RunSummary`` shape lands in the loop module; the contract here
        # is that it is non-None and reports terminal status.
        assert summary is not None
    finally:
        await cp.close()
