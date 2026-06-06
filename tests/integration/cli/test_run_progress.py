# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import io
from datetime import UTC, datetime

import anyio
import pytest
from rich.console import Console

from stargraph.cli._progress import ProgressPrinter
from stargraph.runtime.bus import EventBus
from stargraph.runtime.events import (
    ErrorEvent,
    ResultEvent,
    ToolCallEvent,
    ToolResultEvent,
    TransitionEvent,
)


def _now() -> datetime:
    return datetime.now(UTC)


@pytest.mark.integration
@pytest.mark.anyio
async def test_renders_node_lines_and_counts_llm_calls() -> None:
    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    printer = ProgressPrinter(console)

    bus = EventBus()

    async def _produce() -> None:
        # Simulate: start node "a" -> tool call -> transition to "b" -> finish
        await bus.send(
            TransitionEvent(
                run_id="r",
                step=1,
                ts=_now(),
                from_node="__start__",
                to_node="a",
                rule_id="r0",
                reason="start",
            ),
            fathom=None,
        )
        await bus.send(
            ToolCallEvent(
                run_id="r",
                step=1,
                ts=_now(),
                tool_name="dspy.predict",
                namespace="t",
                args={},
                call_id="c1",
            ),
            fathom=None,
        )
        await bus.send(
            ToolResultEvent(
                run_id="r",
                step=1,
                ts=_now(),
                call_id="c1",
                ok=True,
                result={},
            ),
            fathom=None,
        )
        await bus.send(
            TransitionEvent(
                run_id="r",
                step=2,
                ts=_now(),
                from_node="a",
                to_node="b",
                rule_id="r1",
                reason="progress",
            ),
            fathom=None,
        )
        await bus.send(
            TransitionEvent(
                run_id="r",
                step=3,
                ts=_now(),
                from_node="b",
                to_node="__end__",
                rule_id="r2",
                reason="halt",
            ),
            fathom=None,
        )
        await bus.send(
            ResultEvent(
                run_id="r",
                step=3,
                ts=_now(),
                status="done",
                final_state={},
                run_duration_ms=100,
            ),
            fathom=None,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(_produce)
        tg.start_soon(printer.consume, bus)
    await bus.aclose()

    text = out.getvalue()
    # We rendered lines for nodes "a" and "b" but NOT __start__ or __end__
    assert " a " in text or "] a" in text
    assert " b " in text or "] b" in text
    assert "__start__" not in text
    assert "__end__" not in text
    assert "dspy.predict" in text
    assert "✓" in text

    s = printer.stats()
    assert s.step_count == 2  # nodes a, b (sentinels excluded)
    assert s.llm_call_count == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_quiet_suppresses_output_but_records_stats() -> None:
    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    printer = ProgressPrinter(console, quiet=True)

    bus = EventBus()

    async def _produce() -> None:
        await bus.send(
            TransitionEvent(
                run_id="r",
                step=1,
                ts=_now(),
                from_node="__start__",
                to_node="a",
                rule_id="r0",
                reason="start",
            ),
            fathom=None,
        )
        await bus.send(
            TransitionEvent(
                run_id="r",
                step=2,
                ts=_now(),
                from_node="a",
                to_node="__end__",
                rule_id="r1",
                reason="halt",
            ),
            fathom=None,
        )
        await bus.send(
            ResultEvent(
                run_id="r",
                step=2,
                ts=_now(),
                status="done",
                final_state={},
                run_duration_ms=10,
            ),
            fathom=None,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(_produce)
        tg.start_soon(printer.consume, bus)
    await bus.aclose()

    assert out.getvalue() == ""
    assert printer.stats().step_count == 1


@pytest.mark.integration
@pytest.mark.anyio
async def test_error_event_marks_node_failed() -> None:
    out = io.StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    printer = ProgressPrinter(console)

    bus = EventBus()

    async def _produce() -> None:
        await bus.send(
            TransitionEvent(
                run_id="r",
                step=1,
                ts=_now(),
                from_node="__start__",
                to_node="boom",
                rule_id="r0",
                reason="start",
            ),
            fathom=None,
        )
        await bus.send(
            ErrorEvent(
                run_id="r",
                step=1,
                ts=_now(),
                scope="node",
                message="kaboom",
                recoverable=False,
            ),
            fathom=None,
        )
        await bus.send(
            ResultEvent(
                run_id="r",
                step=1,
                ts=_now(),
                status="failed",
                final_state={},
                run_duration_ms=5,
            ),
            fathom=None,
        )

    async with anyio.create_task_group() as tg:
        tg.start_soon(_produce)
        tg.start_soon(printer.consume, bus)
    await bus.aclose()

    text = out.getvalue()
    assert "✗" in text
    assert "kaboom" in text
