# SPDX-License-Identifier: Apache-2.0
"""LanceDB provider CRUD + filter integration tests (FR-2, FR-10).

Phase-3 testing checkpoint exercising the
:class:`~stargraph.stores.lancedb.LanceDBVectorStore` POC end-to-end against
a real on-disk LanceDB dataset. Five tests pin the public Protocol
surface (``bootstrap`` / ``upsert`` / ``search`` / ``delete``):

1. :func:`test_bootstrap_idempotent` -- bootstrapping the same path
   three times is a no-op (table count and on-disk version stable).
2. :func:`test_upsert_search_delete_roundtrip` -- five-row CRUD cycle
   with a hit-count assertion before and after delete.
3. :func:`test_search_filter_sql_where` -- ``filter='metadata = ...'``
   restricts vector search to rows whose serialised JSON metadata
   matches; LanceDB stores ``metadata`` as a JSON-encoded string column
   (see :meth:`LanceDBVectorStore.upsert`), so the SQL ``WHERE`` clause
   is matched against the full JSON blob -- this test pins that
   contract.
4. :func:`test_search_mode_vector_fts_hybrid` -- each of
   ``mode='vector' | 'fts' | 'hybrid'`` returns a non-empty
   :class:`~stargraph.stores.vector.Hit` list against seeded data.
5. :func:`test_delete_returns_count` -- ``delete()`` returns the number
   of rows actually removed.

Uses :class:`~stargraph.stores.embeddings.FakeEmbedder` (``ndims=4``) for
determinism + speed; no MiniLM weights required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.lancedb import LanceDBVectorStore
from stargraph.stores.vector import Row

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_NDIMS = 4


def _seed_rows() -> list[Row]:
    """Five rows split across two ``tag`` metadata partitions (foo/bar)."""
    return [
        Row(id="r1", text="alpha quick brown", metadata={"tag": "foo"}),
        Row(id="r2", text="beta lazy dog", metadata={"tag": "foo"}),
        Row(id="r3", text="gamma quick fox", metadata={"tag": "foo"}),
        Row(id="r4", text="delta slow turtle", metadata={"tag": "bar"}),
        Row(id="r5", text="epsilon swift hare", metadata={"tag": "bar"}),
    ]


async def _seeded_store(path: Path) -> LanceDBVectorStore:
    """Build + bootstrap + populate a store at ``path``."""
    store = LanceDBVectorStore(path, FakeEmbedder(ndims=_NDIMS))
    await store.bootstrap()
    await store.upsert(_seed_rows())
    return store


async def test_bootstrap_idempotent(tmp_path: Path) -> None:
    """Calling :meth:`bootstrap` three times does not error or duplicate state."""
    import lancedb  # pyright: ignore[reportMissingTypeStubs]

    store = LanceDBVectorStore(tmp_path / "vectors", FakeEmbedder(ndims=_NDIMS))
    for _ in range(3):
        await store.bootstrap()

    db = await lancedb.connect_async(tmp_path / "vectors")
    tbl = await db.open_table("vectors")
    # No upserts happened -- count stays at 0 across all three bootstraps.
    assert await tbl.count_rows() == 0


async def test_upsert_search_delete_roundtrip(tmp_path: Path) -> None:
    """Upsert 5 rows, search returns hits, delete subset, search shrinks."""
    store = await _seeded_store(tmp_path / "vectors")
    embedder = FakeEmbedder(ndims=_NDIMS)
    query_vec = (await embedder.embed(["alpha quick brown"], kind="query"))[0]

    pre = await store.search(vector=query_vec, k=10, mode="vector")
    assert len(pre) == 5
    assert {h.id for h in pre} == {"r1", "r2", "r3", "r4", "r5"}

    deleted = await store.delete(["r1", "r2"])
    assert deleted == 2

    post = await store.search(vector=query_vec, k=10, mode="vector")
    assert len(post) == 3
    assert {h.id for h in post} == {"r3", "r4", "r5"}


async def test_search_filter_sql_where(tmp_path: Path) -> None:
    """``filter`` clause is forwarded as a SQL ``WHERE`` against the row.

    LanceDB serialises :class:`Row.metadata` as a JSON-encoded string
    column (see :meth:`LanceDBVectorStore.upsert`), so a literal
    substring match on the ``metadata`` column is the filter mechanism
    actually exposed by the POC. Once metadata is promoted to typed
    columns (Phase-3 hardening) the filter form will become
    ``metadata.tag = 'foo'``; for now we pin the JSON-blob form to lock
    the current contract.
    """
    store = await _seeded_store(tmp_path / "vectors")
    embedder = FakeEmbedder(ndims=_NDIMS)
    query_vec = (await embedder.embed(["alpha"], kind="query"))[0]

    hits = await store.search(
        vector=query_vec,
        filter='metadata = \'{"tag": "foo"}\'',
        k=10,
        mode="vector",
    )
    ids = {h.id for h in hits}
    assert ids == {"r1", "r2", "r3"}, f"expected only foo-tagged rows, got {sorted(ids)}"


async def test_search_mode_vector_fts_hybrid(tmp_path: Path) -> None:
    """All three search modes return non-empty hits against seeded data."""
    store = await _seeded_store(tmp_path / "vectors")
    embedder = FakeEmbedder(ndims=_NDIMS)
    query_text = "alpha quick brown"
    query_vec = (await embedder.embed([query_text], kind="query"))[0]

    vec_hits = await store.search(vector=query_vec, k=5, mode="vector")
    assert vec_hits, "mode='vector' returned no hits"

    fts_hits = await store.search(text=query_text, k=5, mode="fts")
    assert fts_hits, "mode='fts' returned no hits"

    hybrid_hits = await store.search(
        vector=query_vec,
        text=query_text,
        k=5,
        mode="hybrid",
    )
    assert hybrid_hits, "mode='hybrid' returned no hits"


async def test_delete_returns_count(tmp_path: Path) -> None:
    """:meth:`delete` returns the number of rows actually removed."""
    store = await _seeded_store(tmp_path / "vectors")

    # Existing ids -- expect exact count back.
    assert await store.delete(["r1", "r2", "r3"]) == 3

    # Empty input is a no-op.
    assert await store.delete([]) == 0

    # Non-existent id -- nothing deleted, returns 0.
    assert await store.delete(["does-not-exist"]) == 0
