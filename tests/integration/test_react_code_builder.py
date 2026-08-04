# SPDX-License-Identifier: Apache-2.0
"""``kind: react`` / ``kind: code`` builder + execute tests against real dspy.

Builder paths mirror ``test_prebuilt_node_builder.py`` (no LM calls).
The react execute test drives ``dspy.ReAct`` with a ``DummyLM`` scripted
through one tool call, proving the bridged registry tool runs through
the real ``execute_tool`` pipeline (provenance facts recorded) and the
trajectory lands in ``tool_trace``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("dspy", reason="dspy required for react/code builder tests")

import dspy  # pyright: ignore[reportMissingTypeStubs]
from dspy.utils import DummyLM  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel

from stargraph.errors import IRValidationError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.code import CodeNode
from stargraph.nodes.react import ReactAgentNode
from stargraph.nodes.registry import build_node_registry
from stargraph.registry.tools import Tool, ToolRegistry
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

pytestmark = pytest.mark.integration

_FAKE_LM_MODEL = "openai/fake-model-for-build-tests"


def _ctx_lm(lm: Any) -> Any:
    return dspy.context(lm=lm)  # pyright: ignore[reportUnknownMemberType]


def _build(kind: str, **config: object) -> Any:
    registry = build_node_registry([NodeSpec(id="n", kind=kind, config=dict(config))])
    return registry["n"]


def test_react_builds_from_registry() -> None:
    with _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")):
        node = _build("react", tools=["std.web_search@1"])
    assert isinstance(node, ReactAgentNode)
    assert node.config.max_iters == 8


def test_code_builds_from_registry() -> None:
    with _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")):
        node = _build("code", input="task")
    assert isinstance(node, CodeNode)


@pytest.mark.parametrize(
    ("kind", "config", "match"),
    [
        ("react", {}, "invalid config"),  # tools required
        ("react", {"tools": []}, "invalid config"),
        ("react", {"tools": ["std.web_search@1"], "input": "not id"}, "valid identifier"),
        ("react", {"tools": ["std.web_search@1"], "max_iters": 0}, "invalid config"),
        ("code", {"timeout_s": -1}, "invalid config"),
        ("code", {"input": "not id"}, "valid identifier"),
        ("code", {"unknown_key": 1}, "invalid config"),
    ],
)
def test_config_loud_fail_paths(kind: str, config: dict[str, Any], match: str) -> None:
    with (
        _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")),
        pytest.raises(IRValidationError, match=match),
    ):
        _build(kind, **config)


@pytest.mark.parametrize("kind", ["react", "code"])
def test_no_lm_anywhere_fails_loud(kind: str) -> None:
    config: dict[str, Any] = {"tools": ["std.web_search@1"]} if kind == "react" else {}
    with _ctx_lm(None), pytest.raises(IRValidationError, match="no LM configured"):
        _build(kind, **config)


# ------------------------------------------------------- react execute path


@tool(
    name="lookup",
    namespace="test",
    version="1",
    side_effects=SideEffects.none,
    input_schema={
        "type": "object",
        "properties": {"key": {"type": "string"}},
        "required": ["key"],
    },
    output_schema={
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    },
)
def lookup_tool(key: str) -> dict[str, Any]:
    return {"value": f"value-for-{key}"}


class _RecordingFathom:
    def __init__(self) -> None:
        self.templates: list[str] = []

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: dict[str, Any],
    ) -> None:
        del slots, provenance
        self.templates.append(template)


class _ReactState(BaseModel):
    question: str = "what is the value for alpha?"


async def test_react_execute_bridges_tool_through_pipeline() -> None:
    registry = ToolRegistry()
    registry.register(cast("Tool", lookup_tool))

    fathom = _RecordingFathom()
    run_ctx: Any = SimpleNamespace(
        run_id="run-react",
        step=2,
        graph=SimpleNamespace(registry=registry),
        fathom=fathom,
        capabilities=None,
        is_replay=False,
        tool_cassette=None,
    )

    lm = DummyLM(
        [
            {
                "next_thought": "I should look up alpha.",
                "next_tool_name": "test_lookup",
                "next_tool_args": {"key": "alpha"},
            },
            {
                "next_thought": "I have the value; finish.",
                "next_tool_name": "finish",
                "next_tool_args": {},
            },
            {"reasoning": "Looked it up.", "answer": "value-for-alpha"},
        ]
    )

    with _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")):
        node = _build("react", tools=["test.lookup@1"], max_iters=3)

    with _ctx_lm(lm):
        out = await node.execute(_ReactState(), run_ctx)

    assert out["answer"] == "value-for-alpha"
    assert out["tool_trace"] == [
        {
            "tool": "test_lookup",
            "args": {"key": "alpha"},
            "observation": "{'value': 'value-for-alpha'}",
        }
    ]
    # The bridged call went through execute_tool: provenance facts recorded.
    assert fathom.templates == ["stargraph.tool-call", "stargraph.tool-result"]
