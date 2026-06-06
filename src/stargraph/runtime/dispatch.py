# SPDX-License-Identifier: Apache-2.0
"""Per-tick node dispatch -- one iteration of the §3.1.2 nine-step loop.

:func:`dispatch_node` is the body of :func:`stargraph.graph.loop.execute`'s
``while`` loop, lifted into its own function so the loop driver in
``loop.py`` reads as a thin orchestrator (Phase 2 refactor, simplicity).

The function executes steps 1-9 of design §3.1.2 for a single node and
returns the routing outcome -- either the next ``current_id`` to dispatch,
or ``None`` to halt the run. Behavior is unchanged from the inlined
Phase 1 implementation; this is a pure extraction.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from stargraph.checkpoint.protocol import Checkpoint
from stargraph.ir._models import GotoAction, HaltAction, InterruptAction, ParallelAction
from stargraph.runtime.action import ContinueAction, translate_actions
from stargraph.runtime.events import TransitionEvent
from stargraph.runtime.parallel import execute_parallel

if TYPE_CHECKING:
    from stargraph.graph.run import GraphRun
    from stargraph.ir._models import NodeSpec

__all__ = ["dispatch_node"]


async def dispatch_node(
    run: GraphRun,
    nodes: list[NodeSpec],
    current_node: NodeSpec,
    state: Any,
    step: int,
) -> tuple[Any, str | None]:
    """Run one §3.1.2 tick for ``current_node``; return ``(new_state, next_id)``.

    ``next_id`` is ``None`` when the tick halts the run (Fathom ``halt``
    decision or end-of-graph on a ``continue`` decision). Caller is
    responsible for the outer ``while`` and the lifecycle transitions on
    :class:`~stargraph.graph.run.GraphRun`.

    The Phase 1 POC stubs are preserved verbatim -- Fathom is gated on
    ``run.fathom``, capabilities are not yet enforced here, and the
    ``"parallel"`` decision raises :class:`NotImplementedError` (Phase 3).
    """
    current_id = current_node.id

    # 1. Run node body. Stamp the current node id on the run so
    # write-side-effect nodes can key the per-node cassette by
    # ``(node_id, step)`` (design §10.3). Cleared in ``finally`` so the
    # id never leaks across ticks.
    node_impl = run.node_registry.get(current_id)
    if node_impl is None:
        raise KeyError(f"no node implementation registered for id={current_id!r}")
    run.node_id = current_id
    try:
        outputs = await node_impl.execute(state, run)
    finally:
        run.node_id = ""

    # 2. Apply outputs to state (last-write-wins; FR-11 typed merge later).
    state = state.model_copy(update=outputs)

    # 3. Mirror annotated state -> AssertSpecs (Fathom-gated).
    actions: list[Any] = []
    if run.fathom is not None:
        mirror_specs = run.fathom.mirror_state(state, annotations={"node_id": current_id})
        run.mirror_scheduler.schedule(mirror_specs, lifecycle="step")

        # 4. Fathom assert + evaluate (sync; off-thread).
        await asyncio.to_thread(_assert_specs, run.fathom, mirror_specs, run.run_id, step)
        actions = await asyncio.to_thread(run.fathom.evaluate)

        # 4b. Retract consumed stargraph_action facts so they don't leak
        # into subsequent ticks (stale routing prevention).
        if actions:
            await asyncio.to_thread(_retract_stargraph_actions, run.fathom)

    # 5. Translate Fathom actions -> single RoutingDecision.
    decision = translate_actions(actions)

    # 6. Emit transition event (back-pressure-safe via the bus).
    target_for_event = (
        decision.target
        if isinstance(decision, GotoAction)
        else (_next_node_id(nodes, current_id) or "")
    )
    event = TransitionEvent(
        run_id=run.run_id,
        step=step,
        ts=datetime.now(UTC),
        from_node=current_id,
        to_node=target_for_event,
        rule_id="",
        reason=decision.kind,
    )
    await run.bus.send(event, fathom=run.fathom)

    # 7. Shielded checkpoint commit (FR-10).
    assert run.checkpointer is not None  # checked by caller
    checkpoint = Checkpoint(
        run_id=run.run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=run.graph.graph_hash,
        runtime_hash=run.graph.runtime_hash,
        state=state.model_dump(mode="json"),
        clips_facts=[],
        last_node=current_id,
        next_action=(
            None if isinstance(decision, ContinueAction) else decision.model_dump(mode="json")
        ),
        timestamp=datetime.now(UTC),
        parent_run_id=run.parent_run_id,
        side_effects_hash="",
    )
    await asyncio.shield(run.checkpointer.write(checkpoint))

    # 8. Mirror lifecycle: retract step-scoped mirrors at the boundary, then
    #    flush pinned specs to the FactStore (T13).
    run.mirror_scheduler.retract_step()
    await run.mirror_scheduler.persist_pinned(run.fact_store, run_id=run.run_id, step=step)

    # 9. Routing.
    if isinstance(decision, InterruptAction):
        # HITL interrupt boundary (design §4.4, §17 Decision #1, FR-81).
        # Raise the cooperative signal that ``stargraph.graph.loop.execute``
        # catches to transition the run to ``awaiting-input``. Imported
        # lazily to avoid a hard ``runtime → graph`` cycle.
        from stargraph.graph.loop import _HitInterrupt  # pyright: ignore[reportPrivateUsage]

        raise _HitInterrupt(decision)
    if isinstance(decision, HaltAction):
        return state, None
    if isinstance(decision, ParallelAction):
        state, next_id = await _dispatch_parallel(run, nodes, decision, state, step)
        return state, next_id
    if isinstance(decision, GotoAction):
        return state, decision.target
    # ContinueAction -- walk the static IR edge.
    return state, _next_node_id(nodes, current_id)


async def _dispatch_parallel(
    run: GraphRun,
    nodes: list[NodeSpec],
    action: ParallelAction,
    state: Any,
    step: int,
) -> tuple[Any, str | None]:
    """Run a rule-emitted ParallelAction fan-out (design §3.6.1).

    Each target dispatches once via :func:`dispatch_node` against the
    same parent state. The merged result is the last branch's state
    (last-write-wins placeholder; FR-11 typed merge lands later). The
    returned ``next_id`` is the action's ``join`` target (or ``None``
    when join is empty -- the static-edge walk resumes).

    Mirrors :func:`stargraph.graph.loop._run_parallel_block` for the
    top-level ``parallel:`` IR block; the rule-emitted variant is the
    routing-decision form of the same fan-out.
    """

    def _branch_factory(target_id: str, branch_step: int) -> Any:
        async def _run() -> Any:
            target_node = _lookup_node(nodes, target_id)
            new_state, _ = await dispatch_node(run, nodes, target_node, state, branch_step)
            return new_state

        return _run

    factories = [
        _branch_factory(target, step + idx + 1) for idx, target in enumerate(action.targets)
    ]
    results = await execute_parallel(
        factories,
        strategy=action.strategy or "all",
        bus=run.bus,
        fathom=run.fathom,
        run_id=run.run_id,
        step=step,
    )
    merged = results[-1] if results else state
    next_id: str | None = action.join or None
    return merged, next_id


def _lookup_node(nodes: list[NodeSpec], node_id: str) -> NodeSpec:
    """Return the :class:`NodeSpec` with ``id == node_id`` or raise :class:`KeyError`."""
    for node in nodes:
        if node.id == node_id:
            return node
    raise KeyError(f"no node with id={node_id!r} in graph")


def _next_node_id(nodes: list[NodeSpec], current_id: str) -> str | None:
    """Return the id of the node after ``current_id`` in ``nodes``, or ``None``."""
    for idx, node in enumerate(nodes):
        if node.id == current_id and idx + 1 < len(nodes):
            return nodes[idx + 1].id
    return None


def _retract_stargraph_actions(fathom: Any) -> None:
    """Retract all ``stargraph_action`` facts from CLIPS working memory.

    Called after actions are consumed so stale routing facts
    don't leak into subsequent ticks. Uses the Fathom typed retract path
    (safe collect-then-retract) under the adapter's CLIPS lock if one
    is exposed — required because parallel dispatch branches share the
    engine and CLIPS is not thread-safe.
    """
    lock = getattr(fathom, "clips_lock", None)
    try:
        if lock is not None:
            with lock:
                fathom.engine.retract("stargraph_action")
        else:
            fathom.engine.retract("stargraph_action")
    except Exception:
        pass


def _assert_specs(fathom: Any, specs: list[Any], run_id: str, step: int) -> None:
    """Assert each AssertSpec via the Fathom adapter with a minimal provenance bundle."""
    from datetime import UTC, datetime
    from decimal import Decimal

    for spec in specs:
        fathom.assert_with_provenance(
            spec.template,
            spec.slots,
            {
                "origin": "state",
                "source": "mirror",
                "run_id": run_id,
                "step": step,
                "confidence": Decimal("1.0"),
                "timestamp": datetime.now(UTC),
            },
        )
