# SPDX-License-Identifier: Apache-2.0
"""RetrievalNode parallel fan-out integration test (FR-26, AC-4, Task 3.30).

Asserts two surfaces on :class:`stargraph.nodes.retrieval.RetrievalNode`:

1. **Mixed store fan-out** -- a vector branch (LanceDB) and a doc branch
   (SQLiteDoc) seeded with overlapping ids, run through one
   :meth:`RetrievalNode.execute`, both contribute to the fused output
   under ``state["retrieved"]``.

2. **Actual parallel dispatch** -- each per-store dispatch path is
   wrapped in a slow sleep (50ms). With N=3 stores the serial floor is
   ~150ms; if :class:`asyncio.TaskGroup` is dispatching truly in
   parallel the wall-clock is closer to one sleep window. We pin the
   contract loosely (``< 1.5 * single``) so a CI scheduler hiccup does
   not flake the test.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel

from stargraph.ir._models import StoreRef
from stargraph.nodes.retrieval import RetrievalNode
from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.lancedb import LanceDBVectorStore
from stargraph.stores.sqlite_doc import SQLiteDocStore
from stargraph.stores.vector import Hit, Row

if TYPE_CHECKING:
    from pathlib import Path

    from stargraph.nodes.base import ExecutionContext
    from stargraph.stores.doc import DocStore
    from stargraph.stores.vector import VectorStore


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_NDIMS = 4
_SLEEP_S = 0.05


class _RetrievalState(BaseModel):
    query: str


class _StubCtx:
    run_id: str = "fanout-test"


class _SlowVectorStore:
    """Vector-store wrapper that sleeps before delegating to ``inner``.

    Satisfies :class:`~stargraph.stores.vector.VectorStore` structurally.
    Used to inject a measurable per-branch latency so the parallel
    dispatch contract is observable from wall-clock.
    """

    def __init__(self, inner: LanceDBVectorStore, delay_s: float) -> None:
        self._inner = inner
        self._delay_s = delay_s

    async def bootstrap(self) -> None:  # pragma: no cover -- used by setup
        await self._inner.bootstrap()

    async def health(self) -> Any:  # pragma: no cover -- not used in test
        return await self._inner.health()

    async def migrate(self, plan: Any) -> None:  # pragma: no cover -- not used
        await self._inner.migrate(plan)

    async def upsert(self, rows: list[Row]) -> None:  # pragma: no cover -- setup
        await self._inner.upsert(rows)

    async def search(
        self,
        *,
        vector: list[float] | None = None,
        text: str | None = None,
        filter: str | None = None,  # noqa: A002
        k: int = 10,
        mode: str = "vector",
    ) -> list[Hit]:
        await asyncio.sleep(self._delay_s)
        return await self._inner.search(
            vector=vector,
            text=text,
            filter=filter,
            k=k,
            mode=mode,  # type: ignore[arg-type]
        )

    async def delete(self, ids: list[str]) -> int:  # pragma: no cover -- not used
        return await self._inner.delete(ids)


class _SlowDocStore:
    """DocStore wrapper that sleeps before delegating to ``inner``."""

    def __init__(self, inner: SQLiteDocStore, delay_s: float) -> None:
        self._inner = inner
        self._delay_s = delay_s

    async def bootstrap(self) -> None:  # pragma: no cover -- setup
        await self._inner.bootstrap()

    async def health(self) -> Any:  # pragma: no cover -- not used
        return await self._inner.health()

    async def migrate(self, plan: Any) -> None:  # pragma: no cover -- not used
        await self._inner.migrate(plan)

    async def put(
        self,
        doc_id: str,
        content: str | bytes,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:  # pragma: no cover -- setup
        await self._inner.put(doc_id, content, metadata=metadata)

    async def get(self, doc_id: str) -> Any:  # pragma: no cover -- not used
        return await self._inner.get(doc_id)

    async def query(
        self,
        filter: str | None = None,  # noqa: A002
        *,
        limit: int = 100,
    ) -> list[Any]:
        await asyncio.sleep(self._delay_s)
        return await self._inner.query(filter, limit=limit)


async def _build_stores(
    tmp_path: Path,
    *,
    delay_s: float,
) -> tuple[_SlowVectorStore, _SlowDocStore]:
    """Bootstrap + seed a slow-vector and slow-doc store at ``tmp_path``."""
    embedder = FakeEmbedder(ndims=_NDIMS)
    vector_inner = LanceDBVectorStore(tmp_path / "vectors", embedder)
    await vector_inner.bootstrap()
    rows = [
        Row(id="r1", text="alpha quick brown", metadata={"tag": "v"}),
        Row(id="r2", text="beta lazy dog", metadata={"tag": "v"}),
        Row(id="r3", text="gamma quick fox", metadata={"tag": "v"}),
    ]
    await vector_inner.upsert(rows)

    doc_inner = SQLiteDocStore(tmp_path / "docs.sqlite")
    await doc_inner.bootstrap()
    for doc_id, text in [
        ("d1", "Document one about alpha"),
        ("d2", "Document two about beta"),
        ("d3", "Document three about gamma"),
    ]:
        await doc_inner.put(doc_id, text, metadata={"src": "doc"})

    return _SlowVectorStore(vector_inner, delay_s), _SlowDocStore(doc_inner, delay_s)


async def test_mixed_store_fanout_combines_branches(tmp_path: Path) -> None:
    """Two stores → both contribute hits to the fused result."""
    slow_vec, slow_doc = await _build_stores(tmp_path, delay_s=0.0)

    def _resolver(name: str) -> VectorStore | DocStore:
        if name == "vec":
            return cast("VectorStore", slow_vec)
        if name == "docs":
            return cast("DocStore", slow_doc)
        raise KeyError(name)

    node = RetrievalNode(
        stores=[
            StoreRef(name="vec", provider="lancedb"),
            StoreRef(name="docs", provider="sqlite-doc"),
        ],
        store_resolver=_resolver,
        k=10,
    )

    out = await node.execute(
        _RetrievalState(query="alpha"),
        cast("ExecutionContext", _StubCtx()),
    )
    fused = out["retrieved"]
    ids = {h.id for h in fused}
    # Both branches must contribute. Vector branch ids start with ``r``,
    # doc-branch ids with ``d``. With a small ``k`` + a synthetic query
    # the vector branch may return only its top hit (LanceDB's default
    # ``mode='vector'`` ranks by ANN similarity), but at least one
    # ``r*`` id must appear; the doc branch returns up to ``k`` rows
    # via :meth:`DocStore.query` so all three ``d*`` ids must show.
    vector_ids = {hid for hid in ids if hid.startswith("r")}
    doc_ids = {hid for hid in ids if hid.startswith("d")}
    assert vector_ids, f"vector branch missing in {sorted(ids)}"
    assert {"d1", "d2", "d3"}.issubset(doc_ids), f"doc branch missing in {sorted(ids)}"


async def test_dispatch_is_actually_parallel(tmp_path: Path) -> None:
    """3 branches x 50ms sleep should run in ~50ms wall-clock, not ~150ms.

    Bound: parallel run wall-clock < 1.5x single-branch baseline. A
    serial implementation would land ≈ 3x baseline; the bound therefore
    catches an accidental ``await`` chain without being flaky on a noisy
    scheduler.
    """
    slow_vec, slow_doc = await _build_stores(tmp_path, delay_s=_SLEEP_S)

    # Warm up LanceDB/SQLite caches with one search/query each so the
    # baseline measures the sleep, not first-call I/O setup.
    await slow_vec.search(text="alpha", k=3, mode="fts")
    await slow_doc.query(None, limit=3)

    # Baseline: one branch alone.
    single_start = time.monotonic()

    def _resolver_single(name: str) -> VectorStore | DocStore:
        if name == "vec":
            return cast("VectorStore", slow_vec)
        raise KeyError(name)

    node_single = RetrievalNode(
        stores=[StoreRef(name="vec", provider="lancedb")],
        store_resolver=_resolver_single,
        k=5,
    )
    await node_single.execute(
        _RetrievalState(query="alpha"),
        cast("ExecutionContext", _StubCtx()),
    )
    single_elapsed = time.monotonic() - single_start

    # Parallel: 3 branches (vec, docs, docs again under different name).
    def _resolver_three(name: str) -> VectorStore | DocStore:
        if name == "vec":
            return cast("VectorStore", slow_vec)
        if name in {"docs", "docs2"}:
            return cast("DocStore", slow_doc)
        raise KeyError(name)

    node_three = RetrievalNode(
        stores=[
            StoreRef(name="vec", provider="lancedb"),
            StoreRef(name="docs", provider="sqlite-doc"),
            StoreRef(name="docs2", provider="sqlite-doc"),
        ],
        store_resolver=_resolver_three,
        k=5,
    )
    parallel_start = time.monotonic()
    await node_three.execute(
        _RetrievalState(query="alpha"),
        cast("ExecutionContext", _StubCtx()),
    )
    parallel_elapsed = time.monotonic() - parallel_start

    # Parallel must not balloon to ≈ 3x single. Allow generous slack
    # (1.5x baseline) so scheduler jitter doesn't flake CI.
    assert parallel_elapsed < single_elapsed * 1.5 + 0.05, (
        f"dispatch appears serial: single={single_elapsed:.3f}s, "
        f"parallel(3)={parallel_elapsed:.3f}s"
    )
