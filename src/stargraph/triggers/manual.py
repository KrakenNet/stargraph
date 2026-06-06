# SPDX-License-Identifier: Apache-2.0
"""``ManualTrigger`` plugin -- CLI + HTTP enqueue convergence (design §3.1).

The simplest of the three v1 triggers. Unlike :class:`CronTrigger` (task
2.10) and :class:`WebhookTrigger` (task 2.11), :class:`ManualTrigger`
does not poll a clock or listen on a socket: it is the explicit-caller
path used by:

* the ``stargraph run`` CLI subcommand (manual one-shot run from the
  operator's shell), and
* the ``POST /v1/runs`` HTTP route (already mounted by
  :func:`stargraph.serve.api.create_app`).

Both surfaces converge on :meth:`ManualTrigger.enqueue`, which delegates
to :class:`~stargraph.serve.scheduler.Scheduler` and returns the synthesized
``run_id`` so callers can immediately ``GET /v1/runs/{run_id}`` to poll
for terminal state.

Lifecycle (matches the :class:`~stargraph.triggers.Trigger` Protocol):

* :meth:`init` -- stash the :class:`Scheduler` reference from ``deps``.
* :meth:`start` / :meth:`stop` -- no-op (no background loop).
* :meth:`routes` -- return ``[]`` (``POST /v1/runs`` is mounted by the
  serve app directly; no plugin-owned routes are needed).

References: design §3.1 (``manual.py`` row), §6.3 (trigger lifecycle);
FR-3, AC-12.1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.errors import StargraphRuntimeError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from stargraph.serve.scheduler import Scheduler

__all__ = ["ManualTrigger"]


# Route is aliased to ``Any`` to keep this module import-light: pulling
# FastAPI/Starlette into ``stargraph.triggers.manual`` would force every
# plugin host (including non-serve consumers) to install them. Same
# convention as :mod:`stargraph.triggers` (the Protocol module).
type _Route = Any


class ManualTrigger:
    """Manual-enqueue trigger plugin (CLI + HTTP convergence).

    Stateless apart from the :class:`Scheduler` reference captured in
    :meth:`init`. Multiple callers may invoke :meth:`enqueue` concurrently;
    the underlying :class:`Scheduler` queue is the synchronization point.
    """

    _scheduler: Scheduler | None

    def __init__(self) -> None:
        self._scheduler = None

    def init(self, deps: dict[str, Any]) -> None:
        """Capture the :class:`Scheduler` reference from ``deps``.

        ``deps["scheduler"]`` is the lifespan-built
        :class:`~stargraph.serve.scheduler.Scheduler` instance (mirrors
        ``app.state.deps["scheduler"]`` in :mod:`stargraph.serve.api`).
        Raises :class:`StargraphRuntimeError` if the key is missing -- the
        manual trigger has no useful behaviour without a scheduler.
        """
        scheduler = deps.get("scheduler")
        if scheduler is None:
            raise StargraphRuntimeError(
                "ManualTrigger.init(deps) requires deps['scheduler']; "
                "lifespan must build the Scheduler before initialising triggers"
            )
        self._scheduler = scheduler

    def start(self) -> None:
        """No-op: ``ManualTrigger`` has no background loop to spawn."""

    def stop(self) -> None:
        """No-op: nothing to drain."""

    def routes(self) -> list[_Route]:
        """Return ``[]``: ``POST /v1/runs`` is mounted by the serve app directly."""
        return []

    def enqueue(
        self,
        graph_id: str,
        params: Mapping[str, Any],
        idempotency_key: str | None = None,
    ) -> str:
        """Enqueue a manual run; return the synthesized ``run_id``.

        Delegates to :meth:`Scheduler.enqueue` and discards the returned
        :class:`asyncio.Future` -- manual callers retrieve the run handle
        via ``GET /v1/runs/{run_id}`` rather than awaiting the future
        in-process. The future remains live on the scheduler side and
        resolves normally when the run terminates.

        ``run_id`` is synthesized as ``f"poc-{graph_id}"`` to match the
        ``POST /v1/runs`` route convention (see
        :mod:`stargraph.serve.api`). Phase 2 task 2.13 wires the canonical
        Checkpointer-persisted ``run_id`` here once the pending-row
        write lands.

        Raises :class:`StargraphRuntimeError` if :meth:`init` has not been
        called -- the trigger needs a scheduler reference before it can
        enqueue.
        """
        if self._scheduler is None:
            raise StargraphRuntimeError(
                "ManualTrigger.enqueue() requires init(deps) to have been called; "
                "the scheduler reference is not set"
            )
        handle = self._scheduler.enqueue(
            graph_id=graph_id,
            params=params,
            idempotency_key=idempotency_key,
        )
        # Manual callers poll ``GET /v1/runs/{run_id}`` for terminal
        # state; the future stays on the scheduler.
        return handle.run_id
