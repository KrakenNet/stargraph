# SPDX-License-Identifier: Apache-2.0
""":class:`Scheduler` value types + Protocol + module constants (design §6.1, §6.2).

Side-effect-free pieces extracted from :mod:`stargraph.serve.scheduler` so the
scheduler module stays focused on the cron / dispatcher loop machinery. These
are pure data carriers (no behavior beyond ``__init__`` field assignment), a
structural Protocol, and two timing/concurrency constants.

Everything here is re-exported from :mod:`stargraph.serve.scheduler` so the
established import surface is unchanged -- :mod:`stargraph.serve.history`
imports :class:`PendingRun`, the FastAPI route + manual-trigger tests import
:class:`EnqueueHandle`, and ``__all__`` on the scheduler module lists
:class:`PendingStore` / :class:`QueueItem`.

Design refs: §6.1 (durable pending row), §6.2 (per-``graph_hash`` concurrency).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple, Protocol, runtime_checkable

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Mapping
    from datetime import datetime

    from stargraph.checkpoint.protocol import RunSummary


__all__ = [
    "_CRON_POLL_INTERVAL_S",
    "_DEFAULT_GRAPH_CONCURRENCY",
    "EnqueueHandle",
    "PendingRun",
    "PendingStore",
    "QueueItem",
]


#: Default per-``graph_hash`` concurrency limit. The IR has no
#: ``concurrency_limit`` field yet (design §6.2 expects one); ``1`` keeps
#: per-graph runs sequential -- safe POC default. Phase 2 reads
#: ``graph.concurrency_limit`` from the parent IR (task 2.22+).
_DEFAULT_GRAPH_CONCURRENCY = 1

#: Cron-loop polling interval. NFR-3 mandates ±100ms scheduler precision;
#: 50ms gives a 2x margin so the worst-case "fire missed by one tick"
#: case still lands well inside the budget.
_CRON_POLL_INTERVAL_S = 0.05


class EnqueueHandle(NamedTuple):
    """Result of :meth:`Scheduler.enqueue`: ``(run_id, future)``.

    ``run_id`` is the canonical id derived from
    ``(graph_id, idempotency_key)``; callers need it synchronously to
    return / persist the run handle. ``future`` resolves to the
    terminal :class:`RunSummary` -- most production callers discard
    it and retrieve the run via ``GET /v1/runs/{run_id}``.
    """

    run_id: str
    future: asyncio.Future[RunSummary]


class PendingRun:
    """Single durable pending-run row (design §6.1).

    Carries the trigger payload (``graph_id`` + ``params``) plus the
    idempotency key used to dedupe across restarts (cron:
    ``sha256(trigger_id || scheduled_fire)``; webhook:
    ``sha256(trigger_id || body_hash)``; manual: caller-supplied UUID).

    A pending row is inserted before the in-memory queue push and removed
    on terminal state (success or failure); restart loads remaining rows
    and re-pushes them onto the queue so in-flight work survives a
    process kill. The ``run_id`` is assigned at enqueue time so the
    Checkpointer's ``runs`` row (task 2.14) can reference it directly.

    Attributes:
        run_id: Stable identifier for this run; format depends on the
            trigger source. Cron uses ``cron-{trigger_id}-{iso_fire}``;
            webhook uses ``webhook-{trigger_id}-{body_sha8}``; manual
            uses ``manual-{idempotency_key[:12]}``. Formatting is the
            scheduler's responsibility -- callers don't construct it.
        graph_id: Target graph hash / id; doubles as the
            ``graph_hash`` key for the :class:`anyio.CapacityLimiter`
            map (design §6.2 expects ``graph.concurrency_limit`` once
            the IR field lands).
        params: JSON-serializable parameter dict forwarded to the run.
        idempotency_key: Pre-computed dedup key per design §6.1.
        scheduled_fire: Wall-clock instant the trigger fired (cron) or
            was received (webhook/manual). Used by the catchup probe in
            :meth:`Scheduler.replay_pending`.
    """

    __slots__ = (
        "graph_id",
        "idempotency_key",
        "params",
        "run_id",
        "scheduled_fire",
    )

    def __init__(
        self,
        *,
        run_id: str,
        graph_id: str,
        params: Mapping[str, Any],
        idempotency_key: str,
        scheduled_fire: datetime,
    ) -> None:
        self.run_id = run_id
        self.graph_id = graph_id
        self.params = params
        self.idempotency_key = idempotency_key
        self.scheduled_fire = scheduled_fire


@runtime_checkable
class PendingStore(Protocol):
    """Durable pending-run state contract (design §6.1, §6.2).

    Defined locally so the scheduler can accept a checkpointer-shaped
    object without forcing the v1 :class:`stargraph.checkpoint.Checkpointer`
    Protocol to grow scheduler-specific methods. A SQLite implementation
    lands in task 2.14 alongside the ``runs`` table; until then the
    scheduler accepts ``pending_store=None`` and degrades to in-memory.

    The contract is intentionally narrow -- four async methods, no
    transactional guarantees beyond per-call durability. The scheduler
    handles dedupe (idempotency key uniqueness) and crash-recovery
    (replay on startup).
    """

    async def put_pending(self, run: PendingRun) -> None:
        """Persist ``run`` so it survives a process restart."""
        ...

    async def delete_pending(self, run_id: str) -> None:
        """Remove the pending row for ``run_id`` (terminal state reached)."""
        ...

    async def list_pending(self) -> list[PendingRun]:
        """Return all pending rows (used at startup for replay)."""
        ...

    async def has_pending_for_key(self, idempotency_key: str) -> bool:
        """Return ``True`` if a pending row already exists for ``idempotency_key``."""
        ...


class QueueItem:
    """Single enqueued unit of work (in-memory queue row).

    Pairs a :class:`PendingRun` with the caller's awaiting
    :class:`asyncio.Future`. The dispatcher resolves the future on
    success or sets the worker's exception on failure.
    """

    __slots__ = ("future", "pending")

    def __init__(
        self,
        *,
        pending: PendingRun,
        future: asyncio.Future[RunSummary],
    ) -> None:
        self.pending = pending
        self.future = future
