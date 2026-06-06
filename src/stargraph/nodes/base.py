# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.base -- :class:`NodeBase` abstract interface (FR-1, design §5).

Every executable graph node subclasses :class:`NodeBase` and implements
:meth:`NodeBase.execute`, returning the dict of state-field outputs the
single-node execution loop (task 1.27, :mod:`stargraph.graph.loop`) merges
back into the run's :class:`pydantic.BaseModel` state via the field-merge
registry (FR-11).

The minimal :class:`ExecutionContext` :class:`typing.Protocol` declared
here is a Phase-1 placeholder; the full execution context (mirror handle,
checkpointer reference, capabilities gate, event sink, replay flag) is
threaded through ``GraphRun`` at task 1.27. Nodes only depend on the
:class:`Protocol`, so the structural-typing contract remains stable as
the concrete :class:`stargraph.graph.GraphRun` grows fields.

:class:`EchoNode` is a no-op fixture node used by the
``tests/fixtures/sample-graph.yaml`` scenario -- it copies
``state.message`` straight to its output dict, exercising dispatch +
merge without depending on any tool, adapter, or external service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel


@runtime_checkable
class ExecutionContext(Protocol):
    """Minimal per-execution context surfaced to nodes (Phase-1 placeholder).

    Task 1.27 (:mod:`stargraph.graph.loop`) supplies the concrete object;
    additional fields (capabilities gate, event sink, replay flag,
    mirror handle, checkpointer) attach to the same structural type as
    later phases land them. Nodes that don't read any context field
    can ignore the parameter entirely.
    """

    run_id: str


class NodeBase(ABC):
    """Abstract base class for every executable graph node (FR-1).

    Concrete subclasses implement :meth:`execute`, which receives the
    immutable run state (a :class:`pydantic.BaseModel` subclass declared
    by the graph) plus an :class:`ExecutionContext`, and returns a dict
    keyed by state-field name. The execution loop (task 1.27) merges
    each returned dict into the next state via the field-merge registry
    (FR-11) -- nodes never mutate state in place.

    Subclasses are free to declare additional fields (e.g. ``model_id``
    on :class:`stargraph.nodes.ml.MLNode`); :class:`NodeBase` itself
    carries no instance state, so subclasses can be plain classes,
    dataclasses, or :class:`pydantic.BaseModel` subclasses depending on
    their validation needs.
    """

    @abstractmethod
    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        """Compute this node's contribution to the next state.

        :param state: The current run state (immutable in this call).
        :param ctx: The per-run execution context (Phase-1 minimal
            shape -- see :class:`ExecutionContext`).
        :returns: A dict keyed by state-field name; each entry is
            merged into the next state via the field-merge registry
            (FR-11).
        """
        ...


class EchoNode(NodeBase):
    """No-op fixture node -- copies ``state.message`` to its output.

    Used by ``tests/fixtures/sample-graph.yaml`` to exercise the
    dispatch + merge path without pulling in any tool, adapter, or
    external service. Reads ``state.message`` (string) from the run
    state and returns ``{"message": <same value>}``; the merge step
    re-applies it as a last-write to the same field. Non-string or
    missing ``message`` raises :class:`AttributeError` at runtime,
    surfacing fixture mis-configuration loudly.
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        del ctx  # unused in fixture; protocol kept for interface symmetry
        # ``getattr`` (rather than ``state.message``) keeps pyright happy:
        # ``state`` is typed as the abstract ``BaseModel`` base, which has
        # no ``message`` field in its declared schema. Fixture graphs
        # supply a concrete state model with ``message: str``; missing
        # field surfaces as :class:`AttributeError` loudly.
        message: object = getattr(state, "message")  # noqa: B009
        return {"message": message}
