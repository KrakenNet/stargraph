# SPDX-License-Identifier: Apache-2.0
"""Pure helpers + control-flow signal types for the single-node execution loop.

Side-effect-free units extracted from :mod:`stargraph.graph.loop` so the
driver module stays focused on the §3.1.2 nine-step tick. Nothing here awaits,
touches the run bus, or drives a node -- these are a control-flow signal class,
a small value object, and three pure functions over the IR node/block lists.

Everything is re-exported from :mod:`stargraph.graph.loop` so the established
import surface is unchanged -- :mod:`stargraph.runtime.dispatch`,
:mod:`stargraph.nodes.interrupt.interrupt_node` (and the demos) import
:class:`_HitInterrupt`, and the test suite imports :func:`_merge_branch_results`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from stargraph.errors import StargraphRuntimeError
from stargraph.runtime.merge import build_last_write_conflict_evidence

if TYPE_CHECKING:
    from stargraph.ir._models import InterruptAction, NodeSpec, ParallelBlock


__all__ = [
    "_HitInterrupt",
    "_InterruptOutcome",
    "_lookup_node",
    "_merge_branch_results",
    "_parallel_block_for",
]


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
