# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the ``RuleSpec.when`` mapping sugar.

``when`` accepts a raw CLIPS string (verbatim passthrough, unchanged
behavior) or the mapping sugar ``{node: <id>, <mirror-field>: <value>}``.
Coverage: :func:`stargraph.ir._when.compile_when` compilation shapes and
error cases, the :func:`stargraph.ir.validate` stage that surfaces them
as structured rows, defrule generation through the sugar, and backfill
ownership from the ``node`` key (in ``test_ir_backfill.py``).
"""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.ir import GotoAction, HaltAction, IRDocument, NodeSpec, RuleSpec, validate
from stargraph.ir._when import compile_when, when_node_ref

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# compile_when
# ---------------------------------------------------------------------------


def test_raw_string_passes_through_verbatim() -> None:
    raw = '?n <- (node-id (id work)) (phase_verdict (value "refine"))'
    assert compile_when(raw) == raw


def test_node_only() -> None:
    assert compile_when({"node": "work"}) == "(node-id (id work))"


def test_state_only() -> None:
    assert compile_when({"phase_verdict": "refine"}) == '(phase_verdict (value "refine"))'


def test_node_and_fields_node_pattern_first() -> None:
    out = compile_when({"score": "high", "node": "work", "phase_verdict": "refine"})
    assert out == ('(node-id (id work)) (score (value "high")) (phase_verdict (value "refine"))')


def test_non_string_scalars_compare_as_str() -> None:
    assert compile_when({"rounds": 2}) == '(rounds (value "2"))'
    assert compile_when({"ratio": 0.5}) == '(ratio (value "0.5"))'
    assert compile_when({"ready": True}) == '(ready (value "True"))'


def test_value_escaping() -> None:
    out = compile_when({"note": 'say "hi" \\ bye'})
    assert out == '(note (value "say \\"hi\\" \\\\ bye"))'


def test_dotted_field_key_mangles_to_registered_spelling() -> None:
    # The routing engine registers dotted templates dot-mangled
    # (fathom idents forbid dots); the sugar matches that spelling.
    assert compile_when({"user.verdict": "pass"}) == '(user-verdict (value "pass"))'


def test_empty_mapping_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        compile_when({})


def test_bad_node_ref_rejected() -> None:
    with pytest.raises(ValueError, match=r"when\.node"):
        compile_when({"node": "wo rk"})
    with pytest.raises(ValueError, match=r"when\.node"):
        compile_when({"node": 7})


def test_bad_field_key_rejected() -> None:
    with pytest.raises(ValueError, match="fact-template"):
        compile_when({"1bad": "x"})
    with pytest.raises(ValueError, match="fact-template"):
        compile_when({"has space": "x"})


def test_non_scalar_value_rejected() -> None:
    with pytest.raises(ValueError, match="scalar"):
        compile_when({"tags": ["a", "b"]})


def test_when_node_ref() -> None:
    assert when_node_ref({"node": "work", "x": "y"}) == "work"
    assert when_node_ref({"x": "y"}) is None
    assert when_node_ref("?n <- (node-id (id work))") is None


# ---------------------------------------------------------------------------
# validate() stage
# ---------------------------------------------------------------------------


def _doc_dict(when: Any) -> dict[str, Any]:
    return {
        "ir_version": "1.1.0",
        "id": "run:when-sugar",
        "nodes": [{"id": "work", "kind": "echo"}],
        "rules": [{"id": "r-1", "when": when, "then": [{"kind": "halt", "reason": "x"}]}],
    }


def test_validate_accepts_valid_sugar() -> None:
    assert validate(_doc_dict({"node": "work", "phase_verdict": "refine"})) == []


def test_validate_accepts_raw_string_untouched() -> None:
    assert validate(_doc_dict("free text the engine parses")) == []


def test_validate_rejects_malformed_sugar_with_path() -> None:
    errors = validate(_doc_dict({"node": "wo rk"}))
    assert len(errors) == 1
    assert errors[0].context["path"] == "/rules/0/when"
    assert "when.node" in errors[0].context["hint"]


def test_validate_rejects_unknown_node_ref() -> None:
    errors = validate(_doc_dict({"node": "ghost"}))  # typo'd / nonexistent node
    assert len(errors) == 1
    assert errors[0].context["path"] == "/rules/0/when"
    assert "ghost" in errors[0].context["hint"]


# ---------------------------------------------------------------------------
# defrule generation through the sugar
# ---------------------------------------------------------------------------


def test_defrule_compiles_sugar_when() -> None:
    from stargraph.fathom._ir_builder import _defrule  # pyright: ignore[reportPrivateUsage]

    rule = RuleSpec(
        id="r-work-refine",
        when={"node": "work", "phase_verdict": "refine"},
        then=[GotoAction(target="work")],
    )
    out = _defrule(rule)
    assert out == (
        "(defrule r-work-refine "
        '(node-id (id work)) (phase_verdict (value "refine")) '
        '=> (assert (stargraph_action (kind goto) (target "work") '
        '(rule_id "r-work-refine"))))'
    )


def test_defrule_stamps_rule_id_on_goto_and_halt() -> None:
    """Routing facts name the rule that asserted them (the ``rule_id`` slot has
    existed on the deftemplate since FR-3; the emitter now fills it)."""
    from stargraph.fathom._ir_builder import _defrule  # pyright: ignore[reportPrivateUsage]

    goto = _defrule(RuleSpec(id="r-goto", when={"node": "a"}, then=[GotoAction(target="b")]))
    halt = _defrule(RuleSpec(id="r-halt", when={"node": "a"}, then=[HaltAction(reason="done")]))
    assert goto is not None and '(rule_id "r-goto")' in goto
    assert halt is not None and '(rule_id "r-halt")' in halt


def test_defrule_escapes_rule_id() -> None:
    """A rule id reaching CLIPS is escaped like every other interpolated string."""
    from stargraph.fathom._ir_builder import _defrule  # pyright: ignore[reportPrivateUsage]

    out = _defrule(RuleSpec(id='r-"x"', when={"node": "a"}, then=[GotoAction(target="b")]))
    assert out is not None
    assert '(rule_id "r-\\"x\\"")' in out


def test_simulate_matches_sugar_rules() -> None:
    """The FR-9 simulator sees the compiled text, so node refs still match."""
    import asyncio

    from stargraph.graph import Graph

    doc = IRDocument(
        ir_version="1.1.0",
        id="run:sim-sugar",
        nodes=[NodeSpec(id="work", kind="echo")],
        rules=[
            RuleSpec(id="r-1", when={"node": "work"}, then=[HaltAction(reason="x")]),
        ],
    )
    graph = Graph(ir=doc)
    result = asyncio.run(graph.simulate({"work": {"out": 1}}))
    (firing,) = result.rule_firings
    assert firing.fired
    assert firing.matched_nodes == ("work",)
