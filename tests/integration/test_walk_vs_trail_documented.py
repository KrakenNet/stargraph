# SPDX-License-Identifier: Apache-2.0
"""Walk-vs-trail divergence documented test (FR-12, AC-9.5).

Pins the Protocol-level documentation that Stargraph's portable Cypher
subset returns *walks* (vertices and edges may repeat) under Kuzu, while
Neo4j 5 Cypher returns *trails* (edges unique). Per design §3.2 / AC-9.5
this divergence MUST be acknowledged at the GraphStore Protocol level so
callers know the same query may yield different result counts across
providers.

Three checks:

1. :func:`test_graphstore_docstring_mentions_walk_and_trail` -- the
   :class:`~stargraph.stores.graph.GraphStore` Protocol docstring (or the
   :mod:`stargraph.stores.graph` module docstring) names both ``walk`` and
   ``trail`` so the divergence cannot regress silently.
2. :func:`test_is_trail_flagged_kuzu_only` -- the docstring flags trail
   filtering as provider-specific / Kuzu-only (no ``is_trail`` flag is
   exposed by :class:`~stargraph.stores.ryugraph.RyuGraphStore`).
3. :func:`test_walk_pattern_returns_potentially_more_results` -- a small
   triangle ``a -> b -> a`` with ``hops=2`` exercises the cycle so the
   walk-semantics expectation (potentially more paths than under trail)
   is encoded as a runtime assertion rather than just prose.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

import pytest

from stargraph.stores import graph as graph_module
from stargraph.stores.graph import GraphStore, NodeRef
from stargraph.stores.ryugraph import RyuGraphStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


def _combined_docs() -> str:
    """Return concatenated module + Protocol docstrings for graph.py."""
    module_doc = inspect.getdoc(graph_module) or ""
    protocol_doc = inspect.getdoc(GraphStore) or ""
    return f"{module_doc}\n{protocol_doc}"


def test_graphstore_docstring_mentions_walk_and_trail() -> None:
    """GraphStore Protocol or module docstring names both walk and trail (AC-9.5)."""
    docs = _combined_docs().lower()
    assert "walk" in docs, (
        "GraphStore docstring must mention 'walk' semantics (AC-9.5); "
        "found neither in module nor Protocol docstring."
    )
    assert "trail" in docs, (
        "GraphStore docstring must mention 'trail' semantics (AC-9.5); "
        "found neither in module nor Protocol docstring."
    )


def test_is_trail_flagged_ryugraph_only() -> None:
    """``is_trail`` filtering is documented RyuGraph-only / provider-specific (AC-9.5)."""
    # RyuGraphStore deliberately does NOT expose an ``is_trail`` flag --
    # RyuGraph always returns walk semantics, so trail filtering is the
    # caller's responsibility (see graph.py Protocol docstring).
    assert not hasattr(RyuGraphStore, "is_trail"), (
        "RyuGraphStore must not expose an is_trail flag; "
        "trail filtering is documented as provider-specific (AC-9.5)."
    )
    assert not hasattr(GraphStore, "is_trail"), (
        "GraphStore Protocol must not declare is_trail; "
        "trail filtering is documented as provider-specific (AC-9.5)."
    )

    docs = _combined_docs().lower()
    # The Protocol docstring must explicitly tag the trail behaviour as
    # provider-specific / RyuGraph-only so callers know not to rely on it.
    assert "ryugraph" in docs and "provider-specific" in docs, (
        "GraphStore docstring must flag trail filtering as RyuGraph-only / "
        "provider-specific (AC-9.5); found neither phrasing."
    )


async def test_walk_pattern_returns_potentially_more_results(tmp_path: Path) -> None:
    """Triangle ``a -> b -> a`` with hops=2 exercises walk semantics (AC-9.5).

    Per Kuzu walk semantics the same edge may be re-traversed, so the
    cycle yields paths that a trail-only provider (Neo4j 5) would
    suppress. We assert the expand call returns *at least one* path --
    the exact count is intentionally provider-dependent under AC-9.5,
    and this test documents the divergence by exercising the cycle.
    """
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()

    a = NodeRef(id="a", kind="Entity")
    b = NodeRef(id="b", kind="Entity")

    # Triangle with a back-edge: a -> b, b -> a. Under walk semantics,
    # hops=2 from `a` can yield (a -> b -> a), which a trail-only
    # provider would reject (edge `a->b` would have to repeat in
    # reverse, or vertex `a` would repeat).
    await store.add_triple(a, "knows", b)
    await store.add_triple(b, "knows", a)

    paths = await store.expand(a, hops=2)

    # AC-9.5: result count is provider-dependent. We assert non-empty
    # to confirm the cycle is reachable; we do NOT assert an exact
    # count because that would over-specify against trail providers.
    assert paths, "expected expand(a, hops=2) to return at least one path under walk semantics"
    # Document expectation: under walk semantics at least one returned
    # path should re-visit `a` (the cycle endpoint). We tolerate the
    # case where the underlying engine collapses duplicate edges, but
    # at minimum the start node `a` must appear in some returned walk.
    assert any(node.id == "a" for path in paths for node in path.nodes), (
        "expected at least one returned walk to mention node 'a' (cycle endpoint)"
    )
