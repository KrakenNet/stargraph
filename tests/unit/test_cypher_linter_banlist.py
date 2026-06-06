# SPDX-License-Identifier: Apache-2.0
"""Cypher linter ban-list per-construct tests (FR-12, AC-9.1, NFR-4).

One test per banned construct, each asserting that
:meth:`stargraph.stores.cypher.Linter.check` raises
:class:`stargraph.errors.UnportableCypherError`. Snippets contain the
banned token plus the minimum scaffolding required to make the
surrounding query syntactically plausible.
"""

from __future__ import annotations

import pytest

from stargraph.errors import UnportableCypherError
from stargraph.stores.cypher import Linter

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_apoc_call_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("CALL apoc.create.node(['L'], {}) YIELD node RETURN node")


def test_gds_call_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("CALL gds.pageRank.stream('g') YIELD nodeId RETURN nodeId")


def test_call_in_transactions_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("CALL { MATCH (n) DETACH DELETE n } IN TRANSACTIONS")


def test_mutating_call_subquery_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("CALL { MATCH (n:Foo) RETURN n } RETURN 1")


def test_collect_subquery_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("RETURN COLLECT { MATCH (n) RETURN n.id } AS ids")


def test_dynamic_label_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("MATCH (n:$($label)) RETURN n")


def test_load_csv_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("LOAD CSV FROM 'file:///x.csv' AS row RETURN row")


def test_show_indexes_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("SHOW INDEXES")


def test_map_projection_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("MATCH (n) RETURN n {.id, .name}")


def test_path_comprehension_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("MATCH (n) RETURN [(n)-[:R]->(m) | m.id] AS ids")


def test_yield_star_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("CALL db.labels() YIELD *")


def test_shortest_path_rejected() -> None:
    with pytest.raises(UnportableCypherError):
        Linter().check("MATCH p = shortestPath((a)-[*]-(b)) RETURN p")
