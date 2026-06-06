# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :class:`stargraph.triggers.cron.CronTrigger` (FR-4).

Covers cronsim integration, IANA TZ handling, idempotency-key shape,
DST behaviour, eager validation, and the trigger→scheduler enqueue
shape.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import cronsim
import pytest

from stargraph.errors import StargraphRuntimeError
from stargraph.triggers.cron import CronSpec, CronTrigger

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = [pytest.mark.unit, pytest.mark.trigger]


class _RecordingScheduler:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def enqueue(
        self,
        graph_id: str,
        params: Mapping[str, Any],
        idempotency_key: str | None = None,
        *,
        trigger_source: str = "manual",
    ) -> Any:
        self.calls.append(
            {
                "graph_id": graph_id,
                "params": dict(params),
                "idempotency_key": idempotency_key,
                "trigger_source": trigger_source,
            }
        )
        return None


def _spec(**overrides: Any) -> CronSpec:
    """Build a :class:`CronSpec` with reasonable defaults for tests."""
    base: dict[str, Any] = {
        "trigger_id": "cron:test",
        "cron_expression": "0 9 * * 1-5",
        "tz": "UTC",
        "graph_id": "graph-cron",
        "params": {},
    }
    base.update(overrides)
    return CronSpec(**base)


def test_cronsim_weekday_9am_yields_five_per_week() -> None:
    """``0 9 * * 1-5`` (weekday 9am) produces 5 fires/week per cronsim."""
    zone = ZoneInfo("UTC")
    # Start on a Sunday so the next 5 fires are Mon-Fri.
    base = datetime(2026, 4, 26, 0, 0, tzinfo=zone)
    sim = cronsim.CronSim("0 9 * * 1-5", base)
    fires = [next(sim) for _ in range(5)]
    weekdays = {f.weekday() for f in fires}
    # 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri
    assert weekdays == {0, 1, 2, 3, 4}
    # Sixth fire jumps over the weekend back to Monday.
    sixth = next(sim)
    assert sixth.weekday() == 0


def test_iana_tz_yields_distinct_utc_instants() -> None:
    """``0 9 * * *`` in two zones produces different UTC instants.

    9am Los Angeles ≠ 9am Tokyo: converted to UTC, the LA fire is 16:00 or
    17:00 UTC (DST-dependent) and the Tokyo fire is 00:00 UTC. Locks the
    "tz is stored explicitly per trigger" design intent.
    """
    la = ZoneInfo("America/Los_Angeles")
    tokyo = ZoneInfo("Asia/Tokyo")
    base_la = datetime(2026, 4, 30, 0, 0, tzinfo=la)
    base_tokyo = datetime(2026, 4, 30, 0, 0, tzinfo=tokyo)
    fire_la = next(cronsim.CronSim("0 9 * * *", base_la))
    fire_tokyo = next(cronsim.CronSim("0 9 * * *", base_tokyo))
    assert fire_la.astimezone(ZoneInfo("UTC")) != fire_tokyo.astimezone(ZoneInfo("UTC"))


def test_next_fire_after_now_returns_future_moment() -> None:
    """:meth:`CronTrigger.next_fire` returns a tz-aware fire strictly after ``after``."""
    spec = _spec(cron_expression="*/15 * * * *", tz="UTC")
    trig = CronTrigger()
    after = datetime(2026, 4, 30, 12, 5, tzinfo=ZoneInfo("UTC"))
    fire = trig.next_fire(spec, after=after)
    assert fire > after
    # Quarter-hour cadence: next fire should be 12:15 UTC.
    assert fire == datetime(2026, 4, 30, 12, 15, tzinfo=ZoneInfo("UTC"))


def test_dst_spring_forward_handled() -> None:
    """``30 2 * * *`` across the 2026-03-08 DST jump in ``America/New_York``.

    Same locked behaviour as :mod:`tests.unit.serve.test_scheduler`:
    cronsim does NOT skip the DST day -- it promotes the missing 02:30
    fire to the closest existing wall-clock instant. Asserts the
    next-day cadence resumes correctly.
    """
    zone = ZoneInfo("America/New_York")
    spec = _spec(cron_expression="30 2 * * *", tz="America/New_York")
    trig = CronTrigger()
    # After 2026-03-07 03:00 EST -> first fire today (3-7 02:30) is past;
    # next fire is on 03-08 (DST day) at the cronsim-promoted time.
    after = datetime(2026, 3, 7, 3, 0, tzinfo=zone)
    fire_dst = trig.next_fire(spec, after=after)
    assert fire_dst.date() == datetime(2026, 3, 8).date()
    # Next fire after the DST day is 03-09 02:30 EDT (normal cadence).
    fire_after = trig.next_fire(spec, after=fire_dst)
    assert fire_after == datetime(2026, 3, 9, 2, 30, tzinfo=zone)


def test_invalid_cron_expression_fails_at_init() -> None:
    """Bad cron expression raises at :meth:`init`, not at first fire.

    Lock the "fail fast at startup, not hours later" contract from
    design §6.3.
    """
    bad_spec = _spec(cron_expression="not a real cron")
    trig = CronTrigger()
    with pytest.raises(cronsim.CronSimError):
        trig.init(
            {
                "scheduler": _RecordingScheduler(),
                "cron_specs": [bad_spec],
            }
        )


def test_invalid_tz_fails_at_init() -> None:
    """Bad IANA TZ name raises at :meth:`init` via :class:`ZoneInfoNotFoundError`."""
    bad_spec = _spec(tz="Mars/Olympus_Mons")
    trig = CronTrigger()
    with pytest.raises(ZoneInfoNotFoundError):
        trig.init(
            {
                "scheduler": _RecordingScheduler(),
                "cron_specs": [bad_spec],
            }
        )


def test_init_requires_scheduler() -> None:
    """``deps['scheduler']`` is required."""
    trig = CronTrigger()
    with pytest.raises(StargraphRuntimeError, match="requires deps"):
        trig.init({"cron_specs": [_spec()]})


def test_init_requires_specs() -> None:
    """``deps['cron_specs']`` is required and must be non-empty."""
    trig = CronTrigger()
    with pytest.raises(StargraphRuntimeError, match="cron_specs"):
        trig.init({"scheduler": _RecordingScheduler(), "cron_specs": []})


async def test_fire_emits_canonical_idempotency_key() -> None:
    """:meth:`CronTrigger._fire` enqueues with ``sha256(trigger_id||iso_fire)``.

    Locks the trigger→scheduler shape: the fire path passes the
    canonical idempotency key so dedupe works across the dual-path
    arrangement (scheduler's internal cron loop + per-trigger fire
    loop).
    """
    sched = _RecordingScheduler()
    spec = _spec(trigger_id="cron:emit-test", graph_id="graph-emit")
    trig = CronTrigger()
    trig.init({"scheduler": sched, "cron_specs": [spec]})
    fire_at = datetime(2026, 4, 30, 9, 0, tzinfo=ZoneInfo("UTC"))
    await trig._fire(spec, fire_at)  # pyright: ignore[reportPrivateUsage]
    expected_key = hashlib.sha256(f"cron:emit-test|{fire_at.isoformat()}".encode()).hexdigest()
    assert len(sched.calls) == 1
    assert sched.calls[0]["graph_id"] == "graph-emit"
    assert sched.calls[0]["idempotency_key"] == expected_key


def test_idempotency_key_static_helper_is_pure() -> None:
    """:meth:`CronTrigger.idempotency_key` is callable without an instance.

    Static so the catchup probe + tests can compute keys without
    holding a :class:`CronTrigger` instance. Same payload -> same key.
    """
    fire = datetime(2026, 4, 30, 9, 0, tzinfo=ZoneInfo("UTC"))
    a = CronTrigger.idempotency_key("cron:t", fire)
    b = CronTrigger.idempotency_key("cron:t", fire)
    assert a == b
    expected = hashlib.sha256(f"cron:t|{fire.isoformat()}".encode()).hexdigest()
    assert a == expected
