# SPDX-License-Identifier: Apache-2.0
"""``isinstance`` checks for default Providers vs Store Protocols (AC-1.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from stargraph.stores import (
    DocStore,
    FactStore,
    GraphStore,
    LanceDBVectorStore,
    MemoryStore,
    RyuGraphStore,
    SQLiteDocStore,
    SQLiteFactStore,
    SQLiteMemoryStore,
    VectorStore,
)

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


class _StubEmbedding:
    """Minimal duck-typed ``Embedding`` for cheap LanceDB construction."""

    model_id = "stub/model"
    revision = "main"
    content_hash = "0" * 64
    ndims = 8

    async def embed(
        self,
        texts: list[str],
        *,
        kind: Literal["query", "document"],
    ) -> list[list[float]]:
        del kind
        return [[0.0] * self.ndims for _ in texts]


def test_lancedb_vector_store_isinstance(tmp_path: Path) -> None:
    """``LanceDBVectorStore`` satisfies the ``VectorStore`` Protocol (AC-1.2)."""
    store = LanceDBVectorStore(tmp_path / "vec", _StubEmbedding())
    assert isinstance(store, VectorStore)


def test_kuzu_graph_store_isinstance(tmp_path: Path) -> None:
    """``RyuGraphStore`` satisfies the ``GraphStore`` Protocol (AC-1.2)."""
    store = RyuGraphStore(tmp_path / "graph")
    assert isinstance(store, GraphStore)


def test_sqlite_doc_store_isinstance(tmp_path: Path) -> None:
    """``SQLiteDocStore`` satisfies the ``DocStore`` Protocol (AC-1.2)."""
    store = SQLiteDocStore(tmp_path / "doc.db")
    assert isinstance(store, DocStore)


def test_sqlite_memory_store_isinstance(tmp_path: Path) -> None:
    """``SQLiteMemoryStore`` satisfies the ``MemoryStore`` Protocol (AC-1.2)."""
    store = SQLiteMemoryStore(tmp_path / "mem.db")
    assert isinstance(store, MemoryStore)


def test_sqlite_fact_store_isinstance(tmp_path: Path) -> None:
    """``SQLiteFactStore`` satisfies the ``FactStore`` Protocol (AC-1.2)."""
    store = SQLiteFactStore(tmp_path / "fact.db")
    assert isinstance(store, FactStore)
