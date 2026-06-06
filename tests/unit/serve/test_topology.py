# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.serve.topology` (IR -> UI projection).

:func:`derive_topology` walks an :class:`IRDocument`'s rules, attributes
each rule to its owning node (via :func:`backfill_rule_node_ids` for
1.0.0 docs), and emits typed UI edges per routing action plus per-node
``is_entry`` / ``is_terminal`` / ``hitl`` flags. These tests cover the
five edge kinds and the two flag post-passes:

* ``goto`` -> ``goto`` for forward edges (target row > source row).
* ``goto`` -> ``loop`` when target == source.
* ``goto`` -> ``goto_back`` when target row < source row in the
  declaration order (back-arc render hint).
* ``retry`` -> ``loop`` and ``backoff_ms`` carries through.
* ``parallel`` -> one ``parallel`` edge per target, ``strategy`` /
  ``join`` carried.
* ``interrupt`` -> ``interrupt_gate`` self-edge + ``hitl=True`` on owner.
* ``halt`` -> ``is_terminal=True``, no edge.
* ``assert`` / ``retract`` -> no edge, no flags.
* Rules whose owner can't be resolved (no ``node_id`` after backfill,
  or owner not in ``doc.nodes``) land in ``orphan_rules`` and emit
  zero edges.
* Entry/terminal post-pass: zero in-edges + outgoing -> ``is_entry``;
  any in-edge + zero outgoing -> ``is_terminal``.

The fixture builds a small but exhaustive IRDocument programmatically
rather than parsing YAML; ``loads()`` is JSON-only and adding YAML
plumbing would muddy what these tests assert.
"""

from __future__ import annotations

import pytest

from stargraph.ir import (
    AssertAction,
    GotoAction,
    HaltAction,
    IRDocument,
    NodeSpec,
    ParallelAction,
    RetryAction,
    RuleSpec,
)
from stargraph.ir._models import InterruptAction
from stargraph.serve.topology import derive_topology


def _nodes(*ids: str) -> list[NodeSpec]:
    return [NodeSpec(id=i, kind="echo") for i in ids]


# ---------------------------------------------------------------------------
# goto / loop / goto_back
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_goto_forward_emits_goto_edge() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a", "b"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[GotoAction(target="b")],
            ),
        ],
    )
    topo = derive_topology(doc)

    assert [e.kind for e in topo.edges] == ["goto"]
    edge = topo.edges[0]
    assert edge.source == "a"
    assert edge.target == "b"
    assert edge.rule_id == "r1"
    assert topo.orphan_rules == []


@pytest.mark.unit
def test_goto_self_emits_loop_edge() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[GotoAction(target="a")],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert [e.kind for e in topo.edges] == ["loop"]


@pytest.mark.unit
def test_goto_to_earlier_node_emits_goto_back() -> None:
    """Target row < source row in declaration order -> render as back-arc."""
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a", "b", "c"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="c",
                when="?n <- (node-id (id c))",
                then=[GotoAction(target="a")],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert [e.kind for e in topo.edges] == ["goto_back"]


# ---------------------------------------------------------------------------
# retry, parallel, interrupt, halt, assert/retract
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_retry_emits_loop_with_backoff_ms() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a", "b"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[RetryAction(target="a", backoff_ms=250)],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert len(topo.edges) == 1
    edge = topo.edges[0]
    assert edge.kind == "loop"
    assert edge.backoff_ms == 250


@pytest.mark.unit
def test_parallel_emits_one_edge_per_target_with_strategy_and_join() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a", "b", "c"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[
                    ParallelAction(
                        targets=["b", "c"],
                        strategy="all",
                        join="post_join",
                    ),
                ],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert [e.kind for e in topo.edges] == ["parallel", "parallel"]
    assert {e.target for e in topo.edges} == {"b", "c"}
    for e in topo.edges:
        assert e.source == "a"
        assert e.strategy == "all"
        assert e.join == "post_join"


@pytest.mark.unit
def test_interrupt_emits_self_gate_and_flags_hitl() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[InterruptAction(prompt="approve?")],
            ),
        ],
    )
    topo = derive_topology(doc)

    assert [e.kind for e in topo.edges] == ["interrupt_gate"]
    edge = topo.edges[0]
    assert edge.source == "a"
    assert edge.target == "a"
    assert next(n for n in topo.nodes if n.id == "a").hitl is True


@pytest.mark.unit
def test_halt_flags_terminal_and_emits_no_edge() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[HaltAction(reason="done")],
            ),
        ],
    )
    topo = derive_topology(doc)

    assert topo.edges == []
    assert next(n for n in topo.nodes if n.id == "a").is_terminal is True


@pytest.mark.unit
def test_assert_and_retract_contribute_no_edges() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[AssertAction(fact="compliance_clean", slots="{}")],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert topo.edges == []
    a = next(n for n in topo.nodes if n.id == "a")
    assert a.is_terminal is False
    assert a.is_entry is False


# ---------------------------------------------------------------------------
# Backfill, orphan rules, entry/terminal post-pass
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_backfill_attributes_legacy_rules_to_nodes() -> None:
    """A 1.0.0-style rule with no ``node_id`` is filled from ``when``."""
    doc = IRDocument(
        ir_version="1.0.0",
        id="g:test",
        nodes=_nodes("a", "b"),
        rules=[
            RuleSpec(
                id="r1",
                when="?n <- (node-id (id a))",
                then=[GotoAction(target="b")],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert len(topo.edges) == 1
    assert topo.edges[0].source == "a"
    # backfill mutates the doc in place -- important for downstream callers.
    assert doc.rules[0].node_id == "a"


@pytest.mark.unit
def test_unresolvable_owner_lands_in_orphan_rules() -> None:
    """When ``node_id`` cannot be inferred, the rule is reported, not dropped."""
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a", "b"),
        rules=[
            RuleSpec(
                id="r_orphan",
                when="(state (ready true))",  # no node-id pattern
                then=[GotoAction(target="b")],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert topo.edges == []
    assert topo.orphan_rules == ["r_orphan"]


@pytest.mark.unit
def test_entry_and_terminal_postpass() -> None:
    """Zero-in / outgoing -> entry; any-in / zero-out -> terminal."""
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a", "b", "c"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[GotoAction(target="b")],
            ),
            RuleSpec(
                id="r2",
                node_id="b",
                when="?n <- (node-id (id b))",
                then=[GotoAction(target="c")],
            ),
            # c has no outgoing rule -> implicit terminal via post-pass.
        ],
    )
    topo = derive_topology(doc)
    by_id = {n.id: n for n in topo.nodes}
    assert by_id["a"].is_entry is True
    assert by_id["a"].is_terminal is False
    assert by_id["b"].is_entry is False
    assert by_id["b"].is_terminal is False
    assert by_id["c"].is_entry is False
    assert by_id["c"].is_terminal is True


@pytest.mark.unit
def test_unknown_target_in_action_is_skipped() -> None:
    """Action targets pointing at non-nodes don't create dangling edges."""
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:test",
        nodes=_nodes("a"),
        rules=[
            RuleSpec(
                id="r1",
                node_id="a",
                when="?n <- (node-id (id a))",
                then=[GotoAction(target="ghost")],
            ),
        ],
    )
    topo = derive_topology(doc)
    assert topo.edges == []


@pytest.mark.unit
def test_response_carries_graph_id_and_ir_version() -> None:
    doc = IRDocument(
        ir_version="1.1.0",
        id="g:demo",
        nodes=_nodes("a"),
        rules=[],
    )
    topo = derive_topology(doc)
    assert topo.graph_id == "g:demo"
    assert topo.ir_version == "1.1.0"
    assert [n.id for n in topo.nodes] == ["a"]
