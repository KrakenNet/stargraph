# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :class:`harbor.triggers.manual.ManualTrigger` (FR-3).

Manual triggers carry the explicit-caller path used by both the
``harbor run`` CLI subcommand and the ``POST /v1/runs`` HTTP route.
Both surfaces converge on :meth:`ManualTrigger.enqueue`, which delegates
to :class:`Scheduler.enqueue` and synthesises a ``run_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harbor.errors import HarborRuntimeError
from harbor.triggers.manual import ManualTrigger

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = [pytest.mark.unit, pytest.mark.trigger]


class _RecordingScheduler:
    """Captures :meth:`enqueue` calls for assertion convergence."""

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
        # Mirror the real Scheduler return shape so callers that read
        # ``handle.run_id`` (ManualTrigger after task #14) work. Tests
        # never await the ``future`` slot, so we leave it as a typed-
        # but-unusable sentinel rather than building an asyncio.Future
        # under a sync test (which would need a running loop).
        from datetime import UTC, datetime

        from harbor.serve.scheduler import EnqueueHandle, Scheduler

        key = idempotency_key or Scheduler._synth_idempotency_key(  # pyright: ignore[reportPrivateUsage]
            graph_id, datetime.now(UTC)
        )
        run_id = Scheduler._derive_run_id(graph_id, key)  # pyright: ignore[reportPrivateUsage]
        return EnqueueHandle(run_id=run_id, future=None)  # pyright: ignore[reportArgumentType]


@pytest.fixture
def trigger() -> tuple[ManualTrigger, _RecordingScheduler]:
    """Return an initialised :class:`ManualTrigger` + its recording scheduler."""
    sched = _RecordingScheduler()
    t = ManualTrigger()
    t.init({"scheduler": sched})
    return t, sched


def test_manual_init_requires_scheduler() -> None:
    """:meth:`ManualTrigger.init` raises when ``deps['scheduler']`` is missing."""
    with pytest.raises(HarborRuntimeError, match="requires deps"):
        ManualTrigger().init({})


def test_manual_enqueue_before_init_raises() -> None:
    """Calling :meth:`enqueue` without :meth:`init` raises :class:`HarborRuntimeError`."""
    t = ManualTrigger()
    with pytest.raises(HarborRuntimeError, match=r"requires init\(deps\)"):
        t.enqueue("graph-x", {})


def test_manual_enqueue_delegates_to_scheduler(
    trigger: tuple[ManualTrigger, _RecordingScheduler],
) -> None:
    """Single :meth:`enqueue` call records exactly one scheduler invocation.

    Locks the FR-3 contract: the trigger does not buffer or dedupe;
    every caller invocation produces one ``Scheduler.enqueue`` call
    with the supplied ``graph_id`` + ``params``.
    """
    t, sched = trigger
    run_id = t.enqueue("graph-x", {"alpha": 1})
    assert len(sched.calls) == 1
    assert sched.calls[0]["graph_id"] == "graph-x"
    assert sched.calls[0]["params"] == {"alpha": 1}
    assert isinstance(run_id, str)
    # Canonical Scheduler-derived run_id is a hex hash, not a synthesized
    # ``poc-{graph_id}`` stub; just assert non-empty + hex-ish shape.
    assert len(run_id) >= 8


def test_manual_cli_and_http_convergence(
    trigger: tuple[ManualTrigger, _RecordingScheduler],
) -> None:
    """Two paths (CLI-shaped, HTTP-shaped) → identical ``Scheduler.enqueue`` payload.

    The "CLI path" passes positional ``graph_id`` + ``params`` (mirrors
    ``harbor run <graph> --params=...``); the "HTTP path" passes the
    same payload through the same method (since the route handler
    delegates here directly). Both must produce identical
    ``graph_id`` + ``params`` on the scheduler.
    """
    t, sched = trigger
    payload = {"foo": "bar", "n": 7}
    # CLI shape
    t.enqueue("graph-converge", payload)
    # HTTP shape (same method; the route handler in `harbor.serve.api`
    # forwards the body unchanged)
    t.enqueue("graph-converge", payload)
    assert len(sched.calls) == 2
    assert sched.calls[0]["graph_id"] == sched.calls[1]["graph_id"]
    assert sched.calls[0]["params"] == sched.calls[1]["params"]


def test_manual_enqueue_passes_idempotency_key_through(
    trigger: tuple[ManualTrigger, _RecordingScheduler],
) -> None:
    """Caller-supplied ``idempotency_key`` is forwarded verbatim.

    Manual triggers commonly pass an explicit caller UUID (the spec's
    "manual: caller-supplied UUID" idempotency convention); the trigger
    must forward it without rewriting.
    """
    t, sched = trigger
    t.enqueue("graph-x", {"x": 1}, idempotency_key="manual-uuid-1")
    assert sched.calls[0]["idempotency_key"] == "manual-uuid-1"


def test_manual_state_passthrough(
    trigger: tuple[ManualTrigger, _RecordingScheduler],
) -> None:
    """Initial state in ``params`` is forwarded to the scheduler unchanged.

    Forms the basis of "GraphRun receives caller-supplied initial
    state" -- the trigger does not strip or coerce ``params`` keys.
    """
    t, sched = trigger
    initial = {"state.counter": 0, "state.user_input": "value"}
    t.enqueue("graph-state", initial)
    assert sched.calls[0]["params"] == initial


def test_manual_routes_returns_empty(
    trigger: tuple[ManualTrigger, _RecordingScheduler],
) -> None:
    """:meth:`routes` returns ``[]``; ``POST /v1/runs`` is mounted by the serve app.

    Documents the FR-3 + AC-12.1 plugin contract: manual triggers do
    not own HTTP routes (the canonical entrypoint is the serve-app's
    ``POST /v1/runs`` route, which calls into the trigger). A custom
    route would create a duplicate enqueue path.
    """
    t, _sched = trigger
    assert t.routes() == []


def test_manual_start_and_stop_are_no_ops(
    trigger: tuple[ManualTrigger, _RecordingScheduler],
) -> None:
    """``start`` / ``stop`` are no-ops; manual triggers have no background loop.

    Calling them in any order must not raise; subsequent
    :meth:`enqueue` still works.
    """
    t, sched = trigger
    t.start()
    t.stop()
    t.start()
    t.enqueue("graph-x", {})
    assert len(sched.calls) == 1
