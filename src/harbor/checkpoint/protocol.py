# SPDX-License-Identifier: Apache-2.0
"""Checkpointer Protocol + Checkpoint/RunSummary Pydantic records (FR-16, design §3.2.1).

Defines the storage-driver contract that the engine runtime calls into
to persist and restore execution state. Drivers (aiosqlite-WAL,
asyncpg-pgbouncer-safe) implement :class:`Checkpointer` structurally;
no inheritance required.

The :class:`Checkpoint` record is the JCS-serializable snapshot the
runtime emits at each step boundary -- 12 fields covering identity
(``run_id``/``step``/``branch_id``/``parent_step_idx``), provenance
(``graph_hash``/``runtime_hash``/``parent_run_id``), state
(``state``/``clips_facts``/``last_node``/``next_action``), timing
(``timestamp``), and tool-output integrity (``side_effects_hash``).

:class:`RunSummary` is the lightweight row returned by ``list_runs`` for
inspect/CLI surfaces -- six fields, no state payload.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- pydantic resolves at runtime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

__all__ = [
    "Checkpoint",
    "Checkpointer",
    "RunSummary",
]


class Checkpoint(BaseModel):
    """Per-step execution snapshot persisted by the Checkpointer (design §3.2.1).

    All 12 fields are required at construction; ``branch_id``,
    ``parent_step_idx``, ``next_action``, and ``parent_run_id`` accept
    ``None`` for main-branch / leaf / original-run rows.
    """

    run_id: str
    step: int
    branch_id: str | None
    """Parallel branch identity; ``None`` = main."""
    parent_step_idx: int | None
    """Parent step for branched checkpoints."""
    graph_hash: str
    """May be derived (cf-prefix)."""
    runtime_hash: str
    state: dict[str, Any]
    """JCS-serializable snapshot."""
    clips_facts: list[Any]
    """``save_facts`` text-format output -- ``list[str]`` lines (FR-16) or
    legacy ``list[dict]`` rows; both serialize cleanly through orjson into
    the ``clips_facts`` JSONB column."""
    last_node: str
    next_action: dict[str, Any] | None
    timestamp: datetime
    parent_run_id: str | None
    """cf parent; ``None`` for original runs."""
    side_effects_hash: str
    """sha256 over recorded tool outputs."""


class RunSummary(BaseModel):
    """Lightweight run-level row returned by ``Checkpointer.list_runs``."""

    run_id: str
    graph_hash: str
    started_at: datetime
    last_step_at: datetime
    status: Literal["running", "done", "failed", "paused", "cancelled"]
    parent_run_id: str | None


class Checkpointer(Protocol):
    """Storage-driver contract (design §3.2.1).

    Implementations: :mod:`harbor.checkpoint.sqlite` (aiosqlite + WAL),
    :mod:`harbor.checkpoint.postgres` (asyncpg + pgbouncer-safe). Both
    arrive in subsequent tasks (1.20, 3.20).
    """

    async def bootstrap(self) -> None:
        """Idempotent schema/migration bootstrap."""
        ...

    async def write(self, checkpoint: Checkpoint) -> None:
        """Persist a :class:`Checkpoint`."""
        ...

    async def read_latest(self, run_id: str) -> Checkpoint | None:
        """Return the highest-``step`` checkpoint for ``run_id`` or ``None``."""
        ...

    async def read_at_step(self, run_id: str, step: int) -> Checkpoint | None:
        """Return the checkpoint at ``(run_id, step)`` or ``None``."""
        ...

    async def list_runs(
        self, *, since: datetime | None = None, limit: int = 100
    ) -> list[RunSummary]:
        """Return run summaries, optionally filtered by ``since`` cutoff."""
        ...
