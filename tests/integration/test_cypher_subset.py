# SPDX-License-Identifier: Apache-2.0
"""Two-engine Cypher portable-subset CI gate (FR-12, AC-9.3, NFR-4).

Runs the same allow-list query corpus against an in-memory ``RyuGraphStore``
AND a Neo4j 5 testcontainer. For each query we normalise the rows into a
``set[tuple]`` and assert byte-identical equality across engines. This is the
loud-fail two-engine gate that backs the design §3.2 portable-subset
contract: any divergence -- syntax or semantics -- aborts the suite, which
in turn aborts CI when run with ``--runslow``.

Skip behaviour (NFR-4 loud-fail compatible):
- ``pytest.importorskip("testcontainers.neo4j")`` -- skip if the optional
  ``testcontainers[neo4j]`` extra is missing.
- ``pytest.importorskip("neo4j")`` -- skip if the neo4j python driver is
  missing.
- Skip if the local Docker daemon is unreachable (mirrors
  ``test_postgres_checkpointer.py``).

The skip is only valid for *environment* gaps. Once the corpus runs, any
mismatch between Kuzu and Neo4j 5 is a hard test failure -- never a skip.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

testcontainers = pytest.importorskip(
    "testcontainers.neo4j",
    reason="testcontainers[neo4j] not installed",
)
neo4j_pkg = pytest.importorskip(
    "neo4j",
    reason="neo4j python driver not installed",
)

from stargraph.stores.graph import NodeRef  # noqa: E402
from stargraph.stores.ryugraph import RyuGraphStore  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration, pytest.mark.slow]


# --------------------------------------------------------------------------- #
# Docker availability gate                                                    #
# --------------------------------------------------------------------------- #


def _docker_available() -> bool:
    """Return ``True`` iff the local Docker daemon answers a ping."""
    try:
        import docker  # type: ignore[import-untyped]

        client: Any = docker.from_env()  # pyright: ignore[reportUnknownMemberType]
        client.ping()
    except Exception:  # any failure ⇒ skip
        return False
    return True


if not _docker_available():
    pytest.skip(
        "docker daemon unavailable -- skipping Cypher two-engine gate",
        allow_module_level=True,
    )


# --------------------------------------------------------------------------- #
# Fixture corpus                                                              #
# --------------------------------------------------------------------------- #
#
# A handful of triples that exercise: distinct subject/object kinds,
# repeated subjects (fan-out), and a predicate that appears more than once.
# Both engines ingest the same fixture; downstream queries probe a subset
# of the design §3.2 portable corpus.

_TRIPLES: tuple[tuple[str, str, str, str, str], ...] = (
    # (s_id, s_kind, predicate, o_id, o_kind)
    ("alice", "Person", "knows", "bob", "Person"),
    ("alice", "Person", "knows", "carol", "Person"),
    ("bob", "Person", "knows", "carol", "Person"),
    ("alice", "Person", "owns", "doc-1", "Doc"),
    ("bob", "Person", "owns", "doc-2", "Doc"),
)


# Each entry: (query_id, cypher). All MUST be in the linter allow-list AND
# executable on both Kuzu and Neo4j 5 with the fixture above.
_CORPUS: tuple[tuple[str, str], ...] = (
    (
        "match-return-id",
        "MATCH (n:Entity) RETURN n.id AS id",
    ),
    (
        "match-where-equals",
        "MATCH (n:Entity) WHERE n.kind = 'Doc' RETURN n.id AS id",
    ),
    (
        "match-rel-projection",
        "MATCH (s:Entity)-[r:Rel]->(o:Entity) RETURN s.id AS s, r.predicate AS p, o.id AS o",
    ),
    (
        "match-rel-where-predicate",
        "MATCH (s:Entity)-[r:Rel]->(o:Entity) "
        "WHERE r.predicate = 'knows' "
        "RETURN s.id AS s, o.id AS o",
    ),
    (
        "match-starts-with",
        "MATCH (n:Entity) WHERE n.id STARTS WITH 'doc' RETURN n.id AS id",
    ),
    (
        "match-with-filter",
        "MATCH (s:Entity)-[r:Rel]->(o:Entity) "
        "WITH s, o, r WHERE r.predicate = 'knows' "
        "RETURN s.id AS s, o.id AS o",
    ),
)


# --------------------------------------------------------------------------- #
# Engine drivers                                                              #
# --------------------------------------------------------------------------- #


def _normalise_rows(rows: list[dict[str, Any]]) -> set[tuple[tuple[str, Any], ...]]:
    """Return rows as a ``set`` of sorted ``(col, val)`` tuples for comparison."""
    out: set[tuple[tuple[str, Any], ...]] = set()
    for row in rows:
        out.add(tuple(sorted(row.items())))
    return out


async def _seed_kuzu(store: RyuGraphStore) -> None:
    await store.bootstrap()
    for s_id, s_kind, p, o_id, o_kind in _TRIPLES:
        await store.add_triple(
            NodeRef(id=s_id, kind=s_kind),
            p,
            NodeRef(id=o_id, kind=o_kind),
        )


async def _run_kuzu(store: RyuGraphStore, cypher: str) -> set[tuple[tuple[str, Any], ...]]:
    rs = await store.query(cypher)
    return _normalise_rows(rs.rows)


def _seed_neo4j(driver: Any) -> None:
    """Seed the same fixture into a Neo4j 5 session.

    We use plain ``MERGE`` writes here -- they're inside the linter
    allow-list but we route them through the driver directly because
    Neo4j's session API (not Stargraph's GraphStore) is the SUT for the
    other engine.
    """
    with driver.session() as session:  # pyright: ignore[reportUnknownMemberType]
        session.run("MATCH (n) DETACH DELETE n")  # pyright: ignore[reportUnknownMemberType]
        for s_id, s_kind, p, o_id, o_kind in _TRIPLES:
            session.run(  # pyright: ignore[reportUnknownMemberType]
                "MERGE (s:Entity {id: $s_id}) "
                "ON CREATE SET s.kind = $s_kind "
                "ON MATCH SET s.kind = $s_kind "
                "MERGE (o:Entity {id: $o_id}) "
                "ON CREATE SET o.kind = $o_kind "
                "ON MATCH SET o.kind = $o_kind "
                "MERGE (s)-[r:Rel {predicate: $p}]->(o)",
                s_id=s_id,
                s_kind=s_kind,
                o_id=o_id,
                o_kind=o_kind,
                p=p,
            )


def _run_neo4j(driver: Any, cypher: str) -> set[tuple[tuple[str, Any], ...]]:
    rows: list[dict[str, Any]] = []
    with driver.session() as session:  # pyright: ignore[reportUnknownMemberType]
        result = session.run(cypher)  # pyright: ignore[reportUnknownMemberType]
        for record in result:  # pyright: ignore[reportUnknownVariableType]
            rows.append(dict(record))
    return _normalise_rows(rows)


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def neo4j_driver() -> Iterator[Any]:
    """Spin up a Neo4j 5 testcontainer, yield a connected driver, seed it."""
    from testcontainers.neo4j import Neo4jContainer  # type: ignore[import-untyped]

    with Neo4jContainer("neo4j:5") as neo:
        driver = cast("Any", neo.get_driver())  # pyright: ignore[reportUnknownMemberType]
        try:
            _seed_neo4j(driver)
            yield driver
        finally:
            driver.close()


# --------------------------------------------------------------------------- #
# Two-engine equivalence test                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(("query_id", "cypher"), _CORPUS, ids=[q[0] for q in _CORPUS])
async def test_corpus_matches_kuzu_and_neo4j(
    tmp_path: Path,
    neo4j_driver: Any,
    query_id: str,
    cypher: str,
) -> None:
    """Each corpus query returns identical row sets on Kuzu and Neo4j 5.

    Loud-fail (NFR-4): any divergence is a hard assertion error. Skipping is
    only valid for environment gaps (no Docker, missing optional packages),
    handled at module level above.
    """
    del query_id  # only used as the parametrize id
    kuzu_store = RyuGraphStore(tmp_path / "kuzu-graph")
    await _seed_kuzu(kuzu_store)

    kuzu_rows = await _run_kuzu(kuzu_store, cypher)
    neo4j_rows = _run_neo4j(neo4j_driver, cypher)

    assert kuzu_rows == neo4j_rows, (
        f"Cypher subset divergence on {cypher!r}:\n"
        f"  kuzu : {sorted(kuzu_rows)}\n"
        f"  neo4j: {sorted(neo4j_rows)}"
    )
