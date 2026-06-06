# SPDX-License-Identifier: Apache-2.0
"""Single-writer-per-path serialization tests (FR-9, NFR-1, NFR-4).

Pins the contract that two concurrent writers against the same on-disk
store path serialise through :func:`stargraph.stores._common._lock_for`
without corruption: every row from every concurrent task is durable
afterwards. Loud-fail mandatory per NFR-4 -- a future change that drops
the lock surfaces here, not as silent data loss in production.

The three primary tests dispatch two writers via :func:`asyncio.gather`
and assert post-condition row counts on the on-disk store:

1. :func:`test_lancedb_concurrent_upsert` -- two
   :meth:`LanceDBVectorStore.upsert` calls at the same path; all rows
   present afterwards.
2. :func:`test_sqlite_doc_concurrent_put` -- two
   :meth:`SQLiteDocStore.put` calls at the same path; both docs queryable.
3. :func:`test_kuzu_concurrent_add_triple` -- two
   :meth:`RyuGraphStore.add_triple` calls at the same path; both edges
   persisted.

A fourth test pins observability: while a writer is mid-flight,
:meth:`LanceDBVectorStore.health` reports ``lock_state='held'``, the
hint Stargraph exposes per design §3.1 for runtime introspection.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import lancedb  # pyright: ignore[reportMissingTypeStubs]
import pytest

from stargraph.stores._common import _lock_for  # pyright: ignore[reportPrivateUsage]
from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.graph import NodeRef
from stargraph.stores.lancedb import LanceDBVectorStore
from stargraph.stores.ryugraph import RyuGraphStore
from stargraph.stores.sqlite_doc import SQLiteDocStore
from stargraph.stores.vector import Row

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_NDIMS = 4


async def test_lancedb_concurrent_upsert(tmp_path: Path) -> None:
    """Two concurrent ``upsert`` tasks at the same LanceDB path retain all rows."""
    path = tmp_path / "vectors"
    store = LanceDBVectorStore(path, FakeEmbedder(ndims=_NDIMS))
    await store.bootstrap()

    rows_a = [Row(id=f"a-{i}", text=f"alpha-{i}") for i in range(5)]
    rows_b = [Row(id=f"b-{i}", text=f"beta-{i}") for i in range(5)]

    await asyncio.gather(store.upsert(rows_a), store.upsert(rows_b))

    db = await lancedb.connect_async(path)
    tbl = await db.open_table("vectors")
    assert await tbl.count_rows() == 10
    arrow = await tbl.query().to_arrow()
    persisted = {row["id"] for row in arrow.to_pylist()}
    expected = {r.id for r in rows_a} | {r.id for r in rows_b}
    assert persisted == expected


async def test_sqlite_doc_concurrent_put(tmp_path: Path) -> None:
    """Two concurrent :meth:`SQLiteDocStore.put` calls land both docs intact."""
    path = tmp_path / "docs.sqlite"
    store = SQLiteDocStore(path)
    await store.bootstrap()

    await asyncio.gather(
        store.put("doc-a", "alpha content"),
        store.put("doc-b", "beta content"),
    )

    docs = await store.query(limit=10)
    by_id = {d.id: d for d in docs}
    assert set(by_id) == {"doc-a", "doc-b"}
    assert by_id["doc-a"].content == "alpha content"
    assert by_id["doc-b"].content == "beta content"


async def test_kuzu_concurrent_add_triple(tmp_path: Path) -> None:
    """Two concurrent :meth:`RyuGraphStore.add_triple` calls persist both edges."""
    path = tmp_path / "graph"
    store = RyuGraphStore(path)
    await store.bootstrap()

    await asyncio.gather(
        store.add_triple(
            NodeRef(id="alice", kind="Person"),
            "knows",
            NodeRef(id="bob", kind="Person"),
        ),
        store.add_triple(
            NodeRef(id="alice", kind="Person"),
            "knows",
            NodeRef(id="carol", kind="Person"),
        ),
    )

    result = await store.query(
        "MATCH (s:Entity {id: 'alice'})-[r:Rel]->(o:Entity) RETURN o.id AS object",
    )
    objects = {row["object"] for row in result.rows}
    assert objects == {"bob", "carol"}


async def test_lock_state_observable_in_health(tmp_path: Path) -> None:
    """``health()`` reports ``lock_state='held'`` while a writer holds the lock.

    Acquires the per-path lock manually (the same primitive
    :meth:`upsert` uses) and asserts ``StoreHealth.lock_state == 'held'``;
    on release we drop back to ``'free'``. Pins the design §3.1 hint
    surface without racing a real upsert.
    """
    path = tmp_path / "vectors"
    store = LanceDBVectorStore(path, FakeEmbedder(ndims=_NDIMS))
    await store.bootstrap()

    lock = _lock_for(path)
    async with lock:
        held = await store.health()
        assert held.lock_state == "held"

    free = await store.health()
    assert free.lock_state == "free"
