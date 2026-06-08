# SPDX-License-Identifier: Apache-2.0
"""Single-node execution loop -- :func:`execute` wires §3.1.2 pseudocode (FR-1).

This module is the Phase 1 POC implementation of design §3.1.2's nine-step
runtime tick. It walks the IR's ``nodes`` list one at a time (no parallel/join
in v1 -- see :data:`stargraph.runtime.action.RoutingDecision` -- the
:class:`~stargraph.ir.ParallelAction` variant raises :class:`NotImplementedError`
in :func:`~stargraph.runtime.dispatch.dispatch_node`), emits a
:class:`TransitionEvent` per tick, and writes a :class:`Checkpoint` under
:func:`asyncio.shield` so a caller cancelling the run mid-write cannot tear
the row in half (FR-10).

Phase 2 refactor: the per-tick body (steps 1-9) lives in
:func:`stargraph.runtime.dispatch.dispatch_node` -- this module is now a thin
driver that owns the lifecycle transitions on :class:`~stargraph.graph.GraphRun`
and the terminal :class:`~stargraph.runtime.events.ResultEvent`.

The loop is deliberately a **skeleton** -- POC wiring keeps stubs at the
edges that have not yet landed:

* **Fathom integration** is gated on ``run.fathom`` being non-``None``. When
  present, mirror specs are derived from the post-merge state via
  :meth:`stargraph.fathom.FathomAdapter.mirror_state`, asserted off-thread
  (CLIPS is sync; :func:`asyncio.to_thread` shifts it off the event loop),
  and the resulting ``stargraph_action`` facts are translated into a
  :data:`~stargraph.runtime.action.RoutingDecision` via
  :func:`~stargraph.runtime.action.translate_actions`. When absent, the loop
  walks the static IR edge (next node in ``ir.nodes``) -- the empty-actions
  branch yields a :class:`~stargraph.runtime.action.ContinueAction` either way.
* **Capability gate** (design §4.2, FR-14, AC-4.1): before any node
  dispatches and before any tool the IR declares can be invoked, the
  loop consults :class:`stargraph.security.Capabilities`. Tool gate runs
  once at loop entry (covers every :class:`~stargraph.ir._models.ToolRef`
  in ``run.graph.ir.tools`` -- the engine's IR-level tool surface; the
  per-call gate inside :mod:`stargraph.runtime.tool_exec` lands later but
  the IR-declared set is the upper bound on what any tick can dispatch,
  so an entry-time sweep is sound). Node gate runs before each
  :func:`dispatch_node` call (and inside each parallel-branch factory).
  Deny on either path raises :class:`stargraph.errors.CapabilityError`
  (the typed exception per design §7 / NFR-7) and audit-emits a
  :class:`~stargraph.runtime.events.BosunAuditEvent` with
  ``fact={"kind": "capability_denied", ...}`` so the JSONL audit sink
  records the deny. ``run.capabilities is None`` (POC default on
  :class:`~stargraph.graph.run.GraphRun`) skips both gates -- backwards
  compatible with task 1.x EchoNode-only tests; the POC default
  hardens once a profile selector wires a default-deny instance in.
  Node-level gating reads ``getattr(node, "required_capability",
  None)`` -- IR :class:`~stargraph.ir._models.NodeSpec` does not yet
  declare the field (Phase 2 backfill); ``None`` is the no-check
  sentinel until the schema lands.
* **Cooperative cancel/pause boundaries** (design §4.2, FR-76, NFR-17,
  NFR-18) land here: after each :func:`dispatch_node` (and each
  :func:`_run_parallel_block`) returns, the loop checks
  ``run._cancel_event`` and ``run._pause_event``. Cancel raises
  :class:`asyncio.CancelledError` cooperatively (the
  :meth:`~stargraph.graph.run.GraphRun.cancel` call site already set
  ``state="cancelled"`` and emitted :class:`~stargraph.runtime.events.RunCancelledEvent`,
  so the loop just unwinds). Pause transitions ``state="paused"``,
  emits a terminal :class:`~stargraph.runtime.events.ResultEvent`, and
  returns cleanly (the :meth:`~stargraph.graph.run.GraphRun.pause` call
  site already emitted :class:`~stargraph.runtime.events.RunPausedEvent`,
  but does NOT mutate state -- that's the loop's job per design §4.1).
* **HITL interrupt dispatch** (design §4.4, §17 Decision #1, FR-81, AC-14.1):
  ``InterruptAction`` is a control-flow primitive, not a routing decision,
  so the loop intercepts it BEFORE :func:`~stargraph.runtime.action.translate_actions`
  produces a :data:`~stargraph.runtime.action.RoutingDecision`. The dispatch
  surface is the typed signal :class:`_HitInterrupt`: any node body, future
  :class:`~stargraph.ir._models.InterruptAction`-pre-screen in
  :func:`~stargraph.runtime.dispatch.dispatch_node`, or the upcoming
  :class:`InterruptNode` (task 1.16) raises it carrying the IR
  :class:`~stargraph.ir._models.InterruptAction` payload. The loop's
  ``except _HitInterrupt:`` arm transitions ``state="awaiting-input"``,
  emits :class:`~stargraph.runtime.events.WaitingForInputEvent` (with
  ``prompt`` / ``interrupt_payload`` / ``requested_capability`` from the
  signal payload), and returns a :class:`RunSummary` with
  ``status="running"`` (the run is alive, just paused on input -- no
  terminal :class:`ResultEvent` is emitted because awaiting-input is
  not a terminal state). Phase 1 surface keeps the dispatch path
  pollution-free: :data:`~stargraph.runtime.action.RoutingDecision` never
  carries an interrupt variant; the signal flow is orthogonal.
* **Resume-from-respond hook** (continuing past
  :meth:`~stargraph.graph.run.GraphRun.respond`): hot-resume on the same
  live coroutine, for both timeout shapes (#81). The ``_HitInterrupt``
  arm transitions ``state="awaiting-input"``, emits
  :class:`~stargraph.runtime.events.WaitingForInputEvent`, then blocks in
  :func:`_await_respond_or_timeout` on ``run._respond_event``.
  :meth:`~stargraph.graph.run.GraphRun.respond` flips ``state`` back to
  ``"running"``, asserts the response as a ``stargraph.evidence`` Fathom
  fact, and sets that event -- waking this coroutine, which advances past
  the interrupt node along the static IR edge to a terminal state. A
  ``timeout`` (if set) is an inline watchdog over the same wait; a
  ``timeout`` of ``None`` waits indefinitely. The legacy ``timeout is
  None`` cold-restart arm (which returned ``status="running"`` and left
  the run hung because nothing re-drove the loop after ``respond()``) is
  gone. Cross-process resume from a checkpoint after a restart is still
  the separate cold-restart path via
  :meth:`~stargraph.graph.run.GraphRun.resume`.
* **Side-effects hash**: empty string in v1 (no tool calls recorded yet).
* **JSON serialization** of state for the checkpoint goes through Pydantic's
  ``model_dump(mode='json')`` so the row matches the JCS contract on the
  storage side.

Halts on either:

1. :class:`~stargraph.ir.HaltAction` translated into a ``"halt"`` decision
   (FR-1 ``action.halt``), or
2. End-of-graph (the static IR edge runs off the end of ``ir.nodes``).

The function returns the :class:`~stargraph.checkpoint.protocol.RunSummary`
record for the completed run. The summary's ``status`` reflects the
terminal lifecycle state on :class:`stargraph.graph.GraphRun`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import anyio

from stargraph.checkpoint.protocol import RunSummary
from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.runtime.dispatch import dispatch_node
from stargraph.runtime.events import (
    BosunAuditEvent,
    InterruptTimeoutEvent,
    ResultEvent,
    WaitingForInputEvent,
)
from stargraph.runtime.merge import build_last_write_conflict_evidence
from stargraph.runtime.parallel import execute_parallel

if TYPE_CHECKING:
    from stargraph.graph.run import GraphRun
    from stargraph.ir._models import InterruptAction, NodeSpec, ParallelBlock

__all__ = ["execute"]


class _HitInterrupt(Exception):  # noqa: N818  -- control-flow signal, not an error
    """Internal control-flow signal carrying an :class:`InterruptAction` payload.

    Per design §17 Decision #1 (locked), ``InterruptAction`` is a control-flow
    primitive dispatched BEFORE :func:`~stargraph.runtime.action.translate_actions`
    -- it must NOT pollute the :data:`~stargraph.runtime.action.RoutingDecision`
    union. This typed signal is the dispatch surface: any node body, future
    :class:`~stargraph.ir._models.InterruptAction`-pre-screen in
    :func:`~stargraph.runtime.dispatch.dispatch_node`, or the upcoming
    :class:`InterruptNode` (task 1.16) raises it; the loop's ``except``
    arm transitions ``state="awaiting-input"``, emits
    :class:`~stargraph.runtime.events.WaitingForInputEvent`, and exits cleanly.

    The signal is module-private (underscore-prefixed) because it is part of
    the loop/run cooperative-boundary contract, mirroring the underscore
    convention on :attr:`GraphRun._cancel_event` / :attr:`GraphRun._pause_event`.
    Public surface for external callers is :class:`InterruptAction` itself
    (the IR variant); the signal is the wiring.
    """

    __slots__ = ("action",)

    def __init__(self, action: InterruptAction) -> None:
        super().__init__(f"interrupt requested: {action.prompt!r}")
        self.action = action


async def execute(run: GraphRun) -> RunSummary:
    """Drive ``run`` through the §3.1.2 nine-step tick until halt or end-of-graph.

    See module docstring for the skeleton boundary -- Fathom and parallel/join
    branches are deferred to later tasks. The capability gate (design §4.2)
    fires here: tool-set sweep at loop entry, node gate before each
    :func:`dispatch_node`. The loop transitions ``run.state`` ``pending`` →
    ``running`` on entry and ``running`` → ``done`` or ``failed`` on exit;
    the :class:`RunSummary` returned mirrors the final state.
    """
    if run.checkpointer is None:
        # The §3.1.2 step-7 ``asyncio.shield(checkpointer.write(...))`` cannot
        # run without a driver -- a checkpoint-less loop would be a silent
        # no-op for durability (FR-10). Fail loudly per FR-6.
        raise StargraphRuntimeError(
            "GraphRun has no checkpointer wired; runs must persist (FR-10)."
        )

    nodes = run.graph.ir.nodes
    started_at = datetime.now(UTC)
    if not nodes:
        # An empty graph is a degenerate halt: no work, no checkpoints, done.
        run.state = "done"
        await _emit_result(run, started_at, run.initial_state, status="done")
        return _summary(run, status="done")

    # Capability gate -- tool-set sweep (design §4.2, FR-14, AC-4.1). Every
    # ``ToolRef`` declared in the IR is the upper bound on what any tick can
    # dispatch; gating the entire IR-declared set at loop entry is a sound
    # superset of "before every tool dispatch" given the per-call gate inside
    # :mod:`stargraph.runtime.tool_exec` lands at task 1.27. Skipped silently
    # when ``run.capabilities is None`` (POC default; hardens once a profile
    # selector wires a default-deny instance into ``GraphRun``).
    await _check_tool_capabilities(run)

    state: Any = run.initial_state
    current_id: str | None = nodes[0].id  # Phase 1 POC: first node is the entry.
    step = 0
    run.state = "running"

    try:
        while current_id is not None:
            current_node = _lookup_node(nodes, current_id)
            # Capability gate -- per-node check before dispatch (design §4.2,
            # AC-13.7, AC-14.9, AC-15.5). Reads ``getattr(current_node,
            # "required_capability", None)``: IR ``NodeSpec`` does not yet
            # declare the field (Phase 2 backfill), so production runs see
            # ``None`` and skip; once the IR schema lands the same call
            # site enforces the gate without further wiring.
            await _check_node_capability(run, current_node)
            try:
                state, current_id = await dispatch_node(run, nodes, current_node, state, step)
            except _HitInterrupt as signal:
                # HITL interrupt boundary (design §4.4, §17 Decision #1, FR-81,
                # AC-14.1, FR-87/NFR-22 timeout per task 2.34).
                #
                # Both timeout shapes resume on this same live coroutine via
                # ``_await_respond_or_timeout`` (#81 -- the legacy cold-restart
                # arm for ``timeout is None`` returned ``status="running"`` and
                # left the run hung forever because nothing re-drove the loop
                # after ``respond()``):
                #
                # * ``timeout is None``: wait indefinitely on
                #   ``run._respond_event``. ``respond()`` wakes this coroutine,
                #   which advances past the interrupt node (next IR-edge target).
                # * ``timeout is not None`` (FR-87 inline watchdog): race
                #   ``anyio.move_on_after`` against the respond event. Respond
                #   wins -> resume past the interrupt node. Timer wins -> emit
                #   ``InterruptTimeoutEvent`` and apply the ``on_timeout`` policy
                #   (``"halt"`` or ``"goto:<node_id>"``).
                # Re-arm the respond handshake before parking (#81): a prior
                # interrupt on this same hot-resumed run leaves the event set,
                # which would otherwise let this wait() return instantly and
                # skip the pause (advancing past the node while ``state`` stays
                # a stale ``"awaiting-input"``). Must precede the state flip --
                # respond() is gated on state=="awaiting-input" -- keeping it
                # race-free.
                run._rearm_respond_gate()  # pyright: ignore[reportPrivateUsage]
                run.state = "awaiting-input"
                await _emit_waiting_for_input(run, signal.action, step)
                outcome = await _await_respond_or_timeout(
                    run, signal.action, step, current_id=current_node.id, nodes=nodes
                )
                if outcome.terminal_status is not None:
                    await _emit_result(run, started_at, state, status=outcome.terminal_status)
                    return _summary(run, status=outcome.terminal_status)
                # Non-terminal outcome: continue the loop at the resolved id
                # (either the next IR-edge target after respond, or the
                # ``goto:<node_id>`` target on timeout-with-goto).
                current_id = outcome.next_id
                step += 1
                continue
            step += 1
            # Cooperative cancel/pause boundary (design §4.2, FR-76, NFR-17,
            # NFR-18). ``dispatch_node`` has just written a shielded
            # checkpoint at step-1; this is the safe re-entry point to
            # observe the per-run signals set by :meth:`GraphRun.cancel` /
            # :meth:`GraphRun.pause`. Cancel is preferred over pause when
            # both are set (terminal trumps transient).
            if run._cancel_event.is_set():  # pyright: ignore[reportPrivateUsage]
                # ``cancel()`` already set ``state="cancelled"`` and emitted
                # ``RunCancelledEvent``; raising :class:`asyncio.CancelledError`
                # unwinds tools/nodes cooperatively per NFR-17. The outer
                # ``except Exception:`` does not catch ``CancelledError``
                # (BaseException subtree), so the cancelled state survives.
                # ``_cancel_event`` / ``_pause_event`` are intentionally
                # underscore-prefixed on :class:`GraphRun` -- they are
                # cooperative-boundary signals owned by the run/loop pair,
                # not part of the public surface; loop is the one place
                # outside ``GraphRun`` that consults them (design §4.2).
                raise asyncio.CancelledError("run cancelled cooperatively")
            if run._pause_event.is_set():  # pyright: ignore[reportPrivateUsage]
                # ``pause()`` already emitted ``RunPausedEvent``; the loop
                # owns the state transition + clean exit per design §4.1.
                # Checkpoint persistence has already happened inside
                # ``dispatch_node`` (step-1's shielded write); resume is the
                # standard cold-restart path via :meth:`GraphRun.resume`.
                run.state = "paused"
                await _emit_result(run, started_at, state, status="paused")
                return _summary(run, status="paused")
            # Phase 3 wiring: if the next node opens a parallel block declared
            # in ``ir.parallel``, fan out via :func:`execute_parallel` and
            # resume from the block's ``join`` target. The static IR-edge
            # walk would otherwise traverse the targets sequentially without
            # firing branch lifecycle events (FR-13, design §3.7.1).
            block = _parallel_block_for(run.graph.ir.parallel, current_id)
            if block is not None:
                try:
                    state, current_id = await _run_parallel_block(run, nodes, block, state, step)
                except _HitInterrupt as signal:
                    # Same interrupt boundary post-parallel-fan-out: any branch
                    # raising ``_HitInterrupt`` propagates through
                    # :func:`execute_parallel`'s task-group surface; the loop
                    # owns the state transition + clean exit. Timeout policy
                    # (task 2.34) mirrors the single-node arm above.
                    # Re-arm before parking (#81), as in the single-node arm:
                    # precede the state flip so a stale set from a prior
                    # interrupt cannot skip this pause.
                    run._rearm_respond_gate()  # pyright: ignore[reportPrivateUsage]
                    run.state = "awaiting-input"
                    await _emit_waiting_for_input(run, signal.action, step)
                    # Both timeout shapes resume on this live coroutine (#81).
                    # Post-parallel resume: pass the block's first target as
                    # the "current" id so a respond resolution advances along
                    # the static IR edge from there. ``current_id`` is the
                    # block's ``join`` target (or ``None``); not the right
                    # anchor for the next-edge walk.
                    branch_anchor = block.targets[0]
                    parallel_outcome = await _await_respond_or_timeout(
                        run, signal.action, step, current_id=branch_anchor, nodes=nodes
                    )
                    if parallel_outcome.terminal_status is not None:
                        await _emit_result(
                            run, started_at, state, status=parallel_outcome.terminal_status
                        )
                        return _summary(run, status=parallel_outcome.terminal_status)
                    current_id = parallel_outcome.next_id
                    step += 1
                    continue
                step += 1
                # Same cooperative boundary post-parallel-fan-out: each
                # branch's :func:`dispatch_node` has written its own
                # checkpoint, and the merged step is now the safe re-entry
                # point for cancel/pause observation.
                if run._cancel_event.is_set():  # pyright: ignore[reportPrivateUsage]
                    raise asyncio.CancelledError("run cancelled cooperatively")
                if run._pause_event.is_set():  # pyright: ignore[reportPrivateUsage]
                    run.state = "paused"
                    await _emit_result(run, started_at, state, status="paused")
                    return _summary(run, status="paused")
    except asyncio.CancelledError:
        # Cooperative cancel: ``run.state`` was set to ``"cancelled"`` by
        # :meth:`GraphRun.cancel` before the boundary observed the event,
        # and the typed :class:`~stargraph.runtime.events.RunCancelledEvent`
        # was emitted there too -- the loop deliberately does NOT emit a
        # duplicate terminal :class:`ResultEvent` for cancel because
        # ``ResultEvent.status`` is currently ``Literal["done","failed","paused"]``
        # (widening to add ``"cancelled"`` is a downstream events-module
        # change, out of scope for this task per Files=loop.py only).
        # Re-raise so callers of :meth:`GraphRun.start` / :meth:`GraphRun.wait`
        # see the cooperative-cancel signal per NFR-17.
        raise
    except Exception as exc:
        run.state = "failed"
        # Record the failure reason on the run (#68) so any post-mortem
        # reader (and, on the re-raise, the scheduler's exception handler)
        # can distinguish a node error from an interrupt timeout. The
        # scheduler also derives the same pair from the propagated
        # exception; setting it here keeps the run handle self-describing.
        run.error_class = type(exc).__name__
        run.error_cause = str(exc)
        raise

    run.state = "done"
    await _emit_result(run, started_at, state, status="done")
    return _summary(run, status="done")


class _InterruptOutcome:
    """Resolution of an :class:`InterruptAction`-with-timeout race (task 2.34).

    Two distinct outcomes the loop arm must distinguish:

    * **Non-terminal resume** -- ``terminal_status is None`` and
      ``next_id`` is the node id to dispatch next. Two sub-cases produce
      this shape: (a) respond arrived in time, the loop should advance
      past the interrupt node along the static IR edge; (b) timer fired
      with ``on_timeout="goto:<node_id>"``, the loop resumes at the
      named target (skipping the interrupt as if it were never present).

    * **Terminal halt** -- ``terminal_status="failed"`` (timeout +
      ``on_timeout="halt"``). The caller emits a terminal
      :class:`~stargraph.runtime.events.ResultEvent` and returns the
      summary; ``next_id`` is unused.

    A small typed value object beats threading a ``str | None`` +
    ``status: Literal[...] | None`` tuple through two return paths --
    the named fields make the post-race branch readable.
    """

    __slots__ = ("next_id", "terminal_status")

    def __init__(self, *, next_id: str | None, terminal_status: str | None) -> None:
        self.next_id = next_id
        self.terminal_status = terminal_status


async def _await_respond_or_timeout(
    run: GraphRun,
    action: InterruptAction,
    step: int,
    *,
    current_id: str,
    nodes: list[NodeSpec],
) -> _InterruptOutcome:
    """Block on ``respond()``, optionally under a timeout watchdog (FR-87, NFR-22).

    When ``action.timeout is None`` the wait is unbounded -- the loop parks
    on ``run._respond_event`` until :meth:`GraphRun.respond` wakes it (#81).
    When a timeout is set, ``anyio.move_on_after(timeout)`` races the same
    respond event.

    Per design §9.5 + locked Decision #1, the loop's ``_HitInterrupt``
    arm handles ``InterruptAction.timeout`` inline (not via a detached
    background task) so the timeout-vs-respond decision and downstream
    state transition stay on the same coroutine. ``anyio.move_on_after``
    is the canonical anyio timeout primitive (over
    ``asyncio.wait_for``); the whole loop is anyio-native per Stargraph's
    convention.

    Branches:

    * Respond arrives within the timeout: ``run.state`` was flipped back
      to ``"running"`` by :meth:`GraphRun.respond` (which also asserted
      the ``stargraph.evidence`` fact + emitted the audit event); the loop
      advances past the interrupt node along the static IR edge --
      ``next_id = next-IR-edge after current_id``.

    * Timer fires first: emit
      :class:`~stargraph.runtime.events.InterruptTimeoutEvent` carrying
      ``action.on_timeout``. Apply the policy:

      - ``"halt"`` (default if unset): set ``run.state="failed"``,
        return ``terminal_status="failed"``. Caller emits the terminal
        :class:`~stargraph.runtime.events.ResultEvent`.
      - ``"goto:<node_id>"``: parse the target id, set ``run.state="running"``,
        return ``next_id=<target>``. The loop resumes at the target as
        if the interrupt were never present.

    Precision (NFR-22): :func:`anyio.move_on_after` is bounded by the
    event loop's scheduler granularity (sub-ms on Linux). The ±100ms
    budget is the audit-log envelope, not the timer's accuracy floor.
    """
    responded = False
    if action.timeout is None:
        # No watchdog (#81): wait indefinitely for respond(). respond()
        # flips state back to "running" and sets _respond_event, waking
        # this same coroutine so it advances past the interrupt node.
        await run._respond_event.wait()  # pyright: ignore[reportPrivateUsage]
        responded = True
    else:
        with anyio.move_on_after(action.timeout.total_seconds()):
            await run._respond_event.wait()  # pyright: ignore[reportPrivateUsage]
            responded = True

    if responded:
        # Respond arrived: state has already been flipped to "running"
        # by GraphRun.respond. Advance past the interrupt node via the
        # static IR edge -- the same _next_node_id helper dispatch_node
        # uses on a ContinueAction.
        from stargraph.runtime.dispatch import _next_node_id  # pyright: ignore[reportPrivateUsage]

        return _InterruptOutcome(
            next_id=_next_node_id(nodes, current_id),
            terminal_status=None,
        )

    # Timer fired first: emit InterruptTimeoutEvent and apply on_timeout.
    on_timeout = action.on_timeout
    await run.bus.send(
        InterruptTimeoutEvent(
            run_id=run.run_id,
            step=step,
            ts=datetime.now(UTC),
            on_timeout=on_timeout,
        ),
        fathom=run.fathom,
    )

    # ``InterruptAction.on_timeout`` is ``Literal["halt"] | str`` with default
    # ``"halt"``, so the field is always a non-empty string at this point. An
    # IR loader that drops an explicit ``on_timeout=""`` is filtered upstream
    # by the IR-load warning per task 2.34 #4 (defaults to ``"halt"`` with a
    # logged warning at IR-load time, never a runtime crash).
    if on_timeout == "halt":
        run.state = "failed"
        # Record *why* the run failed (#68): a HITL interrupt that timed out
        # waiting for a respond is a distinct failure class from a node
        # error -- the scheduler threads these onto the ``runs_history`` row.
        run.error_class = "interrupt_timeout"
        run.error_cause = f"interrupt timed out after {action.timeout}; on_timeout=halt"
        return _InterruptOutcome(next_id=None, terminal_status="failed")
    if on_timeout.startswith("goto:"):
        target = on_timeout[len("goto:") :]
        if not target:
            raise StargraphRuntimeError(
                f"InterruptAction.on_timeout {on_timeout!r} has empty goto target"
            )
        run.state = "running"
        return _InterruptOutcome(next_id=target, terminal_status=None)
    raise StargraphRuntimeError(
        f"InterruptAction.on_timeout {on_timeout!r} is not 'halt' or 'goto:<node_id>'"
    )


async def _emit_waiting_for_input(
    run: GraphRun,
    action: InterruptAction,
    step: int,
) -> None:
    """Publish :class:`WaitingForInputEvent` for an HITL interrupt (FR-81, AC-14.1).

    The event carries ``prompt`` / ``interrupt_payload`` / ``requested_capability``
    from the IR :class:`InterruptAction`. ``timeout`` and ``on_timeout`` are
    not surfaced in :class:`WaitingForInputEvent` (timeout enforcement is the
    InterruptNode's responsibility per task 1.16; the loop's role is the
    state transition + event emission only).
    """
    event = WaitingForInputEvent(
        run_id=run.run_id,
        step=step,
        ts=datetime.now(UTC),
        prompt=action.prompt,
        interrupt_payload=dict(action.interrupt_payload),
        requested_capability=action.requested_capability,
    )
    await run.bus.send(event, fathom=run.fathom)


async def _emit_result(
    run: GraphRun,
    started_at: datetime,
    state: Any,
    *,
    status: str,
) -> None:
    """Publish a terminal :class:`ResultEvent` on the run's bus (FR-14).

    Wraps the ``status`` literal cast for the ``done|failed|paused`` union and
    snapshots ``state`` via ``model_dump(mode="json")`` so the JSONL audit sink
    sees the same shape as the checkpointer's ``state`` blob.
    """
    if status not in ("done", "failed", "paused"):
        raise StargraphRuntimeError(f"invalid terminal status: {status!r}")
    duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    final_state: dict[str, Any] = (
        state.model_dump(mode="json") if hasattr(state, "model_dump") else {}
    )
    event = ResultEvent(
        run_id=run.run_id,
        step=0,
        ts=datetime.now(UTC),
        status=status,
        final_state=final_state,
        run_duration_ms=duration_ms,
    )
    await run.bus.send(event, fathom=run.fathom)


def _lookup_node(nodes: list[NodeSpec], node_id: str) -> NodeSpec:
    """Return the :class:`NodeSpec` with ``id == node_id`` or raise :class:`KeyError`."""
    for node in nodes:
        if node.id == node_id:
            return node
    raise KeyError(f"no node with id={node_id!r} in graph")


def _parallel_block_for(blocks: list[ParallelBlock], next_id: str | None) -> ParallelBlock | None:
    """Return the IR ``ParallelBlock`` whose first target equals ``next_id``.

    Phase-3 minimal wire: the loop intercepts when the static IR edge would
    enter the head of a declared parallel block and reroutes through
    :func:`execute_parallel` so branch lifecycle events fire (FR-13).
    """
    if next_id is None:
        return None
    for block in blocks:
        if block.targets and block.targets[0] == next_id:
            return block
    return None


async def _run_parallel_block(
    run: GraphRun,
    nodes: list[NodeSpec],
    block: ParallelBlock,
    state: Any,
    step: int,
) -> tuple[Any, str | None]:
    """Fan out ``block.targets`` via :func:`execute_parallel`, return ``(state, join)``.

    Each branch dispatches its target node once and returns the post-merge
    state. The merged result is the last branch's state (last-write-wins
    placeholder; FR-11 typed merge lands later). Branch lifecycle events
    (BranchStarted/Completed) fire through the bus so consumers (JSONL
    audit sink, future Fathom mirror) observe FR-13's contract.
    """

    def _branch_factory(target_id: str, branch_step: int) -> Any:
        async def _run() -> Any:
            target_node = _lookup_node(nodes, target_id)
            # Capability gate inside each parallel branch (design §4.2). The
            # tool-set sweep at loop entry already covered the IR-declared
            # tool surface; per-node ``required_capability`` is checked here
            # before fan-out so a denied branch raises before its
            # ``dispatch_node`` writes a checkpoint.
            await _check_node_capability(run, target_node)
            new_state, _ = await dispatch_node(run, nodes, target_node, state, branch_step)
            return new_state

        return _run

    factories = [_branch_factory(target, step + idx) for idx, target in enumerate(block.targets)]
    results = await execute_parallel(
        factories,
        strategy=block.strategy,
        bus=run.bus,
        fathom=run.fathom,
        run_id=run.run_id,
        step=step,
    )
    # Reducer-aware merge across branches (FR-11). Disjoint fields merge
    # silently; conflicts without a declared reducer raise with evidence.
    merged_state = _merge_branch_results(results, ir=run.graph.ir, reducer_registry=None)
    next_id = block.join or None
    return merged_state, next_id


def _merge_branch_results(
    results: list[Any],
    *,
    ir: Any,
    reducer_registry: Any,
) -> Any:
    """Reducer-aware merge across parallel-block branch outputs (FR-11).

    For each field written by any branch:

    * If only one branch wrote it, accept that value unconditionally.
    * If multiple branches wrote the same field and ``reducer_registry``
      has a reducer registered under that field name, apply
      ``reducer(a, b)`` (pairwise left-fold across all branch values).
    * If multiple branches wrote the same field and no reducer is
      declared, raise :class:`stargraph.errors.StargraphRuntimeError` with
      :func:`stargraph.runtime.merge.build_last_write_conflict_evidence`
      payload (force-loud per FR-11).

    ``ir`` is accepted for future use (reducer lookup by IR field
    annotation); unused in the current implementation where
    ``reducer_registry`` carries the field→callable map directly.
    """
    del ir  # reserved for future IR-annotation-driven reducer lookup

    # Collect all keys and per-field branch values.
    all_keys: dict[str, list[Any]] = {}
    for branch in results:
        if not isinstance(branch, dict):
            # Non-dict branch state: no field-level merge possible.
            # Return the last branch's state (degenerate case).
            return results[-1]
        branch_dict = cast("dict[str, Any]", branch)
        for key, value in branch_dict.items():
            all_keys.setdefault(key, []).append(value)

    merged: dict[str, Any] = {}
    for field, values in all_keys.items():
        if len(values) == 1:
            # Disjoint: only one branch wrote this field.
            merged[field] = values[0]
        else:
            # Conflict: multiple branches wrote the same field.
            if reducer_registry is not None:
                try:
                    reducer = reducer_registry.get(field)
                except (KeyError, Exception):
                    reducer = None
            else:
                reducer = None

            if reducer is not None:
                # Apply reducer as binary combine(a, b) left-fold.
                result = values[0]
                for v in values[1:]:
                    result = reducer(result, v)
                merged[field] = result
            else:
                evidence = build_last_write_conflict_evidence(
                    field=field,
                    n_branches=len(values),
                    original_confidence=1.0,
                )
                raise StargraphRuntimeError(
                    f"parallel branch conflict on field {field!r}: "
                    "no reducer declared (FR-11 force-loud). "
                    f"Evidence: {evidence!r}",
                )
    return merged


async def _check_tool_capabilities(run: GraphRun) -> None:
    """Gate every IR-declared tool through :class:`Capabilities` (design §4.2).

    Walks ``run.graph.ir.tools`` and resolves each :class:`ToolRef` against
    ``run.graph.registry``; for every resolved tool calls
    ``run.capabilities.check(tool.spec)``. On deny, audit-emits a typed
    :class:`BosunAuditEvent` (``fact={"kind": "capability_denied", ...}``)
    via :attr:`run.bus` so the JSONL audit sink records it (Resolved Decision
    #5: single sink), then raises :class:`stargraph.errors.CapabilityError`.

    Skipped silently when ``run.capabilities is None`` (POC default) or when
    the graph has no registry / declares no tools -- the gate is intentionally
    a no-op until a profile selector wires a default-deny instance in.

    A :class:`ToolRef` whose id is unknown to the registry is *not* a
    capability-deny: it surfaces through :class:`stargraph.errors.PluginLoadError`
    at the registry boundary, not here. We catch that path and re-raise so
    a missing-tool failure does not masquerade as a capability denial.
    """
    if run.capabilities is None:
        return
    registry = run.graph.registry
    if registry is None:
        return
    for tool_ref in run.graph.ir.tools:
        try:
            tool = registry.get_tool(tool_ref.id)
        except Exception:
            # Registry-side failures (missing id, etc.) are PluginLoadError
            # territory -- propagate as-is. The capability gate concerns
            # itself only with the deny path on a resolved tool.
            raise
        spec = tool.spec
        if run.capabilities.check(spec):
            continue
        # Build a stable tool id matching the registry convention
        # (``namespace.name@version`` per design §3.5) so the audit
        # consumer can correlate denies to the registry surface.
        tool_id = f"{spec.namespace}.{spec.name}@{spec.version}"
        await _emit_capability_denied(
            run,
            kind="tool",
            subject=tool_id,
            permissions=list(spec.permissions),
        )
        raise CapabilityError(
            f"capability denied for tool {tool_id!r}: required {spec.permissions!r}",
            capability=",".join(spec.permissions),
            tool_id=tool_id,
            deployment="loop",
        )


async def _check_node_capability(run: GraphRun, node: NodeSpec) -> None:
    """Gate node execution through :meth:`Capabilities.has_permission` (design §4.2).

    Reads ``getattr(node, "required_capability", None)`` so the call site
    is forward-compatible with the Phase 2 IR backfill that adds the
    field to :class:`stargraph.ir._models.NodeSpec`. ``None`` (the current
    Phase 1 default for every node) skips the gate; otherwise the
    capability string is parsed by :meth:`Capabilities.has_permission`
    (``"<name>"`` or ``"<name>:<scope>"``).

    On deny, audit-emits :class:`BosunAuditEvent` and raises
    :class:`CapabilityError`. Skipped silently when ``run.capabilities
    is None``.
    """
    if run.capabilities is None:
        return
    required: str | None = getattr(node, "required_capability", None)
    if required is None:
        return
    if run.capabilities.has_permission(required):
        return
    await _emit_capability_denied(
        run,
        kind="node",
        subject=node.id,
        permissions=[required],
    )
    raise CapabilityError(
        f"capability denied for node {node.id!r}: required {required!r}",
        capability=required,
        tool_id=None,
        deployment="loop",
    )


async def _emit_capability_denied(
    run: GraphRun,
    *,
    kind: str,
    subject: str,
    permissions: list[str],
) -> None:
    """Publish a typed :class:`BosunAuditEvent` for a capability-deny (design §4.2).

    ``kind`` distinguishes ``"tool"`` vs ``"node"``; ``subject`` is the
    deny target's id; ``permissions`` is the list of required strings the
    grant set failed to cover. ``pack_id="stargraph.runtime"`` mirrors the
    convention established by :meth:`GraphRun.respond` (task 1.7) for
    runtime-emitted (non-Bosun-pack) audit facts -- the pack-id namespace
    is reserved for runtime concerns when no Bosun pack is the source.
    """
    now = datetime.now(UTC)
    # ProvenanceBundle (FR-55, AC-11.2): capability denials at the
    # runtime tool/node gate are system-emitted; origin="system" with
    # pack id as source matches the stargraph.runtime respond convention.
    provenance: dict[str, Any] = {
        "origin": "system",
        "source": "stargraph.runtime",
        "run_id": run.run_id,
        "step": 0,
        "confidence": 1.0,
        "timestamp": now.isoformat(),
    }
    event = BosunAuditEvent(
        run_id=run.run_id,
        step=0,
        ts=now,
        pack_id="stargraph.runtime",
        pack_version="1.0",
        fact={
            "kind": "capability_denied",
            "subject_kind": kind,
            "subject": subject,
            "permissions": permissions,
        },
        provenance=provenance,
    )
    await run.bus.send(event, fathom=run.fathom)


def _summary(run: GraphRun, *, status: str) -> RunSummary:
    """Build the terminal :class:`RunSummary` for ``run``.

    Phase 1 POC: ``started_at == last_step_at`` (a real start-time field on
    :class:`GraphRun` lands when run-history reconstruction matures). The
    ``status`` literal is constrained by :class:`RunSummary` itself.
    """
    now = datetime.now(UTC)
    return RunSummary(
        run_id=run.run_id,
        graph_hash=run.graph.graph_hash,
        started_at=now,
        last_step_at=now,
        status=status,  # pyright: ignore[reportArgumentType] -- caller passes valid Literal
        parent_run_id=run.parent_run_id,
        # Terminal failure diagnostics (#68): ``None`` unless the run set
        # them on a failed terminal path (interrupt-timeout-halt below, or
        # the ``except Exception`` arm in :func:`execute`).
        error_class=run.error_class,
        error_cause=run.error_cause,
    )
