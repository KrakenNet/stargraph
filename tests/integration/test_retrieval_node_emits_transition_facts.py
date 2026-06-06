# SPDX-License-Identifier: Apache-2.0
"""RetrievalNode per-branch ``stargraph.transition`` emission test (FR-26, Task 3.30).

The Phase-1 :class:`~stargraph.nodes.retrieval.RetrievalNode` calls
``ctx.emit_event(payload)`` once per branch when the supplied
:class:`~stargraph.nodes.base.ExecutionContext` exposes that hook (see
the docstring on :class:`stargraph.nodes.retrieval.RetrievalNode`). The
Phase-1 :class:`ExecutionContext` Protocol does NOT yet declare
``emit_event``; concrete contexts may attach it ad-hoc. This test
pins the contract that **when** ``emit_event`` is wired, exactly one
``stargraph.transition`` payload per branch is dispatched.

If the production :class:`ExecutionContext` is later tightened to a
typed event sink (Phase-2), this test should continue to pass --
``RetrievalNode`` already routes through ``getattr(ctx, "emit_event",
None)`` and a typed sink that exposes the same name will satisfy the
contract.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel

from stargraph.ir._models import StoreRef
from stargraph.nodes.retrieval import RetrievalNode
from stargraph.stores.vector import Hit

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext
    from stargraph.stores.doc import DocStore
    from stargraph.stores.vector import VectorStore


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


class _RetrievalState(BaseModel):
    query: str


class _RecordingCtx:
    """:class:`ExecutionContext` stand-in with an ``emit_event`` recorder."""

    run_id: str = "transition-emit-test"

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit_event(self, payload: dict[str, Any]) -> None:
        self.events.append(payload)


class _StubVectorStore:
    """Returns a hard-coded hit list for ``search``."""

    def __init__(self, hits: list[Hit]) -> None:
        self._hits = hits

    async def bootstrap(self) -> None:  # pragma: no cover -- not exercised
        return None

    async def health(self) -> Any:  # pragma: no cover -- not exercised
        return None

    async def migrate(self, plan: Any) -> None:  # pragma: no cover -- not exercised
        return None

    async def upsert(self, rows: list[Any]) -> None:  # pragma: no cover -- not exercised
        return None

    async def search(
        self,
        *,
        vector: list[float] | None = None,
        text: str | None = None,
        filter: str | None = None,  # noqa: A002
        k: int = 10,
        mode: str = "vector",
    ) -> list[Hit]:
        del vector, text, filter
        return list(self._hits)

    async def delete(self, ids: list[str]) -> int:  # pragma: no cover -- not exercised
        del ids
        return 0


async def test_emits_one_transition_fact_per_branch() -> None:
    """One :code:`stargraph.transition` payload per declared store."""
    store_a = _StubVectorStore([Hit(id="a1", score=0.0, metadata={})])
    store_b = _StubVectorStore(
        [Hit(id="b1", score=0.0, metadata={}), Hit(id="b2", score=0.0, metadata={})],
    )

    def _resolver(name: str) -> VectorStore | DocStore:
        if name == "alpha":
            return cast("VectorStore", store_a)
        if name == "beta":
            return cast("VectorStore", store_b)
        raise KeyError(name)

    node = RetrievalNode(
        stores=[
            StoreRef(name="alpha", provider="lancedb"),
            StoreRef(name="beta", provider="lancedb"),
        ],
        store_resolver=_resolver,
        k=5,
    )

    ctx = _RecordingCtx()
    await node.execute(_RetrievalState(query="x"), cast("ExecutionContext", ctx))

    # One payload per declared StoreRef.
    assert len(ctx.events) == 2

    by_store = {ev["store"]: ev for ev in ctx.events}
    assert set(by_store.keys()) == {"alpha", "beta"}

    alpha = by_store["alpha"]
    assert alpha["kind"] == "stargraph.transition"
    assert alpha["provider"] == "lancedb"
    assert alpha["n_hits"] == 1
    assert isinstance(alpha["ts"], str) and alpha["ts"]

    beta = by_store["beta"]
    assert beta["kind"] == "stargraph.transition"
    assert beta["provider"] == "lancedb"
    assert beta["n_hits"] == 2


async def test_emit_event_absent_does_not_raise() -> None:
    """No ``emit_event`` on ctx → execute completes silently (best-effort).

    The Phase-1 :class:`ExecutionContext` Protocol declares only
    ``run_id``. RetrievalNode must not require the event sink to be
    present; its absence is the documented Phase-1 default.
    """

    class _MinimalCtx:
        run_id: str = "no-emit-ctx"

    store = _StubVectorStore([Hit(id="x", score=0.0, metadata={})])

    def _resolver(name: str) -> VectorStore | DocStore:
        del name
        return cast("VectorStore", store)

    node = RetrievalNode(
        stores=[StoreRef(name="vec", provider="lancedb")],
        store_resolver=_resolver,
        k=3,
    )

    out = await node.execute(
        _RetrievalState(query="x"),
        cast("ExecutionContext", _MinimalCtx()),
    )
    assert [h.id for h in out["retrieved"]] == ["x"]
