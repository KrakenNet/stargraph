# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.ir._backfill` (IR 1.1 ``RuleSpec.node_id``).

The IR gained an explicit ``node_id`` on :class:`RuleSpec` in 1.1.0, but
1.0.0 documents (and many hand-authored 1.1.0 rules) leave it ``None``
and instead encode ownership in the canonical CLIPS pattern
``(node-id (id <NAME>))`` inside the rule's ``when`` clause.
:func:`backfill_rule_node_ids` walks every ``node_id is None`` rule,
extracts the name, and -- when the name is a real node id in the
document -- writes it back.

Coverage:

* explicit ``node_id`` on a rule is preserved (no overwrite).
* legacy ``when`` strings get the inferred id.
* the binding-prefix variant ``?n <- (node-id (id X))`` matches.
* compound when-clauses (``(node-id (id X)) (state ...)``) match.
* an unknown name (typo / ref to a non-node) leaves ``node_id`` ``None``
  -- the topology layer reports such rules as orphans rather than
  silently lying about ownership.
* ``when`` without the pattern leaves ``node_id`` ``None``.
* the helper returns the document for chaining.
"""

from __future__ import annotations

import pytest

from stargraph.ir import (
    GotoAction,
    HaltAction,
    IRDocument,
    NodeSpec,
    RuleSpec,
    backfill_rule_node_ids,
)
from stargraph.ir._backfill import NODE_ID_PATTERN


def _doc(rules: list[RuleSpec]) -> IRDocument:
    return IRDocument(
        ir_version="1.1.0",
        id="run:test-backfill",
        nodes=[
            NodeSpec(id="alpha", kind="echo"),
            NodeSpec(id="beta", kind="echo"),
        ],
        rules=rules,
    )


@pytest.mark.unit
def test_explicit_node_id_is_preserved() -> None:
    """An already-populated ``node_id`` is not overwritten by inference."""
    doc = _doc(
        [
            RuleSpec(
                id="r1",
                node_id="alpha",
                when="?n <- (node-id (id beta))",  # disagrees on purpose
                then=[HaltAction(reason="x")],
            ),
        ],
    )
    backfill_rule_node_ids(doc)
    assert doc.rules[0].node_id == "alpha"


@pytest.mark.unit
def test_binding_prefix_pattern_is_inferred() -> None:
    """``?n <- (node-id (id X))`` -> ``node_id = "X"`` when X is a node."""
    doc = _doc(
        [
            RuleSpec(
                id="r1",
                when="?n <- (node-id (id alpha))",
                then=[GotoAction(target="beta")],
            ),
        ],
    )
    backfill_rule_node_ids(doc)
    assert doc.rules[0].node_id == "alpha"


@pytest.mark.unit
def test_compound_when_clause_is_inferred() -> None:
    """The pattern matches even when followed by extra fact constraints."""
    doc = _doc(
        [
            RuleSpec(
                id="r1",
                when="?n <- (node-id (id alpha)) (state (compliance_status pending))",
                then=[GotoAction(target="beta")],
            ),
        ],
    )
    backfill_rule_node_ids(doc)
    assert doc.rules[0].node_id == "alpha"


@pytest.mark.unit
def test_unknown_name_leaves_node_id_none() -> None:
    """Inferred names that aren't node ids in the doc are dropped (orphan)."""
    doc = _doc(
        [
            RuleSpec(
                id="r1",
                when="?n <- (node-id (id ghost_node))",
                then=[HaltAction(reason="x")],
            ),
        ],
    )
    backfill_rule_node_ids(doc)
    assert doc.rules[0].node_id is None


@pytest.mark.unit
def test_when_without_pattern_leaves_node_id_none() -> None:
    """No ``(node-id (id X))`` in ``when`` -> stays ``None``."""
    doc = _doc(
        [
            RuleSpec(
                id="r1",
                when="(state (ready true))",
                then=[HaltAction(reason="x")],
            ),
        ],
    )
    backfill_rule_node_ids(doc)
    assert doc.rules[0].node_id is None


@pytest.mark.unit
def test_returns_document_for_chaining() -> None:
    """The helper returns the same doc instance to support ``derive(backfill(doc))``."""
    doc = _doc([])
    assert backfill_rule_node_ids(doc) is doc


@pytest.mark.unit
def test_node_id_pattern_extracts_name_with_dashes_and_dots() -> None:
    """Stable-id grammar: alnum, ``_``, ``-``, ``.`` are all valid name chars."""
    m = NODE_ID_PATTERN.search("?n <- (node-id (id sub-graph.enrichment_v2))")
    assert m is not None
    assert m.group(1) == "sub-graph.enrichment_v2"
