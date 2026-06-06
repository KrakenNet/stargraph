# SPDX-License-Identifier: Apache-2.0
"""Trigger Protocol + ``TriggerEvent`` model + dispatcher re-exports.

Triggers are pluggy plugins (entry-point group ``stargraph.triggers``) that
emit :class:`TriggerEvent` objects into the scheduler queue. The four
lifecycle methods (``init``/``start``/``stop``/``routes``) mirror the
hookspec wrappers declared in :mod:`stargraph.plugin.hookspecs` (design
§6.3): the scheduler invokes them via
:func:`stargraph.plugin.triggers_dispatcher.dispatch_trigger_lifecycle`,
which provides per-plugin ``try/except`` isolation so that one
misbehaving trigger cannot block the others (FR-2, AC-12.2).

This module re-exports the dispatcher utilities so callers can write
``from stargraph.triggers import dispatch_trigger_lifecycle`` instead of
reaching into the plugin sub-package.

References: design §3.1 (new module table), §6.3 (trigger plugin
lifecycle); FR-1 (trigger plugin contract), FR-2 (per-plugin isolation),
AC-12.1, AC-12.2, AC-12.3.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- pydantic resolves at runtime
from typing import Any, Protocol, runtime_checkable

from stargraph.ir import IRBase
from stargraph.plugin.triggers_dispatcher import (
    DispatchResult,
    collect_trigger_routes,
    dispatch_trigger_lifecycle,
)

__all__ = [
    "DispatchResult",
    "Trigger",
    "TriggerEvent",
    "collect_trigger_routes",
    "dispatch_trigger_lifecycle",
]


# Route is aliased to ``Any`` so this module stays import-light: pulling
# FastAPI/Starlette into ``stargraph.triggers`` would force every plugin
# host (including non-serve consumers) to install them. Mirrors the
# convention in :mod:`stargraph.plugin.hookspecs`. Phase 2+ tightens this
# to ``starlette.routing.BaseRoute`` once the serve module lands its
# FastAPI dependency officially.
type _Route = Any


class TriggerEvent(IRBase):
    """Scheduler-bound event emitted by a trigger plugin (design §6.3).

    Triggers (manual / cron / webhook) construct a :class:`TriggerEvent`
    and hand it to the scheduler, which uses ``idempotency_key`` to
    deduplicate against pending-run state in the Checkpointer (FR-9.x,
    NFR-3) before enqueuing a run.

    Attributes:
        trigger_id: Stable identifier of the emitting trigger plugin
            instance (e.g. ``"cron:nightly-cve-feed"``,
            ``"webhook:nvd-mirror"``).
        scheduled_fire: Timestamp the trigger considered the canonical
            fire time. For cron triggers this is the cron-tick instant
            (used in ``sha256(trigger_id || scheduled_fire)`` idempotency
            keying per design §6.3); for webhook/manual triggers it is
            ``datetime.now(UTC)`` at receipt.
        idempotency_key: Pre-computed dedup key. Cron uses
            ``sha256(trigger_id || scheduled_fire)``; webhook uses
            ``sha256(trigger_id || body_hash)``; manual uses a
            caller-supplied UUID (defaults to a fresh one if absent).
        payload: Arbitrary JSON-serializable parameters forwarded to
            the run as ``params`` (e.g. webhook body, manual override
            args). Inherits ``extra='forbid'`` from :class:`IRBase`, so
            unknown top-level fields fail validation; ``payload`` is
            the escape hatch for trigger-specific data.
    """

    trigger_id: str
    scheduled_fire: datetime
    idempotency_key: str
    payload: dict[str, Any]


@runtime_checkable
class Trigger(Protocol):
    """Pluggable trigger contract (design §6.3, FR-1, FR-2).

    A trigger plugin owns its lifecycle (``init``/``start``/``stop``)
    plus optional FastAPI routes (``routes``). The scheduler invokes
    each method through
    :func:`stargraph.plugin.triggers_dispatcher.dispatch_trigger_lifecycle`
    so one plugin's failure never blocks the others (AC-12.2).

    The lifecycle method signatures mirror the hookspec callables in
    :mod:`stargraph.plugin.hookspecs` (``trigger_init`` / ``trigger_start`` /
    ``trigger_stop`` / ``trigger_routes``): all three lifecycle hooks
    receive a single ``deps`` mapping carrying the
    :class:`stargraph.serve.api.ServeContext` plus any wiring the plugin
    needs (logger, scheduler enqueue callback, audit sink, etc.).
    ``routes`` takes no arguments and returns the FastAPI routes the
    plugin wants the serve app to mount (empty list for cron-only
    triggers).

    Implementations may be sync or async; the dispatcher invokes the
    bound method directly and the scheduler awaits the result if it is
    a coroutine.
    """

    def init(self, deps: dict[str, Any]) -> None:
        """Set up internal state from ``deps`` at lifespan startup."""
        ...

    def start(self) -> None:
        """Begin emitting :class:`TriggerEvent`s into the scheduler queue."""
        ...

    def stop(self) -> None:
        """Drain in-flight work and stop emitting events (graceful shutdown)."""
        ...

    def routes(self) -> list[_Route]:
        """Return FastAPI routes to mount; default ``[]`` for non-HTTP triggers."""
        ...
