# SPDX-License-Identifier: Apache-2.0
"""RyuGraph provider integration tests (FR-3, FR-11, AC-12.2/12.3).

Three smoke tests pinning the public Protocol surface of
:class:`~stargraph.stores.ryugraph.RyuGraphStore` against a real on-disk
RyuGraph database:

1. :func:`test_bootstrap_creates_entity_rel_tables` -- bootstrap
   installs both the ``Entity`` node table and the ``Rel`` edge table
   per design §3.2.
2. :func:`test_add_triple_then_query` -- ``add_triple`` round-trips
   through ``query`` (single triple, single hit).
3. :func:`test_ryugraph_async_connection` -- the underlying connection is
   the native :class:`ryugraph.AsyncConnection` (smoke check on impl).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import ryugraph

from stargraph.stores.graph import NodeRef
from stargraph.stores.ryugraph import RyuGraphStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


async def test_bootstrap_creates_entity_rel_tables(tmp_path: Path) -> None:
    """``bootstrap`` installs Entity (NODE) + Rel (REL) tables (design §3.2)."""
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()

    rs = await store.query("CALL show_tables() RETURN *")
    table_types = {(row["name"], row["type"]) for row in rs.rows}
    assert ("Entity", "NODE") in table_types
    assert ("Rel", "REL") in table_types


async def test_add_triple_then_query(tmp_path: Path) -> None:
    """``add_triple`` upsert round-trips through ``query`` (FR-3)."""
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()

    await store.add_triple(
        NodeRef(id="alice", kind="Person"),
        "knows",
        NodeRef(id="bob", kind="Person"),
    )

    rs = await store.query(
        "MATCH (s:Entity)-[r:Rel]->(o:Entity) "
        "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
    )
    assert len(rs.rows) == 1
    row = rs.rows[0]
    assert row["subject"] == "alice"
    assert row["predicate"] == "knows"
    assert row["object"] == "bob"


async def test_ryugraph_async_connection(tmp_path: Path) -> None:
    """Underlying connection is the native :class:`ryugraph.AsyncConnection` (FR-11)."""
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()

    conn = store._require_conn()  # pyright: ignore[reportPrivateUsage]
    assert isinstance(conn, ryugraph.AsyncConnection)
