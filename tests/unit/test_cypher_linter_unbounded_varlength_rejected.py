# SPDX-License-Identifier: Apache-2.0
"""Cypher linter rejects unbounded variable-length traversals (FR-12, AC-9.2).

The Kuzu / Neo4j-5 portable subset bans bare ``*`` quantifiers on
relationship patterns -- they degrade to graph-wide traversals on Neo4j
and are unsupported on Kuzu. Bounded forms (``*1..5``, ``*..10``) stay
in the subset.
"""

from __future__ import annotations

import pytest

from stargraph.errors import UnportableCypherError
from stargraph.stores.cypher import Linter


@pytest.mark.knowledge
@pytest.mark.unit
def test_unbounded_varlength_rejected() -> None:
    """Bare ``*`` on a relationship pattern raises UnportableCypherError."""
    linter = Linter()
    with pytest.raises(UnportableCypherError) as excinfo:
        linter.check("MATCH (a)-[:REL*]->(b) RETURN b")
    assert excinfo.value.context["rule"] == "varlen-unbounded"


@pytest.mark.knowledge
@pytest.mark.unit
def test_bounded_varlength_accepted() -> None:
    """Explicit lower and upper bounds (``*1..5``) pass the linter."""
    linter = Linter()
    linter.check("MATCH (a)-[:REL*1..5]->(b) RETURN b")


@pytest.mark.knowledge
@pytest.mark.unit
def test_implicit_lower_bound_accepted() -> None:
    """Implicit lower bound with explicit upper (``*..10``) passes."""
    linter = Linter()
    linter.check("MATCH (a)-[:REL*..10]->(b) RETURN b")
