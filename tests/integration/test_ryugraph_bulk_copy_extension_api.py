# SPDX-License-Identifier: Apache-2.0
"""RyuGraph ``bulk_copy`` provider-extension API (FR-11, AC-12.4).

``RyuGraphStore.bulk_copy(entities_csv=..., edges_csv=...)`` is a
provider extension that surfaces RyuGraph's native ``COPY FROM``
bulk-load path. It is intentionally NOT part of the
:class:`GraphStore` Protocol: bulk-CSV ingest has no portable analogue
across every property-graph provider, and exposing it on the Protocol
would force any future provider swap-in to ship an incompatible shim.

These two tests pin the contract:

1. :func:`test_bulk_copy_loads_csvs` -- writing a header-CSV per table
   and calling :meth:`RyuGraphStore.bulk_copy` lands the rows in
   ``Entity`` + ``Rel``, reachable through the portable :meth:`query`.
2. :func:`test_bulk_copy_not_in_graphstore_protocol` -- ``hasattr(
   GraphStore, 'bulk_copy')`` is ``False``; only the provider class
   carries the method.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.stores.graph import GraphStore
from stargraph.stores.ryugraph import RyuGraphStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


async def test_bulk_copy_loads_csvs(tmp_path: Path) -> None:
    """``bulk_copy`` round-trips CSV rows into Entity + Rel (FR-11)."""
    entities_csv = tmp_path / "entities.csv"
    edges_csv = tmp_path / "edges.csv"
    entities_csv.write_text("id,kind\nalice,Person\nbob,Person\ncarol,Person\n")
    # RyuGraph COPY for REL tables expects (from, to, <all-props>) columns
    # in the table's declared order; Rel carries reserved bitemporal
    # ``t_valid`` / ``t_invalid`` columns alongside ``predicate``.
    edges_csv.write_text(
        "from,to,predicate,t_valid,t_invalid\nalice,bob,knows,,\nbob,carol,knows,,\n"
    )

    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()
    await store.bulk_copy(entities_csv=entities_csv, edges_csv=edges_csv)

    rs_nodes = await store.query("MATCH (e:Entity) RETURN count(e) AS n")
    assert rs_nodes.rows[0]["n"] == 3

    rs_edges = await store.query(
        "MATCH (s:Entity)-[r:Rel]->(o:Entity) "
        "RETURN s.id AS subject, r.predicate AS predicate, o.id AS object"
    )
    triples = {(row["subject"], row["predicate"], row["object"]) for row in rs_edges.rows}
    assert ("alice", "knows", "bob") in triples
    assert ("bob", "knows", "carol") in triples


def test_bulk_copy_not_in_graphstore_protocol() -> None:
    """``bulk_copy`` is a provider extension, NOT on the Protocol (AC-12.4)."""
    assert not hasattr(GraphStore, "bulk_copy"), (
        "bulk_copy must stay off GraphStore so future provider swap-in does "
        "not need a RyuGraph-specific shim (FR-11, AC-12.4)."
    )
    assert hasattr(RyuGraphStore, "bulk_copy"), (
        "RyuGraphStore must expose bulk_copy as a provider extension."
    )
