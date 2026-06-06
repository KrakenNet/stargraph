# SPDX-License-Identifier: Apache-2.0
""":class:`CronTrigger` plugin -- cronsim-driven scheduled fire (design §6.1, §6.3).

The cron variant of the v1 trigger trio. Unlike :class:`ManualTrigger`
(task 2.9, explicit-caller path) and :class:`WebhookTrigger` (task 2.11,
HTTP-receive path), :class:`CronTrigger` owns a background ``asyncio``
task per :class:`CronSpec` that loops:

1. compute ``next_fire`` via :class:`cronsim.CronSim` (DST-safe, IANA TZ
   per :class:`zoneinfo.ZoneInfo`),
2. ``await anyio.sleep_until(next_fire)``,
3. compute the idempotency key ``sha256(f"{trigger_id}|{iso_fire}")``,
4. enqueue via :meth:`Scheduler.enqueue` (Phase 2 task 2.13 wires the
   key to the Checkpointer pending row for restart-safe dedupe).

Lifecycle (matches the :class:`~stargraph.triggers.Trigger` Protocol):

* :meth:`init` -- stash the :class:`Scheduler` from ``deps`` and parse
  the supplied :class:`CronSpec` list (validates every cron expression
  + IANA TZ up-front; bad config fails fast at lifespan startup, not
  at first fire).
* :meth:`start` -- spawn one background fire-loop task per spec. The
  tasks survive until :meth:`stop` cancels them.
* :meth:`stop` -- cancel each fire-loop task and await clean exit.
* :meth:`routes` -- ``[]`` (no HTTP surface; cron polls a clock).

Why ``cronsim``? Design §6.1 picked it for DST-safety -- ``croniter``
silently mishandles tz transitions (the design's research call rejects
it). ``cronsim.CronSim(expr, base_dt)`` returns an iterator yielding
the next match relative to ``base_dt``; we pass ``datetime.now(tz)``
each loop iteration so DST forward/backward shifts produce one fire
each (cronsim handles the ambiguity).

Missed-fire policy (per :class:`CronSpec.missed_fire_policy`):

* ``"fire_once_catchup"`` (default) -- on :meth:`start`, if the most
  recent scheduled fire was missed (system was down), fire once with
  the missed ``scheduled_fire`` so the idempotency key matches what
  a never-down system would have produced. Subsequent fires resume
  the normal forward cadence.
* ``"skip"`` -- silently skip any missed fires; the first fire is the
  next future ``next_fire``.

References: design §6.1 (async cron loop), §6.3 (trigger plugin
lifecycle), §3.1 (cron.py row); FR-4 (cron trigger), AC-12.1
(plugin discovery), NFR-3 (±100ms scheduler precision).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

import cronsim
from pydantic import Field

from stargraph.errors import StargraphRuntimeError
from stargraph.ir import IRBase

if TYPE_CHECKING:
    from collections.abc import Iterable

    from stargraph.serve.scheduler import Scheduler

__all__ = ["CronSpec", "CronTrigger"]

_logger = logging.getLogger(__name__)

# Route is aliased to ``Any`` to keep this module import-light. Same
# convention as :mod:`stargraph.triggers.manual` and :mod:`stargraph.triggers`.
type _Route = Any

#: Type alias for the missed-fire policy literal. ``fire_once_catchup``
#: is the design.md §6.1 default; ``skip`` is the opt-out for triggers
#: where catch-up is undesired (e.g. heartbeat pings whose signal value
#: degrades after the deadline passes).
type MissedFirePolicy = Literal["fire_once_catchup", "skip"]


class CronSpec(IRBase):
    """Single cron-trigger configuration row (design §6.1 + §6.3).

    Attributes:
        trigger_id: Stable identifier for this cron trigger instance
            (e.g. ``"cron:nightly-cve-feed"``). Goes into the
            idempotency key ``sha256(f"{trigger_id}|{scheduled_fire}")``
            so it must be unique across the deployment.
        cron_expression: Standard 5-field cron expression. Parsed by
            :class:`cronsim.CronSim`; invalid syntax raises at
            :meth:`CronTrigger.init`, not at first fire.
        tz: IANA timezone name (e.g. ``"UTC"``, ``"America/New_York"``)
            resolved via :class:`zoneinfo.ZoneInfo`. Stored explicitly
            per design §6.1 ("IANA TZ stored explicitly per trigger; UTC
            server recommendation in air-gap guide").
        graph_id: Target graph to enqueue when the trigger fires.
        params: JSON-serializable parameter dict forwarded to the run.
        missed_fire_policy: ``"fire_once_catchup"`` (default; on restart,
            fire any missed schedule once -- idempotency key dedupes if
            it already fired) or ``"skip"`` (silently skip missed fires).
    """

    trigger_id: str
    cron_expression: str
    tz: str
    graph_id: str
    params: dict[str, Any] = Field(default_factory=dict)
    missed_fire_policy: MissedFirePolicy = "fire_once_catchup"


class CronTrigger:
    """Cron-driven trigger plugin (cronsim, IANA TZ, ±100ms precision).

    One :class:`CronTrigger` instance owns N :class:`CronSpec` rows; on
    :meth:`start` it spawns one background fire-loop ``asyncio.Task``
    per spec. Each loop computes ``next_fire`` via
    :class:`cronsim.CronSim`, sleeps until then, fires (enqueues +
    idempotency key), and recomputes.

    The task uses ``asyncio.create_task`` (not ``anyio.create_task_group``)
    because ``Scheduler`` already uses the same pattern and the lifespan
    contract is "start spawns tasks, stop cancels them" -- a task group
    would force :meth:`start` to be a context manager, which mis-matches
    the :class:`~stargraph.triggers.Trigger` Protocol.
    """

    _scheduler: Scheduler | None
    _specs: list[tuple[CronSpec, ZoneInfo]]
    _tasks: list[asyncio.Task[None]]
    _last_fire: dict[str, datetime]
    _running: bool

    def __init__(self) -> None:
        self._scheduler = None
        self._specs = []
        self._tasks = []
        self._last_fire = {}
        self._running = False

    def init(self, deps: dict[str, Any]) -> None:
        """Capture the :class:`Scheduler` and validate :class:`CronSpec` list.

        ``deps["scheduler"]`` is the lifespan-built scheduler. ``deps["cron_specs"]``
        is an iterable of :class:`CronSpec` (or dicts that parse to one).
        Both keys are required: a cron trigger with no specs is a
        configuration error caught at startup.

        Validates each cron expression by constructing a :class:`cronsim.CronSim`
        eagerly -- bad expressions raise here, not at first fire (which
        could be hours away). Same for :class:`zoneinfo.ZoneInfo` (raises
        :class:`zoneinfo.ZoneInfoNotFoundError` for unknown IANA names).

        Raises :class:`StargraphRuntimeError` if ``deps`` is missing required
        keys or the spec list is empty.
        """
        scheduler = deps.get("scheduler")
        if scheduler is None:
            raise StargraphRuntimeError(
                "CronTrigger.init(deps) requires deps['scheduler']; "
                "lifespan must build the Scheduler before initialising triggers"
            )
        raw_specs: Iterable[Any] | None = deps.get("cron_specs")
        if raw_specs is None:
            raise StargraphRuntimeError(
                "CronTrigger.init(deps) requires deps['cron_specs']: an iterable of CronSpec rows"
            )
        parsed: list[tuple[CronSpec, ZoneInfo]] = []
        for raw in raw_specs:
            spec = raw if isinstance(raw, CronSpec) else CronSpec.model_validate(raw)
            # Eagerly resolve TZ + parse expression so bad config fails
            # fast at startup. ZoneInfo raises ZoneInfoNotFoundError for
            # unknown IANA names; CronSim raises on malformed expressions.
            zone = ZoneInfo(spec.tz)
            cronsim.CronSim(spec.cron_expression, datetime.now(zone))
            parsed.append((spec, zone))
        if not parsed:
            raise StargraphRuntimeError(
                "CronTrigger.init(deps) received empty deps['cron_specs']; "
                "at least one CronSpec is required"
            )
        self._scheduler = scheduler
        self._specs = parsed

    def start(self) -> None:
        """Spawn one background fire-loop task per :class:`CronSpec`.

        Idempotent: a second :meth:`start` call is a no-op (same contract
        as :meth:`Scheduler.start`).

        The fire-loop tasks are top-level :class:`asyncio.Task` objects
        named ``stargraph.triggers.cron.<trigger_id>`` for inspection. They
        run until :meth:`stop` cancels them.
        """
        if self._running:
            return
        if not self._specs:
            raise StargraphRuntimeError(
                "CronTrigger.start() requires init(deps) to have been called; no specs are loaded"
            )
        self._running = True
        for spec, zone in self._specs:
            task = asyncio.create_task(
                self._fire_loop(spec, zone),
                name=f"stargraph.triggers.cron.{spec.trigger_id}",
            )
            self._tasks.append(task)

    def stop(self) -> None:
        """Cancel all background fire-loop tasks; idempotent.

        Each task is cancelled and awaited; we suppress :class:`asyncio.CancelledError`
        because it is the expected exit path. The ``Trigger`` Protocol's
        :meth:`stop` is sync, so this method schedules the cancellations
        and returns -- the lifespan-level dispatcher (task 2.13+ wiring)
        handles the await side.
        """
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        # Note: Protocol.stop is sync. Awaiting cancellations belongs in
        # the async lifespan dispatcher (task 2.13). Cancelling the task
        # is sufficient to begin teardown; the event loop handles cleanup
        # on next tick.
        self._tasks = []

    def routes(self) -> list[_Route]:
        """Return ``[]``: cron triggers have no HTTP surface."""
        return []

    def next_fire(self, spec: CronSpec, *, after: datetime | None = None) -> datetime:
        """Compute the next scheduled fire time for ``spec``.

        Public for unit-style smoke tests + the Phase-2 catchup probe.
        ``after`` defaults to ``datetime.now(zone)``; pass an explicit
        moment to test "what's the next fire after T?".

        Returns a tz-aware :class:`datetime` in the spec's IANA zone.
        """
        zone = ZoneInfo(spec.tz)
        base = after if after is not None else datetime.now(zone)
        # Coerce ``after`` into the spec's zone if the caller passed UTC
        # or a different tz; cronsim respects the tzinfo of its base dt.
        base = base.replace(tzinfo=zone) if base.tzinfo is None else base.astimezone(zone)
        return next(cronsim.CronSim(spec.cron_expression, base))

    @staticmethod
    def idempotency_key(trigger_id: str, scheduled_fire: datetime) -> str:
        """Compute ``sha256(f"{trigger_id}|{iso_fire}")``.

        Static so callers (tests, the catchup probe, the Checkpointer
        dedupe path in task 2.13) can compute the key without holding a
        :class:`CronTrigger` instance. ISO format includes the tz offset
        so the same wall-clock-instant in different zones produces
        distinct keys (correct: a 09:00 America/New_York fire and a
        09:00 UTC fire are different events).
        """
        payload = f"{trigger_id}|{scheduled_fire.isoformat()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _fire_loop(self, spec: CronSpec, zone: ZoneInfo) -> None:
        """Background fire-loop for a single :class:`CronSpec`.

        Loop:

        1. Compute ``next_fire`` from "now in spec.tz".
        2. Sleep until ``next_fire`` (``asyncio.sleep`` for portability;
           ``anyio.sleep_until`` would be cleaner but adds an anyio import
           for a single call site -- the difference is academic at our
           ±100ms target per NFR-3).
        3. Fire: compute idempotency key, enqueue, record ``last_fire``.
        4. Repeat.

        On ``asyncio.CancelledError`` (from :meth:`stop`), exits cleanly.
        Any other exception is logged and the loop continues -- one bad
        fire must not kill the whole cron trigger (FR-2 isolation spirit
        applied to the per-spec layer).

        Missed-fire ``fire_once_catchup`` handling: on first iteration,
        if the previous scheduled fire (computed by stepping CronSim
        backwards from now) is in the past *and* we have no
        ``last_fire`` record for this trigger, fire once with that
        missed timestamp before entering the normal forward cadence.
        Phase 2 task 2.13 reads ``last_fire`` from the Checkpointer for
        cross-restart durability; the in-memory dict is the POC stand-in.
        """
        first_iteration = True
        while self._running:
            try:
                now = datetime.now(zone)
                if first_iteration and spec.missed_fire_policy == "fire_once_catchup":
                    first_iteration = False
                    missed = self._compute_last_missed_fire(spec, zone, now)
                    if missed is not None and self._last_fire.get(spec.trigger_id) != missed:
                        await self._fire(spec, missed)
                        # Continue to forward cadence after the catchup.
                        continue
                first_iteration = False
                fire_at = next(cronsim.CronSim(spec.cron_expression, now))
                # asyncio.sleep tolerates timezone-aware datetimes via
                # raw float seconds; compute the delta defensively.
                delay = (fire_at - now).total_seconds()
                if delay > 0:
                    await asyncio.sleep(delay)
                if not self._running:
                    return
                await self._fire(spec, fire_at)
            except asyncio.CancelledError:
                return
            except Exception:  # pragma: no cover - defensive
                # Per-spec isolation: log and continue. The fire-loop
                # task surviving a single bad iteration matches the
                # FR-2 plugin-isolation spirit.
                _logger.exception(
                    "cron fire-loop iteration failed for trigger_id=%s",
                    spec.trigger_id,
                )
                # Sleep briefly to avoid a tight failure loop.
                await asyncio.sleep(1.0)

    @staticmethod
    def _compute_last_missed_fire(
        spec: CronSpec,
        zone: ZoneInfo,
        now: datetime,
    ) -> datetime | None:
        """Return the most recent scheduled fire <= ``now``, or ``None``.

        Used by the ``fire_once_catchup`` path: at startup, if the system
        was down across a scheduled fire, this returns that timestamp so
        :meth:`_fire_loop` can emit it once. If the trigger has never
        fired (its first scheduled fire is in the future), returns ``None``.

        cronsim has no "previous fire" iterator for forward expressions
        in older versions, so we compute it by stepping forward from one
        cycle ago and taking the last value <= ``now``.
        """
        # Look back ~1 day; cron expressions fire at most every minute,
        # so at most ~1440 entries. Sufficient for catch-up across a
        # pod-restart window without unbounded iteration.
        from datetime import timedelta

        lookback_start = now - timedelta(days=1)
        sim = cronsim.CronSim(spec.cron_expression, lookback_start)
        last: datetime | None = None
        for _ in range(2000):  # safety bound
            try:
                candidate = next(sim)
            except StopIteration:  # pragma: no cover - cronsim is infinite
                break
            if candidate > now:
                break
            last = candidate
        return last

    async def _fire(self, spec: CronSpec, scheduled_fire: datetime) -> None:
        """Enqueue one run for ``spec`` at ``scheduled_fire``.

        Computes the idempotency key, calls :meth:`Scheduler.enqueue`,
        discards the returned future (cron callers don't await runs in
        process -- the run is observed via ``GET /v1/runs``), and records
        ``last_fire`` for catchup-dedupe within the same process.
        """
        if self._scheduler is None:
            raise StargraphRuntimeError(
                "CronTrigger._fire requires init(deps) to have set the scheduler"
            )
        key = self.idempotency_key(spec.trigger_id, scheduled_fire)
        self._scheduler.enqueue(
            graph_id=spec.graph_id,
            params=spec.params,
            idempotency_key=key,
        )
        self._last_fire[spec.trigger_id] = scheduled_fire
