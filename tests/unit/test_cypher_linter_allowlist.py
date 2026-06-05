# SPDX-License-Identifier: Apache-2.0
"""Cypher portable-subset linter allow-list tests (FR-12, AC-9.1).

Pins the ~18 allow-list constructs documented in design §3.2 against
:meth:`harbor.stores.cypher.Linter.check`. Each test asserts that a
minimal-but-valid Cypher snippet exercising the construct passes the
linter without raising :class:`UnportableCypherError`.

These are read-side complements to the ban-list tests in
``test_cypher_linter_banlist.py`` (Task 3.12). Together they fence the
portable subset that Kuzu and Neo4j-5 must both support (research §F).
"""

from __future__ import annotations

import pytest

from harbor.errors import UnportableCypherError
from harbor.stores.cypher import Linter


@pytest.mark.knowledge
@pytest.mark.unit
def test_match_allowed() -> None:
    """`MATCH` is the read entry-point of the portable subset."""
    Linter().check("MATCH (n:Doc) RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_merge_allowed() -> None:
    """`MERGE` is the upsert primitive shared by Kuzu and Neo4j-5."""
    Linter().check("MERGE (n:Doc {id: $id})")


@pytest.mark.knowledge
@pytest.mark.unit
def test_create_allowed() -> None:
    """`CREATE` is the explicit-insert primitive."""
    Linter().check("CREATE (n:Doc {id: $id})")


@pytest.mark.knowledge
@pytest.mark.unit
def test_where_allowed() -> None:
    """`WHERE` is the post-MATCH filter clause."""
    Linter().check("MATCH (n:Doc) WHERE n.id = $id RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_return_allowed() -> None:
    """`RETURN` is the projection clause."""
    Linter().check("MATCH (n) RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_order_by_allowed() -> None:
    """`ORDER BY` sorts the projection (post-RETURN)."""
    Linter().check("MATCH (n:Doc) RETURN n ORDER BY n.id")


@pytest.mark.knowledge
@pytest.mark.unit
def test_limit_allowed() -> None:
    """`LIMIT` caps the projection size."""
    Linter().check("MATCH (n) RETURN n LIMIT 10")


@pytest.mark.knowledge
@pytest.mark.unit
def test_skip_allowed() -> None:
    """`SKIP` offsets the projection."""
    Linter().check("MATCH (n) RETURN n SKIP 5 LIMIT 10")


@pytest.mark.knowledge
@pytest.mark.unit
def test_with_allowed() -> None:
    """`WITH` chains query stages."""
    Linter().check("MATCH (n:Doc) WITH n WHERE n.score > 0 RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_unwind_allowed() -> None:
    """`UNWIND` expands a list parameter into rows."""
    Linter().check("UNWIND $ids AS id MATCH (n:Doc {id: id}) RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_exists_subquery_allowed() -> None:
    """`EXISTS { ... }` is the existence-check subquery (Kuzu + Neo4j-5)."""
    Linter().check("MATCH (n:Doc) WHERE EXISTS { MATCH (n)-[:REL]->(m) } RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_count_subquery_rejected() -> None:
    """`COUNT { MATCH ... RETURN ... }` is a Neo4j-5+ subquery expression
    that RyuGraph does not implement.

    The prior regex-based linter accepted this form because it could not
    distinguish a count subquery from a plain `COUNT()` aggregation. The
    AST-based linter (graphglot's neo4j-2025+ dialect) rejects it at
    parse time — correct for the portable subset, since the underlying
    graph backend cannot execute it.
    """
    with pytest.raises(UnportableCypherError):
        Linter().check("MATCH (n:Doc) RETURN n, COUNT { MATCH (n)-[:REL]->(m) RETURN m } AS c")


@pytest.mark.knowledge
@pytest.mark.unit
def test_param_allowed() -> None:
    """`$param` is the parameter-binding marker (loud-fail otherwise)."""
    Linter().check("MATCH (n:Doc {id: $id}) RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_on_create_set_allowed() -> None:
    """`ON CREATE SET` runs assignments only on MERGE-creates."""
    Linter().check("MERGE (n:Doc {id: $id}) ON CREATE SET n.created = timestamp()")


@pytest.mark.knowledge
@pytest.mark.unit
def test_on_match_set_allowed() -> None:
    """`ON MATCH SET` runs assignments only on MERGE-matches."""
    Linter().check("MERGE (n:Doc {id: $id}) ON MATCH SET n.touched = timestamp()")


@pytest.mark.knowledge
@pytest.mark.unit
def test_detach_delete_allowed() -> None:
    """`DETACH DELETE` removes a node and its relationships atomically."""
    Linter().check("MATCH (n:Doc {id: $id}) DETACH DELETE n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_starts_with_allowed() -> None:
    """`STARTS WITH` is the prefix-match string operator."""
    Linter().check("MATCH (n:Doc) WHERE n.id STARTS WITH $prefix RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_ends_with_allowed() -> None:
    """`ENDS WITH` is the suffix-match string operator."""
    Linter().check("MATCH (n:Doc) WHERE n.id ENDS WITH $suffix RETURN n")


@pytest.mark.knowledge
@pytest.mark.unit
def test_contains_allowed() -> None:
    """`CONTAINS` is the substring-match string operator."""
    Linter().check("MATCH (n:Doc) WHERE n.id CONTAINS $needle RETURN n")
