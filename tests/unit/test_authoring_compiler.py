# SPDX-License-Identifier: Apache-2.0
"""Authoring-format compiler unit tests (P4) -- lowering + loud shape errors."""

from __future__ import annotations

from typing import Annotated, Any, get_origin, get_type_hints

import pytest

from stargraph.authoring import authoring_clips, compile_authoring, is_authoring_format
from stargraph.errors import IRValidationError
from stargraph.graph.definition import Graph
from stargraph.ir._models import GotoAction, HaltAction

pytestmark = pytest.mark.unit


def _doc(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "research-bot",
        "state": {
            "question": "str",
            "answer": "str",
            "rationale": "str",
            "verdict": {"type": "str", "route": True},
        },
        "nodes": {
            "work": {"kind": "passthrough"},
            "judge": {"kind": "echo"},
        },
        "routes": {
            "judge": {"fail": "work", "pass": "done"},
        },
    }
    base.update(overrides)
    return base


def test_is_authoring_format_detection() -> None:
    assert is_authoring_format(_doc())
    assert not is_authoring_format({"ir_version": "1.0.0", "id": "x", "nodes": []})
    assert not is_authoring_format("not a mapping")
    assert not is_authoring_format({"state": {}})  # no nodes


def test_compiles_to_valid_graph() -> None:
    ir = compile_authoring(_doc())

    assert ir.id == "graph:research-bot"
    assert [n.id for n in ir.nodes] == ["work", "judge"]
    assert ir.state_class == "_sg_authored_research_bot:State"

    graph = Graph(ir)  # full IR validation + state-class resolution
    state = graph.state_schema()
    assert state.question == ""  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]

    hints = get_type_hints(graph.state_schema, include_extras=True)
    assert get_origin(hints["verdict"]) is Annotated  # route: true -> Mirror
    assert get_origin(hints["question"]) is not Annotated


def test_value_routes_compile_to_when_sugar() -> None:
    ir = compile_authoring(_doc())

    by_id = {r.id: r for r in ir.rules}
    fail_rule = by_id["r-judge-fail"]
    assert fail_rule.when == {"node": "judge", "verdict": "fail"}
    assert isinstance(fail_rule.then[0], GotoAction)
    assert fail_rule.then[0].target == "work"

    pass_rule = by_id["r-judge-pass"]
    assert isinstance(pass_rule.then[0], HaltAction)


def test_unconditional_route_and_done() -> None:
    ir = compile_authoring(_doc(routes={"work": "judge", "judge": "done"}))

    by_id = {r.id: r for r in ir.rules}
    assert by_id["r-work"].when == {"node": "work"}
    assert isinstance(by_id["r-judge"].then[0], HaltAction)


def test_react_tools_default_version() -> None:
    ir = compile_authoring(
        _doc(
            nodes={
                "research": {
                    "kind": "react",
                    "tools": ["std.web_search", "custom.thing@2"],
                },
            },
            routes={},
        )
    )
    assert ir.nodes[0].config["tools"] == ["std.web_search@1", "custom.thing@2"]


def test_state_defaults_and_types() -> None:
    ir = compile_authoring(
        _doc(
            state={
                "count": {"type": "int", "default": 3},
                "flag": "bool",
                "items": "list",
            },
            routes={},
        )
    )
    state = Graph(ir).state_schema()
    assert state.count == 3  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    assert state.flag is False  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
    assert state.items == []  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ({"bogus": 1}, "unknown top-level keys"),
        ({"id": "Bad Id"}, "id must match"),
        ({"nodes": {}}, "non-empty mapping"),
        ({"nodes": {"n": {}}}, "missing `kind`"),
        ({"nodes": {"n": "echo"}}, "must be a mapping"),
        ({"state": {"x": "complex"}}, "unsupported type"),
        ({"state": {"x": {"type": "str", "bogus": 1}}}, "unknown keys"),
        ({"routes": {"ghost": "done"}}, "not a declared node"),
        ({"routes": {"work": "ghost"}}, "unknown node"),
        ({"nodes": {"n": {"kind": "echo", "spec": "x.yaml"}}}, "only valid on kind: subgraph"),
    ],
)
def test_loud_shape_errors(mutation: dict[str, Any], match: str) -> None:
    with pytest.raises(IRValidationError, match=match):
        compile_authoring(_doc(**mutation))


def test_value_route_without_routed_verdict_is_loud() -> None:
    doc = _doc(state={"question": "str", "verdict": "str"})  # no route: true

    with pytest.raises(IRValidationError, match="routed `verdict` field") as excinfo:
        compile_authoring(doc)
    assert "route: true" in str(excinfo.value)


def test_authoring_clips_renders_rules() -> None:
    lines = authoring_clips(compile_authoring(_doc()))

    assert any("r-judge-fail" in line and "goto work" in line for line in lines)
    assert any('(verdict (value "pass"))' in line and "halt" in line for line in lines)
