# SPDX-License-Identifier: Apache-2.0
"""Live progress printer for ``stargraph run`` (Plan 1, Task 3).

Subscribes to a :class:`stargraph.runtime.bus.EventBus` and renders one line
per executed node, plus inline tool-call summaries and HITL pause
markers. Stats (step count, LLM calls, tokens, durations) are
accumulated regardless of ``quiet`` so callers can print a final summary
even when per-step output was suppressed.

Behaviour highlights:

* :class:`TransitionEvent` boundaries close the prior node line and open
  the next; sentinel nodes (``__start__``, ``__end__``) are excluded
  from rendering and from the step count.
* :class:`ToolCallEvent` increments the LLM-call counter and is buffered
  on the in-flight node so the closing line names the tool(s) used.
* :class:`ToolResultEvent` adds ``result.usage.total_tokens`` to the
  running total when present; in ``verbose`` mode the result payload is
  also dumped under the node line.
* :class:`ErrorEvent` flips the in-flight node to failed and records the
  message, which renders alongside the closing line as a red
  ``error: <msg>``.
* :class:`WaitingForInputEvent` prints an ``awaiting input`` marker; the
  actual prompt-handling lives in the HITL handler (Task 5).
* Terminal :class:`ResultEvent` closes out the last node and the
  consumer loop returns.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import anyio

if TYPE_CHECKING:
    from datetime import datetime

    from rich.console import Console

    from stargraph.runtime.bus import EventBus

__all__ = ["ProgressPrinter", "ProgressStats"]


_SENTINELS = frozenset({"__start__", "__end__"})


@dataclass(frozen=True)
class ProgressStats:
    """Aggregated counters captured across a run."""

    step_count: int
    llm_call_count: int
    total_tool_tokens: int
    node_durations_ms: dict[str, int]


@dataclass
class _NodeInflight:
    """Mutable state for the node currently being rendered."""

    node_id: str
    started_at: datetime
    step_index: int
    tool_calls: list[str] = field(default_factory=list[str])
    failed: bool = False
    error_message: str | None = None


class ProgressPrinter:
    """Drains an :class:`EventBus` and prints node-level progress."""

    def __init__(
        self,
        console: Console,
        *,
        quiet: bool = False,
        verbose: bool = False,
    ) -> None:
        self._console = console
        self._quiet = quiet
        self._verbose = verbose
        self._current: _NodeInflight | None = None
        self._step_counter = 0
        self._llm_calls = 0
        self._tool_tokens = 0
        self._durations: dict[str, int] = {}
        self._final_state: dict[str, Any] | None = None
        self._run_duration_ms: int | None = None

    async def consume(self, bus: EventBus) -> None:
        """Receive events from ``bus`` until terminal/aclose.

        Returns on terminal :class:`ResultEvent` or when the bus closes
        (cancellation paths). The remaining open node, if any, is
        flushed before returning.
        """
        with contextlib.suppress(anyio.EndOfStream, anyio.ClosedResourceError):
            while True:
                ev: Any = await bus.receive()
                self.feed(ev)
                if ev.type == "result":
                    self.finalize(ev.ts)
                    return

    def stats(self) -> ProgressStats:
        return ProgressStats(
            step_count=self._step_counter,
            llm_call_count=self._llm_calls,
            total_tool_tokens=self._tool_tokens,
            node_durations_ms=dict(self._durations),
        )

    def final_state_dict(self) -> dict[str, Any] | None:
        """Return ``ResultEvent.final_state`` if a terminal event was seen."""
        return self._final_state

    def run_duration_ms(self) -> int | None:
        """Return ``ResultEvent.run_duration_ms`` if a terminal event was seen."""
        return self._run_duration_ms

    # -- handlers -------------------------------------------------------

    def feed(self, ev: Any) -> None:
        """Public hook: process one event (used by external drivers)."""
        self._handle(ev)

    def finalize(self, end_ts: datetime) -> None:
        """Public hook: close the in-flight node line."""
        self._close_current(end_ts)

    def _handle(self, ev: Any) -> None:
        kind: str = ev.type
        if kind == "transition":
            self._on_transition(ev)
        elif kind == "tool_call":
            self._on_tool_call(ev)
        elif kind == "tool_result":
            self._on_tool_result(ev)
        elif kind == "error":
            self._on_error(ev)
        elif kind == "waiting_for_input":
            self._on_waiting(ev)
        elif kind == "result":
            self._final_state = dict(getattr(ev, "final_state", {}) or {})
            run_duration: Any = getattr(ev, "run_duration_ms", None)
            if isinstance(run_duration, int):
                self._run_duration_ms = run_duration

    def _on_transition(self, ev: Any) -> None:
        # Close out previous node (if any) and open the next.
        if self._current is not None:
            self._close_current(ev.ts)
        to_node: str = ev.to_node
        # Empty target = end-of-graph halt (dispatch.py emits "" when there is
        # no next node); sentinels are __start__/__end__ markers. Neither
        # represents a real node about to execute, so don't open an inflight
        # for them — that would surface as a phantom step at run end.
        if to_node and to_node not in _SENTINELS:
            self._step_counter += 1
            self._current = _NodeInflight(
                node_id=to_node,
                started_at=ev.ts,
                step_index=self._step_counter,
            )

    def _on_tool_call(self, ev: Any) -> None:
        self._llm_calls += 1
        if self._current is not None:
            self._current.tool_calls.append(ev.tool_name)

    def _on_tool_result(self, ev: Any) -> None:
        result: Any = ev.result
        if isinstance(result, dict):
            result_dict = cast("dict[str, Any]", result)
            usage_any: Any = result_dict.get("usage")
            if isinstance(usage_any, dict):
                usage_dict = cast("dict[str, Any]", usage_any)
                tokens: Any = usage_dict.get("total_tokens", 0)
                with contextlib.suppress(TypeError, ValueError):
                    self._tool_tokens += int(tokens)
        if self._verbose and not self._quiet and result is not None:
            self._console.print(f"      [dim]{result}[/dim]")

    def _on_error(self, ev: Any) -> None:
        if self._current is not None:
            self._current.failed = True
            self._current.error_message = ev.message

    def _on_waiting(self, ev: Any) -> None:
        # ev.prompt / ev.interrupt_payload are consumed by the HITL handler
        # in Task 5; the printer just renders a pause marker.
        del ev
        if self._quiet or self._current is None:
            return
        n = self._current
        self._console.print(
            f"[{n.step_index:02d}] {n.node_id:<30s} [yellow]⏸  awaiting input[/yellow]"
        )

    # -- rendering ------------------------------------------------------

    def _close_current(self, end_ts: datetime) -> None:
        n = self._current
        if n is None:
            return
        duration_ms = max(0, int((end_ts - n.started_at).total_seconds() * 1000))
        self._durations[n.node_id] = duration_ms
        if not self._quiet:
            mark = "[red]✗[/red]" if n.failed else "[green]✓[/green]"
            dur = self._fmt_duration(duration_ms)
            tools = ""
            if n.tool_calls:
                head = ", ".join(n.tool_calls[:3])
                tail = f" (+{len(n.tool_calls) - 3})" if len(n.tool_calls) > 3 else ""
                tools = f"   [dim]tool: {head}{tail}[/dim]"
            err = ""
            if n.failed and n.error_message:
                err = f"  [red]error: {n.error_message}[/red]"
            self._console.print(f"[{n.step_index:02d}] {n.node_id:<30s} {mark}  {dur}{tools}{err}")
        self._current = None

    @staticmethod
    def _fmt_duration(ms: int) -> str:
        if ms < 1000:
            return f"{ms:>4d}ms"
        return f"{ms / 1000:>4.1f}s"
