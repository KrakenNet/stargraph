# SPDX-License-Identifier: Apache-2.0
"""stargraph.GraphRun -- async execution handle (design §3.1.1, §3.1.3, FR-1, US-2).

Per design §3.1.1, ``GraphRun`` is the async execution half of the Temporal-style
Graph/GraphRun split. It is **single-use** (Open Q3 resolution): one ``run_id``
per ``GraphRun``. ``Graph.start()`` returns a fresh handle bound to the parent
graph's ``graph_hash``; ``resume()`` returns a *new* GraphRun bound to the same
``run_id`` (continuation of the same logical run); ``counterfactual()`` returns
a new GraphRun bound to a *new* ``run_id`` (Temporal "cannot change the past"
invariant -- the original event log is byte-identical post-execution).

Phase 1 ships intentional stubs (per .progress.md):

- :meth:`GraphRun.stream` -- async generator, no events yielded yet
  (full implementation arrives with the run-loop wiring in task 1.16+).
- :meth:`GraphRun.wait` -- raises :class:`NotImplementedError`; full
  implementation arrives mid-Phase 1 once :mod:`stargraph.graph.loop` lands.
- :meth:`GraphRun.checkpoint` -- raises :class:`NotImplementedError`; the
  Checkpointer Protocol + drivers land in tasks 1.20-1.21.
- :meth:`GraphRun.resume` / :meth:`GraphRun.counterfactual` -- both
  raise :class:`NotImplementedError` ("Phase 3 fills"); full implementations
  arrive in tasks 3.26 and 3.33 respectively.

Lifecycle states (design §3.1.3): ``pending|running|paused|done|failed``. The
state field is exposed for inspection (CLI ``stargraph inspect``, tests) but
transitions are driven by the run loop, not by external callers.

The ``Event``, ``RunSummary``, and ``Checkpoint`` types are typed as
:data:`typing.Any` placeholders here; the real classes land in their owning
modules (``stargraph.events``, ``stargraph.graph.loop``, ``stargraph.checkpoint``) and
will replace these annotations via re-export when those tasks complete. Using
``Any`` (rather than forward-string refs to non-existent classes) keeps pyright
strict-mode green without forcing speculative type imports.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

import anyio
from pydantic import BaseModel, create_model

from stargraph.errors import CheckpointError, StargraphRuntimeError
from stargraph.runtime.bus import EventBus
from stargraph.runtime.mirror_lifecycle import MirrorScheduler

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from stargraph.checkpoint.protocol import Checkpoint as _CheckpointModel
    from stargraph.checkpoint.protocol import Checkpointer
    from stargraph.checkpoint.protocol import RunSummary as _RunSummary
    from stargraph.graph.definition import Graph
    from stargraph.nodes.base import NodeBase

__all__ = ["GraphRun", "RunState"]


# Design §3.1.3 / stargraph-serve-and-bosun §4.1 lifecycle states. Literal (not
# Enum) per the simpler-typing guidance in the task spec; transitions are
# documented in the design table rather than enforced by a state machine class
# for the Phase 1 skeleton. The HITL/cancel/pause extension (FR-76, FR-77,
# FR-85, FR-88; AC-13.5) widens this to the design §4.1 set:
# ``awaiting-input`` is the HITL pause state, ``cancelled`` is a terminal
# cooperative-cancel state, and ``error`` is the design's preferred label for
# unhandled-exception terminals. The historical ``failed`` label remains in
# the union as a transitional alias because :mod:`stargraph.graph.loop` still
# writes ``state = "failed"`` on unhandled exceptions; tasks 1.5-1.7 land the
# ``cancel()``/``pause()``/``respond()`` methods + loop wiring and will
# rename ``failed`` → ``error`` at the loop call site (after which this alias
# can drop). This task is type-only -- it widens the surface so follow-up
# tasks can land without each one reopening the Literal alias.
RunState = Literal[
    "pending",
    "running",
    "paused",
    "awaiting-input",
    "done",
    "cancelled",
    "error",
    "failed",
]


# Phase 1 placeholder aliases. These widen to ``Any`` so pyright strict-mode
# stays green without importing modules that don't exist yet. The owning
# modules (``stargraph.events``, ``stargraph.graph.loop``, ``stargraph.checkpoint``)
# will replace these with real classes via re-export when their tasks land.
Event = Any
RunSummary = Any
Checkpoint = Any


# --------------------------------------------------------------------------- #
# Resume helpers (task 3.26)                                                  #
# --------------------------------------------------------------------------- #


# Prefix planted by counterfactual forks (FR-27 derived hash). The cf-derived
# graph_hash is computed as ``sha256("stargraph-cf-v1\x00" + ...)``; the storage
# layer (task 3.33) prepends this literal to mark the row as cf-origin so
# ``resume()`` can refuse it loudly per AC-3.4.
_CF_HASH_PREFIX = "stargraph-cf-v1"


# Pydantic primitive type-name → Python type map for the dynamic state-model
# rebuild used by ``resume`` when no parent ``Graph`` is supplied. Mirrors
# (a deliberate subset of) ``stargraph.graph.definition._TYPE_MAP``.
_STATE_VALUE_TYPE: dict[type, type] = {
    int: int,
    str: str,
    bool: bool,
    bytes: bytes,
    float: float,
}


async def _load_checkpoint(
    checkpointer: Checkpointer,
    run_id: str,
    from_step: int | None,
) -> _CheckpointModel:
    """Read the requested checkpoint or raise :class:`CheckpointError`."""
    if from_step is None:
        ckpt = await checkpointer.read_latest(run_id)
        if ckpt is None:
            raise CheckpointError(
                "no checkpoint found for run_id; cannot resume",
                run_id=run_id,
                reason="no-checkpoint",
            )
        return ckpt

    ckpt = await checkpointer.read_at_step(run_id, from_step)
    if ckpt is None:
        raise CheckpointError(
            f"no checkpoint found at run_id={run_id!r} step={from_step}",
            run_id=run_id,
            step=from_step,
            reason="missing-step",
        )
    return ckpt


def _refuse_cf_prefix(ckpt: _CheckpointModel) -> None:
    """Refuse cf-derived checkpoints on resume (AC-3.4, FR-27)."""
    if ckpt.graph_hash.startswith(_CF_HASH_PREFIX):
        raise CheckpointError(
            "checkpoint graph_hash carries the counterfactual cf-prefix; "
            "resume() refuses cf-derived rows against the original run_id",
            run_id=ckpt.run_id,
            actual_hash=ckpt.graph_hash,
            reason="cf-prefix-hash-refused",
        )


def _migrate_block_applies(graph: Graph, ckpt: _CheckpointModel) -> bool:
    """Return ``True`` if a migrate block bridges checkpoint → current hash."""
    for block in graph.ir.migrate:
        if block.from_hash == ckpt.graph_hash and block.to_hash == graph.graph_hash:
            return True
    return False


def _validate_graph_hash(ckpt: _CheckpointModel, graph: Graph | None) -> None:
    """Refuse on ``graph_hash`` mismatch unless an IR migrate block applies (FR-20)."""
    if graph is None:
        return
    if graph.graph_hash == ckpt.graph_hash:
        return
    migrate_available = _migrate_block_applies(graph, ckpt)
    if migrate_available:
        return
    raise CheckpointError(
        "graph_hash mismatch on resume; no migrate block applies",
        run_id=ckpt.run_id,
        expected_hash=graph.graph_hash,
        actual_hash=ckpt.graph_hash,
        migrate_available=False,
        reason="graph-hash-mismatch",
    )


def _state_model_from_dict(state: dict[str, Any], *, run_id: str) -> BaseModel:
    """Build a dynamic :class:`BaseModel` instance from the checkpoint state dict.

    Resume tests round-trip ``run.initial_state.model_dump()`` and assert the
    field values. When the caller does not pass a parent ``Graph`` (so the
    compiled state schema is unavailable), this helper synthesizes a
    field-for-field model from the persisted state -- enough for round-trip
    inspection and for the empty-IR loop driver below to publish a terminal
    :class:`~stargraph.runtime.events.ResultEvent`.
    """
    fields: dict[str, Any] = {}
    for name, value in state.items():
        value_type: type = type(value)  # pyright: ignore[reportUnknownVariableType]
        py_type = _STATE_VALUE_TYPE.get(value_type, value_type)
        fields[name] = (py_type, ...)
    safe_id = "".join(ch if ch.isalnum() else "_" for ch in run_id) or "run"
    model_cls = create_model(f"StargraphResumeState_{safe_id}", __base__=BaseModel, **fields)
    return model_cls(**state)


def _build_resume_stub_graph(ckpt: _CheckpointModel) -> Graph:
    """Build a minimal parent :class:`Graph` for resumes that omit ``graph=...``.

    The 6 ``test_resume_latest`` / ``test_resume_from_step`` cases call
    ``GraphRun.resume(cp, run_id)`` without supplying the parent graph. They
    cover the "loaded latest / pinned step" half of FR-19 -- not the
    hash-mismatch half (which always passes ``graph=...``). Constructing a
    minimal IR keeps the lifecycle wiring honest: the resumed handle has a
    real :class:`Graph`, ``checkpointer`` is non-``None``, and
    :func:`~stargraph.graph.loop.execute` can drive forward to ``"done"`` via
    the empty-nodes terminal branch.
    """
    # Local import to avoid the foundation/runtime import cycle (Graph imports
    # GraphRun via TYPE_CHECKING; resume runs after both modules are loaded).
    from stargraph.graph.definition import Graph
    from stargraph.ir._models import IRDocument

    del ckpt  # the stub graph carries no IR-shape info from the checkpoint
    ir = IRDocument(ir_version="1.0.0", id="run:resume-stub", nodes=[])
    return Graph(ir)


class GraphRun:
    """Live, single-use execution handle for one ``Graph.start()`` invocation.

    Construction is cheap and side-effect free: the run is in ``pending`` state
    until the run loop begins (task 1.27). The constructor pins identity
    (``run_id``, parent ``graph``, optional ``parent_run_id``) and the runtime
    wiring (event bus, mirror scheduler, node registry, optional checkpointer
    /capabilities/Fathom adapter). The wiring fields default so callers that
    only need the typed handle (tests, replay constructors) can omit them.

    Attributes:
        run_id: UUIDv7 (or caller-supplied) string. Stable across restarts;
            re-used by :meth:`resume` to bind a fresh GraphRun to the same
            logical run.
        graph: The parent :class:`stargraph.graph.Graph` instance. The run holds a
            strong reference so ``graph_hash``, ``runtime_hash``, the compiled
            state schema, and the IR are all reachable from the handle.
        parent_run_id: ``None`` for fresh runs; set on counterfactual children
            to point at the original run whose checkpoint was forked.
        state: Current lifecycle state. Defaults to ``"pending"``; transitions
            are driven by :func:`stargraph.graph.loop.execute` (task 1.27).
        initial_state: The Pydantic state model the run starts from. ``None``
            until :meth:`start` is invoked.
        node_registry: Mapping ``node_id -> NodeBase`` consulted at dispatch
            time by the loop. The Phase 1 POC populates this from the graph's
            registry; the typed factory lands when ``Graph`` knows how to
            resolve ``NodeSpec.kind`` to a concrete :class:`NodeBase` instance.
        checkpointer: Optional :class:`stargraph.checkpoint.Checkpointer`. The
            loop calls :meth:`Checkpointer.write` once per step under
            :func:`asyncio.shield`. ``None`` is unsupported by the loop -- a
            run without a checkpointer is a logic error.
        bus: The :class:`stargraph.runtime.bus.EventBus` the loop publishes
            transition / token / tool events to. Constructed eagerly so
            ``stream()`` can subscribe before the loop starts.
        mirror_scheduler: The :class:`stargraph.runtime.mirror_lifecycle.MirrorScheduler`
            that buckets mirror specs by lifecycle and retracts ``"step"``
            mirrors at the node boundary (design §3.1.2 step 8).
        capabilities: Optional :class:`stargraph.security.Capabilities` gate.
            ``None`` means no capability enforcement (POC default; hardens
            once tool execution lands).
        fathom: Optional :class:`stargraph.fathom.FathomAdapter`. ``None`` skips
            mirror/assert/evaluate (POC stub) and routes via the static IR
            edge; non-``None`` runs the full §3.1.2 path.
    """

    run_id: str
    graph: Graph
    parent_run_id: str | None
    state: RunState
    initial_state: BaseModel | None
    node_registry: dict[str, NodeBase]
    checkpointer: Checkpointer | None
    bus: EventBus
    mirror_scheduler: MirrorScheduler
    capabilities: Any
    fathom: Any
    _cancel_event: anyio.Event
    _pause_event: anyio.Event

    def __init__(
        self,
        *,
        run_id: str,
        graph: Graph,
        parent_run_id: str | None = None,
        initial_state: BaseModel | None = None,
        node_registry: dict[str, NodeBase] | None = None,
        checkpointer: Checkpointer | None = None,
        capabilities: Any = None,
        fathom: Any = None,
        fact_store: Any = None,
        audit_sink: Any = None,
    ) -> None:
        self.run_id = run_id
        self.graph = graph
        self.parent_run_id = parent_run_id
        self.state = "pending"
        self.initial_state = initial_state
        self.node_registry = node_registry if node_registry is not None else {}
        self.checkpointer = checkpointer
        self.capabilities = capabilities
        self.fathom = fathom
        # Scaffold stub -- task T01 (consumed by T13/T02). Optional, default None.
        self.fact_store = fact_store
        self.audit_sink = audit_sink
        # Per-node cassette wiring (design §10.3). ``node_cassette`` is
        # ``None`` until a caller wires one; ``node_id`` is stamped by
        # :func:`stargraph.runtime.dispatch.dispatch_node` for the duration
        # of each node body so write-side-effect nodes can key cassette
        # entries by ``(node_id, step)``.
        self.node_cassette: Any = None
        self.node_id: str = ""
        # Per-run primitives: each :class:`GraphRun` owns a fresh bus + mirror
        # scheduler. Cloning sender handles for parallel branches lands in
        # Phase 3; v1 is single-consumer (the ``stream()`` async iterator).
        self.bus = EventBus()
        self.mirror_scheduler = MirrorScheduler()
        # Cooperative-cancel signal (FR-76, design §4.1). Set by
        # :meth:`cancel`; consumed by :func:`stargraph.graph.loop.execute` at
        # checkpoint boundaries (task 1.8). ``anyio.Event`` (not
        # :class:`asyncio.Event`) per Stargraph's anyio-throughout convention.
        self._cancel_event = anyio.Event()
        # Cooperative-pause signal (FR-76, NFR-18, design §4.1). Set by
        # :meth:`pause`; consumed by :func:`stargraph.graph.loop.execute` at
        # the next checkpoint boundary (task 1.8) -- the loop writes the
        # checkpoint, transitions ``state`` to ``"paused"``, and returns
        # cleanly. Resume is the standard :meth:`resume` cold-restart path.
        self._pause_event = anyio.Event()
        # Cooperative-respond signal (FR-87, NFR-22, design §9.5). Set by
        # :meth:`respond` after the state transitions back to ``"running"``;
        # consumed by :func:`stargraph.graph.loop.execute`'s ``_HitInterrupt``
        # arm when the active :class:`~stargraph.ir._models.InterruptAction`
        # carries a ``timeout``. The loop races
        # :func:`anyio.move_on_after` against ``self._respond_event.wait()``
        # so the timeout watchdog fires within ±100ms of expiry (NFR-22)
        # and the respond path stays cooperative. With ``timeout=None`` the
        # loop waits on this event indefinitely -- hot-resume on the same
        # live coroutine, no watchdog (#81).
        self._respond_event = anyio.Event()
        # Terminal failure diagnostics (#68). Populated by
        # :func:`stargraph.graph.loop.execute` when the run reaches a failed
        # terminal state -- ``error_class`` is a coarse discriminator
        # (``"interrupt_timeout"`` for a HITL timeout-halt, the exception
        # type name for a node error) and ``error_cause`` is a short message.
        # Both stay ``None`` on the success path; the loop's terminal
        # :class:`RunSummary` carries them through to ``runs_history`` so a
        # failed run records *why* it failed (distinguishing a timeout from
        # a node error).
        self.error_class: str | None = None
        self.error_cause: str | None = None

    def _rearm_respond_gate(self) -> None:
        """Reset the respond handshake before parking on a fresh interrupt (#81).

        ``anyio.Event`` has no ``clear()``: once :meth:`respond` calls
        ``.set()`` the event stays set for this run's lifetime. With
        hot-resume (#81) a single :class:`GraphRun` survives across every
        interrupt it hits, so without a reset a prior interrupt's set would
        let the *next* interrupt's ``_respond_event.wait()`` return instantly
        -- silently skipping the second human pause. The loop calls this
        immediately *before* flipping ``state`` to ``"awaiting-input"`` for
        each interrupt. Re-arming before the state flip is race-free:
        :meth:`respond` is gated on ``state == "awaiting-input"`` (it no-ops
        otherwise), so a concurrent respond can only ever set the freshly
        armed event, never a stale one that the loop is about to discard.
        """
        self._respond_event = anyio.Event()

    async def start(self) -> _RunSummary:
        """Drive this run through :func:`stargraph.graph.loop.execute` to completion.

        Returns the :class:`stargraph.checkpoint.protocol.RunSummary` for the
        completed run. The loop transitions :attr:`state` ``pending`` →
        ``running`` → ``done`` (or ``failed`` on unhandled exception) and
        publishes a :class:`~stargraph.runtime.events.TransitionEvent` per node
        tick. POC v1 is single-use: a second call after termination raises
        :class:`RuntimeError` so callers cannot accidentally re-drive a
        completed run handle.
        """
        if self.state != "pending":
            raise StargraphRuntimeError(
                f"GraphRun.start() may only be called on a pending run "
                f"(current state: {self.state!r})"
            )
        # Local import sidesteps the loop module's import of GraphRun.
        from stargraph.graph.loop import execute

        return await execute(self)

    async def cancel(self, *, actor: str) -> None:
        """Cooperatively cancel a live run (FR-76, AC-13.6, NFR-17, design §4.1).

        Valid only from ``"running"``, ``"paused"``, or ``"awaiting-input"`` --
        any other state raises :class:`StargraphRuntimeError` (the run is already
        terminal or has not yet started). The cooperative contract:

        1. Set :attr:`_cancel_event` so the loop sees the cancel at its next
           checkpoint boundary (task 1.8 wires the consumer in
           :func:`stargraph.graph.loop.execute`). The loop is responsible for
           raising :class:`asyncio.CancelledError` and unwinding tools/nodes;
           ``cancel()`` itself does not interrupt in-flight work.
        2. Emit a :class:`~stargraph.runtime.events.RunCancelledEvent` with
           ``reason="user"`` so the audit sink + WS stream observe the
           cooperative-cancel signal immediately (NFR-17 ≤5s p95 budget --
           the event lands now even if the loop boundary is seconds away).
        3. Transition :attr:`state` to ``"cancelled"`` -- terminal label per
           design §4.1's 7-state lifecycle.
        4. Persist a final checkpoint marking the cancelled state if the
           checkpointer is wired and a snapshot can be captured. The full
           checkpointing happens at the loop's next boundary; this in-method
           write is belt-and-suspenders so an external observer (cron sweep,
           CLI inspect) can see the terminal row even if the loop is wedged.
           Skipped silently when :meth:`checkpoint` is not yet implemented
           (lands in tasks 1.20-1.21) so cancel remains usable in Phase 1.
        """
        from datetime import UTC, datetime

        from stargraph.runtime.events import RunCancelledEvent

        if self.state not in ("running", "paused", "awaiting-input"):
            raise StargraphRuntimeError(
                f"cannot cancel from state {self.state!r}; "
                "valid states are 'running', 'paused', 'awaiting-input'"
            )
        # Step 1: signal the cooperative boundary. Idempotent -- ``anyio.Event``
        # ignores repeated ``set()`` calls.
        self._cancel_event.set()
        # Step 2: emit the typed event. ``step=0`` mirrors the convention used
        # by :func:`stargraph.graph.loop._emit_result` for run-scoped (non-tick)
        # events; the loop owns true step counters.
        await self.bus.send(
            RunCancelledEvent(
                run_id=self.run_id,
                step=0,
                ts=datetime.now(UTC),
                actor=actor,
                reason="user",
            ),
            fathom=self.fathom,
        )
        # Step 3: terminal-state transition.
        self.state = "cancelled"
        # Step 4: best-effort persist. ``self.checkpoint()`` raises
        # :class:`NotImplementedError` until tasks 1.20-1.21 land; callers in
        # Phase 1 still get the event + state transition + cancel signal.
        if self.checkpointer is not None:
            try:
                snapshot = self.checkpoint()
            except NotImplementedError:
                return
            await self.checkpointer.write(snapshot)

    async def pause(self, *, actor: str) -> None:
        """Cooperatively pause a running run (FR-76, NFR-18, design §4.1).

        Valid only from ``"running"`` -- any other state raises
        :class:`StargraphRuntimeError` (the run is terminal, pending, paused
        already, or in a HITL wait). The cooperative contract:

        1. Set :attr:`_pause_event` so the loop sees the pause at its next
           checkpoint boundary (task 1.8 wires the consumer in
           :func:`stargraph.graph.loop.execute`). The loop is responsible for
           writing the checkpoint, transitioning :attr:`state` to
           ``"paused"``, and returning cleanly; ``pause()`` itself does not
           interrupt in-flight work.
        2. Emit a :class:`~stargraph.runtime.events.RunPausedEvent` so the
           audit sink + WS stream observe the cooperative-pause signal
           immediately (NFR-18 budget -- the event lands now even if the
           loop boundary is seconds away).

        Unlike :meth:`cancel`, ``pause()`` deliberately does **not** mutate
        :attr:`state` or persist a checkpoint here -- that bookkeeping is
        the loop's responsibility on the next boundary (task 1.8). Resume
        is the standard cold-restart path via :meth:`resume`.
        """
        from datetime import UTC, datetime

        from stargraph.runtime.events import RunPausedEvent

        if self.state != "running":
            raise StargraphRuntimeError(
                f"cannot pause from state {self.state!r}; valid state is 'running'"
            )
        # Step 1: signal the cooperative boundary. Idempotent -- ``anyio.Event``
        # ignores repeated ``set()`` calls.
        self._pause_event.set()
        # Step 2: emit the typed event. ``step=0`` mirrors the convention used
        # by :meth:`cancel` and :func:`stargraph.graph.loop._emit_result` for
        # run-scoped (non-tick) events; the loop owns true step counters.
        await self.bus.send(
            RunPausedEvent(
                run_id=self.run_id,
                step=0,
                ts=datetime.now(UTC),
                actor=actor,
            ),
            fathom=self.fathom,
        )

    async def respond(self, response: dict[str, Any], actor: str) -> None:
        """Deliver a HITL response to a paused-on-interrupt run (FR-85, NFR-19, design §4.1).

        Valid only from ``"awaiting-input"`` -- any other state raises
        :class:`StargraphRuntimeError`. The cooperative contract per design §4.1
        (with reality-anchored API surface adjustments noted below):

        1. Assert a ``stargraph.evidence`` fact via the wired Fathom adapter
           with ``origin="user"``, ``source=actor``, and the response body
           in a single ``data`` slot (raw JSON dict per **locked Decision #2**
           in design §17 -- *not* serialized to a string, *not* wrapped in a
           further envelope; downstream rules pattern-match on JSON
           structure). The assertion uses the existing
           :meth:`~stargraph.fathom.FathomAdapter.assert_with_provenance`
           surface (which encodes provenance into the standard
           underscore-prefixed slots and forwards to ``engine.assert_fact``)
           rather than the design-pseudocode ``assert_fact`` + ad-hoc
           ``StargraphEvidenceFact.to_clips_slots()`` shape -- the latter has
           no implementation in-tree, while the former is the canonical
           Fathom-adapter call site (`runtime/bus.py:144`,
           `runtime/parallel.py:413`, `stores/kg_promotion.py:166`).

        2. Emit an audit event carrying ``actor`` + ``body_hash`` (NOT the
           raw response body -- per design §9.7 compliance/privacy
           posture). ``body_hash`` is ``sha256(rfc8785.dumps(response))``
           where :mod:`rfc8785` is the canonical-JSON encoder already used
           by :mod:`stargraph.replay.counterfactual` and :mod:`stargraph.graph.hash`
           (no ``stargraph.runtime.utils.canonical_json`` helper exists in-tree;
           the rfc8785 import is the in-house convention). The audit event
           is emitted as a :class:`~stargraph.runtime.events.BosunAuditEvent`
           with ``pack_id="stargraph.runtime"`` + ``pack_version="1.0"`` +
           ``fact={"kind": "respond", "actor": actor, "body_hash": body_hash}``;
           a dedicated ``RespondAuditEvent`` may be added later (flagged
           in `.progress.md`).

        3. Transition :attr:`state` to ``"running"``.

        4. The loop's resume hook (consumes the response and advances from
           ``current_step + 1``) is wired into
           :func:`stargraph.graph.loop.execute` by task 1.8 -- this method
           does not call ``execute`` directly because the loop's resume
           contract requires a checkpoint-driven cold-restart path that
           Phase 1 does not yet land. The state transition + asserted
           evidence fact + audit emission are sufficient for the HTTP
           handler at :file:`src/stargraph/serve/respond.py` (task 2.x) to
           return ``200 RunSummary(status="running")`` per design §9.4
           step 5; loop resumption observes the new state on its next
           tick.
        """
        import hashlib
        from datetime import UTC, datetime

        import rfc8785

        from stargraph.runtime.events import BosunAuditEvent

        if self.state != "awaiting-input":
            raise StargraphRuntimeError(
                f"cannot respond from state {self.state!r}; valid state is 'awaiting-input'"
            )

        # Step 1: assert the stargraph.evidence fact. Decision #2: raw JSON dict
        # in the ``data`` slot (no envelope, no string serialization).
        # Provenance carries origin="user" + source=<actor>. ``step=0`` mirrors
        # cancel/pause's convention for run-scoped (non-tick) emissions; the
        # loop owns true step counters and will re-stamp at resume time.
        now = datetime.now(UTC)
        if self.fathom is not None:
            provenance: dict[str, Any] = {
                "origin": "user",
                "source": actor,
                "run_id": self.run_id,
                "step": 0,
                "confidence": 1,
                "timestamp": now,
            }
            # ``data`` is a non-provenance slot: it bypasses the
            # _sanitize_provenance_slot path and is passed through to
            # ``engine.assert_fact`` as-is. Fathom's JSON helpers
            # pattern-match on the raw dict downstream (Decision #2).
            self.fathom.assert_with_provenance(
                "stargraph.evidence",
                {"data": response},
                provenance,
            )

        # Step 2: audit-emit body hash (never the raw body -- design §9.7).
        # rfc8785 is the canonical-JSON encoder used elsewhere in-tree
        # (replay/counterfactual.py, graph/hash.py); ``runtime/utils.py``
        # does not exist, and adding a one-shot wrapper would duplicate the
        # call site for no benefit.
        body_hash = hashlib.sha256(rfc8785.dumps(response)).hexdigest()
        # ProvenanceBundle (FR-55, AC-11.2): the engine-internal respond
        # audit event is system-emitted (the typed envelope mirrors the
        # stargraph.evidence fact above but is a separate audit-bus
        # emission); origin="system" with pack id as source matches the
        # serve-layer respond_orchestrated convention. The actor lives
        # in the fact body for audit consumers.
        audit_provenance: dict[str, Any] = {
            "origin": "system",
            "source": "stargraph.runtime",
            "run_id": self.run_id,
            "step": 0,
            "confidence": 1.0,
            "timestamp": now.isoformat(),
        }
        await self.bus.send(
            BosunAuditEvent(
                run_id=self.run_id,
                step=0,
                ts=now,
                pack_id="stargraph.runtime",
                pack_version="1.0",
                fact={"kind": "respond", "actor": actor, "body_hash": body_hash},
                provenance=audit_provenance,
            ),
            fathom=self.fathom,
        )

        # Step 3: transition back to running. Task 1.8 wires the loop's
        # resume hook to consume the asserted evidence and advance from the
        # current checkpoint's step + 1; until then, the state transition
        # alone is sufficient for the HTTP /respond handler to return
        # status="running" per design §9.4 step 5.
        self.state = "running"

        # Step 4: signal the respond-event so the parked loop iteration
        # inside the ``_HitInterrupt`` arm wakes up and advances past the
        # interrupt node. Both timeout shapes wait on this event now
        # (hot-resume, #81): ``timeout=None`` waits indefinitely; a finite
        # ``timeout`` races a watchdog against it. ``anyio.Event.set`` is
        # idempotent, so a spurious set (e.g. respond racing a watchdog
        # that already fired, or a loop that already exited) is harmless.
        self._respond_event.set()

    async def stream(self) -> AsyncIterator[Event]:
        """Yield engine events as the run progresses (design §3.1.2).

        Single async iterator per run (Open Q3 single-use invariant). Phase 1
        skeleton yields nothing -- the run-loop wiring (task 1.16+) replaces
        this body with the bounded back-pressure event bus drain. The
        ``if False: yield`` idiom keeps this a generator function so callers
        can ``async for ev in run.stream(): ...`` without runtime errors,
        while making the empty-emission contract obvious to readers.
        """
        if False:  # pragma: no cover -- placeholder body; real impl in task 1.16
            yield  # pyright: ignore[reportReturnType]

    async def wait(self) -> RunSummary:
        """Block until the run terminates; return the final :class:`RunSummary`.

        Phase 3 (task 3.26) lands a thin driver: when the run is ``"pending"``,
        :func:`stargraph.graph.loop.execute` is invoked to drive the run to a
        terminal state. The eventual rich ``wait()`` (multi-consumer event
        bus, cancellation, mid-run pause/resume) replaces this body in a
        later task; for now ``wait`` is sufficient to drive resumed runs to
        ``"done"`` per FR-19's resume-and-continue contract.
        """
        if self.state != "pending":
            raise StargraphRuntimeError(
                f"GraphRun.wait() may only be called on a pending run "
                f"(current state: {self.state!r})"
            )
        from stargraph.graph.loop import execute

        return await execute(self)

    def checkpoint(self) -> Checkpoint:
        """Capture an on-demand checkpoint snapshot of current run state (T01).

        Assembly mirrors :func:`stargraph.runtime.dispatch.dispatch_node` step 7
        (``dispatch.py:101-117``) but reads from ``self`` rather than a fresh
        ``state`` argument.  Returns a fully-populated
        :class:`stargraph.checkpoint.protocol.Checkpoint` Pydantic model (INV-2).
        """
        from stargraph.checkpoint.protocol import Checkpoint as _Ckpt

        state_dict: dict[str, Any] = (
            self.initial_state.model_dump(mode="json") if self.initial_state is not None else {}
        )
        return _Ckpt(
            run_id=self.run_id,
            step=0,
            branch_id=None,
            parent_step_idx=None,
            graph_hash=self.graph.graph_hash,
            runtime_hash=self.graph.runtime_hash,
            state=state_dict,
            clips_facts=[],
            last_node=self.node_id,
            next_action=None,
            timestamp=datetime.now(UTC),
            parent_run_id=self.parent_run_id,
            side_effects_hash="",
        )

    @classmethod
    async def resume(
        cls,
        checkpointer: Checkpointer,
        run_id: str,
        *,
        from_step: int | None = None,
        graph: Graph | None = None,
    ) -> GraphRun:
        """Resume a previously-checkpointed run by ``run_id`` (FR-19, FR-20, FR-27).

        Returns a *new* :class:`GraphRun` instance bound to the same
        ``run_id`` (continuation of the same logical run, design §3.1.1).

        Loads the latest checkpoint by default; ``from_step=N`` pins the
        load to step ``N`` (FR-19). Missing-step is "loud" per FR-6 --
        :class:`CheckpointError` with ``run_id``/``step`` context.

        Refuses cf-prefix derived-hash checkpoints (FR-27, AC-3.4): a
        checkpoint whose ``graph_hash`` starts with ``"stargraph-cf-v1"`` was
        produced by a counterfactual fork and is not eligible for resume
        against its parent run.

        When ``graph`` is supplied, ``graph_hash`` is compared against the
        checkpoint's persisted hash (FR-20); on mismatch the parent IR's
        ``migrate`` blocks are consulted -- a block with matching
        ``from_hash``/``to_hash`` rescues the resume, otherwise
        :class:`CheckpointError` is raised with ``expected_hash``,
        ``actual_hash``, and ``migrate_available: bool`` context.
        """
        ckpt = await _load_checkpoint(checkpointer, run_id, from_step)
        _refuse_cf_prefix(ckpt)
        _validate_graph_hash(ckpt, graph)

        target_graph = graph if graph is not None else _build_resume_stub_graph(ckpt)
        initial_state = _state_model_from_dict(ckpt.state, run_id=run_id)

        return cls(
            run_id=run_id,
            graph=target_graph,
            initial_state=initial_state,
            checkpointer=checkpointer,
            parent_run_id=ckpt.parent_run_id,
        )

    @classmethod
    async def counterfactual(
        cls,
        checkpointer: Checkpointer,
        run_id: str,
        *,
        step: int,
        mutate: Any,
    ) -> GraphRun:
        """Fork a counterfactual child run from ``run_id`` at ``step`` (FR-27).

        Creates a *new* ``run_id`` bound to the cf-derived ``graph_hash``
        (JCS domain-separation per design §3.8.3). The original event log
        and original checkpoints are byte-identical post-execution -- the
        Temporal "cannot change the past" invariant. Per design §3.8.4:

        1. Load the original checkpoint at ``step`` (loud-fail on miss).
        2. Compute :func:`derived_graph_hash` from
           ``original.graph_hash`` + the
           :class:`~stargraph.replay.counterfactual.CounterfactualMutation`.
        3. Mint a fresh ``run_id`` for the cf child via :func:`uuid.uuid4`
           so the child's checkpoint rows never shadow the parent's.
        4. Apply the mutation's ``state_overrides`` to the fork-step
           checkpoint state (in-memory only -- never written back to the
           original ``run_id``).
        5. Return a new :class:`GraphRun` bound to the *new* ``run_id``,
           pointing at the cf-derived ``graph_hash`` via ``parent_run_id``.

        Steps 6-8 of design §3.8.4 (replay 0..N-1 from cassettes,
        re-execute >=N, emit cf-prefixed events into the new history) are
        driven by :func:`stargraph.graph.loop.execute` once the caller calls
        ``await cf_run.wait()`` -- the same lazy-driver pattern used by
        :meth:`resume`.
        """
        # Local imports keep the module-load order honest:
        # - ``uuid`` is stdlib, no cycle risk.
        # - ``CounterfactualMutation`` / ``derived_graph_hash`` live in
        #   ``stargraph.replay.counterfactual`` which imports nothing from
        #   ``stargraph.graph`` -- but a top-level import here would tie the
        #   GraphRun import path to ``rfc8785`` for callers that never
        #   touch counterfactual (test isolation).
        import uuid

        from stargraph.replay.counterfactual import (
            CounterfactualMutation,
            derived_graph_hash,
        )

        if not isinstance(mutate, CounterfactualMutation):
            raise StargraphRuntimeError(
                "GraphRun.counterfactual(mutate=...) must be a CounterfactualMutation instance"
            )

        # Step 1: load the original checkpoint at the cf-fork step.
        ckpt = await _load_checkpoint(checkpointer, run_id, step)

        # Step 2: derived hash (domain-separated; FR-27 design §3.8.3).
        cf_graph_hash = derived_graph_hash(ckpt.graph_hash, mutate)

        # Step 3: mint a fresh run_id for the cf child. Never re-uses the
        # parent's id so cf checkpoints cannot shadow original rows.
        cf_run_id = f"cf-{uuid.uuid4()}"

        # Step 4: build the fork-step state with the mutation overlay.
        # This is in-memory -- the original checkpoint row is left
        # byte-identical at its original ``run_id``/``step`` coordinate.
        forked_state: dict[str, Any] = dict(ckpt.state)
        if mutate.state_overrides is not None:
            forked_state.update(mutate.state_overrides)

        # Step 5: return a fresh GraphRun bound to the new run_id. The
        # cf-derived ``graph_hash`` is carried by the (in-memory)
        # ``initial_state`` model; checkpoint persistence at the cf step
        # happens when the run loop drives ``cf_run.wait()`` and writes
        # checkpoints under ``cf_run_id`` -- never under ``run_id``.
        del cf_graph_hash  # carried forward by future loop wiring (task 3.34+)
        initial_state = _state_model_from_dict(forked_state, run_id=cf_run_id)
        target_graph = _build_resume_stub_graph(ckpt)

        return cls(
            run_id=cf_run_id,
            graph=target_graph,
            initial_state=initial_state,
            checkpointer=checkpointer,
            parent_run_id=run_id,
        )
