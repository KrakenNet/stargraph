# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.retrieval -- :class:`RetrievalNode` POC (FR-26, AC-4, design §3.8).

Per-store fan-out + RRF fusion node. Given a list of :class:`StoreRef`
bindings and a per-binding ``store_resolver`` callable that maps each
``StoreRef.name`` to a concrete provider instance (vector / graph / doc),
:meth:`RetrievalNode.execute` opens an :class:`asyncio.TaskGroup`,
dispatches one branch per store, awaits all hit-lists, and returns the
fused top-``k`` :class:`~stargraph.stores.vector.Hit` list under
``state["retrieved"]``.

Phase-1 POC scope:

* Vector branch -- ``store.search(text=query, k=k)`` (provider's default
  ``mode="vector"`` falls back to ``"fts"`` when only ``text`` is supplied,
  matching :class:`~stargraph.stores.lancedb.LanceDBVectorStore` ergonomics).
* Doc branch -- ``store.query(filter=None, limit=k)`` mapped to
  :class:`Hit` rows (score = 0.0 -- DocStore has no native ranking;
  RRF still produces a stable order via list rank).
* Graph branch -- skipped in POC if the run state has no ``query``
  field; full Triple-Cypher dispatch lands in Phase-2 (FR-26 Phase-2).
* Reranker -- defaults to :class:`~stargraph.stores.rerankers.RRFReranker`
  when ``rerank=None``.
* Events -- emits ``stargraph.transition`` per branch via
  ``ctx.emit_event`` when the context exposes that hook (Phase-1
  :class:`~stargraph.nodes.base.ExecutionContext` is minimal; the call
  is best-effort and skipped silently when absent).

The "open a TaskGroup" wording in design §3.8 / FR-10 admits an
engine-managed :func:`stargraph.runtime.parallel.create_task_group`;
Phase-3 promotes this to that helper. For the POC we use
:class:`asyncio.TaskGroup` (Python 3.12+ stdlib) directly so the node
stays self-contained.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import NodeBase
from stargraph.stores.cypher import Linter
from stargraph.stores.doc import DocStore
from stargraph.stores.rerankers import RRFReranker
from stargraph.stores.vector import Hit, VectorStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from stargraph.ir._models import StoreRef
    from stargraph.nodes.base import ExecutionContext
    from stargraph.stores.graph import GraphStore
    from stargraph.stores.rerankers import Reranker

__all__ = ["RetrievalNode"]


class RetrievalNode(NodeBase):
    """Parallel fan-out retrieval node with RRF fusion (FR-26, AC-4).

    Each declared :class:`StoreRef` becomes one branch executed inside an
    :class:`asyncio.TaskGroup`; per-branch hit lists are fused via the
    configured :class:`~stargraph.stores.rerankers.Reranker` (default
    :class:`~stargraph.stores.rerankers.RRFReranker`) and exposed under
    ``state["retrieved"]`` for the next node.
    """

    def __init__(
        self,
        stores: list[StoreRef],
        *,
        rerank: Reranker | None = None,
        k: int = 5,
        store_resolver: Callable[[str], VectorStore | GraphStore | DocStore],
        cypher_by_store: dict[str, str] | None = None,
    ) -> None:
        """Compile-time wiring for parallel retrieval (FR-20/FR-26).

        ``cypher_by_store`` maps a graph-store name to the Cypher query
        that branch will execute. When supplied, the linter's
        :meth:`~stargraph.stores.cypher.Linter.requires_write` keyword scan
        runs **once at construction time** to decide whether the derived
        capability is ``db.<name>:read`` or ``db.<name>:write``. This
        derivation is replay-safe: the resulting :attr:`requires` list
        is fixed at compile time and never recomputed during
        :meth:`execute`, so the same input plan always yields the same
        capability set regardless of store-resolver state.
        """
        self._stores = stores
        self._rerank: Reranker = rerank if rerank is not None else RRFReranker()
        self._k = k
        self._resolver = store_resolver
        self._cypher_by_store = cypher_by_store or {}
        self._requires: list[str] = self._derive_requires()

    @property
    def requires(self) -> list[str]:
        """Capabilities derived from input :class:`StoreRef` list (FR-20).

        Each declared store contributes ``db.<name>:read`` by default;
        graph branches whose compile-time Cypher contains a write
        keyword (per :meth:`Linter.requires_write`) escalate to
        ``db.<name>:write`` instead.
        """
        return list(self._requires)

    def _derive_requires(self) -> list[str]:
        linter = Linter()
        caps: list[str] = []
        for ref in self._stores:
            cypher = self._cypher_by_store.get(ref.name)
            verb = "write" if cypher is not None and linter.requires_write(cypher) else "read"
            caps.append(f"db.{ref.name}:{verb}")
        return caps

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        query: str | None = getattr(state, "query", None)
        emit = getattr(ctx, "emit_event", None)

        per_store: list[list[Hit]] = []

        async with asyncio.TaskGroup() as tg:
            tasks: list[tuple[StoreRef, asyncio.Task[list[Hit]]]] = []
            for ref in self._stores:
                store = self._resolver(ref.name)
                tasks.append((ref, tg.create_task(self._dispatch(store, query))))

        for ref, task in tasks:
            hits = task.result()
            per_store.append(hits)
            if callable(emit):
                _safe_emit(
                    emit,
                    {
                        "kind": "stargraph.transition",
                        "store": ref.name,
                        "provider": ref.provider,
                        "n_hits": len(hits),
                        "ts": datetime.now().isoformat(),
                    },
                )

        fused = await self._rerank.fuse(per_store, k=self._k, query=query)
        return {"retrieved": fused}

    async def _dispatch(
        self,
        store: VectorStore | GraphStore | DocStore,
        query: str | None,
    ) -> list[Hit]:
        """Route a single :class:`StoreRef` to its provider-shaped query.

        Both :class:`VectorStore` and :class:`DocStore` are
        ``runtime_checkable`` Protocols, so :func:`isinstance` is the
        narrowing primitive. :class:`GraphStore` would shadow
        :class:`DocStore` on a ``query``-only check, so we order vector
        first, then doc, then fall through (graph branch is Phase-2).
        """
        if isinstance(store, VectorStore):
            if query is None:
                return []
            return await store.search(text=query, k=self._k)
        if isinstance(store, DocStore):
            docs = await store.query(filter=None, limit=self._k)
            return [Hit(id=d.id, score=0.0, metadata=_coerce_metadata(d.metadata)) for d in docs]
        # GraphStore (or unknown) -- POC skip; Phase-2 wires Triple-Cypher.
        return []


def _coerce_metadata(
    meta: dict[str, Any],
) -> dict[str, str | int | float | bool]:
    """Project arbitrary :class:`Document` metadata into the :class:`Hit` shape.

    :class:`~stargraph.stores.vector.Hit` restricts metadata to JSON scalars;
    :class:`~stargraph.stores.doc.Document` allows any orjson-serialisable
    payload. We keep only scalar entries -- nested structures are dropped
    for the POC fused output.
    """
    out: dict[str, str | int | float | bool] = {}
    for key, value in meta.items():
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
    return out


def _safe_emit(
    emit: Callable[..., Any],
    payload: dict[str, Any],
) -> None:
    """Best-effort call into ``ctx.emit_event``; swallow shape mismatches.

    Phase-1 :class:`~stargraph.nodes.base.ExecutionContext` does not declare
    ``emit_event``; concrete contexts may or may not accept the kwarg
    shape we pass. The POC contract is "emit when possible, never break
    retrieval if the sink rejects the payload" -- Phase-2 tightens the
    contract once :class:`ExecutionContext` grows the event sink field.
    """
    try:
        emit(payload)
    except (TypeError, ValueError):  # pragma: no cover -- Phase-2 tightens
        return
