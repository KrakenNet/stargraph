# SPDX-License-Identifier: Apache-2.0
"""``RunHistory`` -- coherent view over checkpoints + audit-log for replay (FR-27).

Per design §3.8.4, counterfactual replay loads a "history" snapshot of an
original run at a fixed cf-fork step ``N`` so it can:

1. Re-hydrate state[0..N-1] from cassettes (deterministic re-execution).
2. Apply the :class:`~stargraph.replay.counterfactual.CounterfactualMutation`
   at step ``N`` (mutate state, override node output, re-version rule pack).
3. Re-execute steps ``>=N`` against a *new* ``run_id`` so the original audit
   log remains byte-identical (Temporal "cannot change the past" invariant).

The class is intentionally a small read-only value: the heavy lifting
(replay of cassettes, dispatch into the run loop) lives in
:meth:`stargraph.GraphRun.counterfactual`. ``RunHistory`` just packages the
inputs so a future :func:`stargraph.compare` or CLI ``stargraph replay`` driver
can reuse the same loader without duplicating the IO path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from stargraph.checkpoint.protocol import Checkpoint

if TYPE_CHECKING:
    from pathlib import Path

    from stargraph.checkpoint.protocol import Checkpointer

__all__ = ["RunHistory"]


@dataclass(frozen=True, slots=True)
class RunHistory:
    """Read-only snapshot of an original run's checkpoints + audit-log path.

    Attributes:
        run_id: The original run's identifier.
        checkpoints: All checkpoints written by the original run, in
            ascending step order. Empty list when the run has no
            checkpoints (e.g. the loader is being used purely for the
            audit-log byte-identity invariant check).
        audit_path: On-disk path to the original audit-log file.
            ``None`` when the run was driven without an audit-log
            (in-memory tests, dry-runs).
    """

    run_id: str
    checkpoints: list[Checkpoint] = field(default_factory=list[Checkpoint])
    audit_path: Path | None = None

    @classmethod
    async def load(
        cls,
        run_id: str,
        *,
        checkpointer: Checkpointer,
        audit_path: Path | None = None,
    ) -> RunHistory:
        """Load a coherent :class:`RunHistory` snapshot for ``run_id``.

        Walks the checkpointer in step order until :meth:`Checkpointer.read_at_step`
        returns ``None``. The audit-log file (if any) is referenced by
        path; the bytes are *not* read here so a counterfactual fork can
        never accidentally hold a stale snapshot.

        Args:
            run_id: The original run's identifier.
            checkpointer: The :class:`stargraph.checkpoint.Checkpointer`
                instance bound to the persistent store.
            audit_path: Optional path to the original audit-log file.

        Returns:
            A fresh :class:`RunHistory` snapshot.
        """
        checkpoints: list[Checkpoint] = []
        step = 0
        while True:
            ckpt = await checkpointer.read_at_step(run_id, step)
            if ckpt is None:
                break
            checkpoints.append(ckpt)
            step += 1
        return cls(run_id=run_id, checkpoints=checkpoints, audit_path=audit_path)

    def state_at_step(self, step: int) -> Checkpoint | None:
        """Return the checkpoint at ``step``, or ``None`` if not present."""
        for ckpt in self.checkpoints:
            if ckpt.step == step:
                return ckpt
        return None
