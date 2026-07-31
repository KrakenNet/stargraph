# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``kind: react`` / ``kind: code`` (P3a composition prebuilts).

No dspy import, no LM: covers the pure helpers (trajectory compression,
fence stripping, tool-name sanitization) and drives :class:`CodeNode`
end-to-end with a fake generator against the REAL ``std.python_exec``
tool through the real ``execute_tool`` pipeline (capability gate,
provenance facts). The dspy build path lives in
``tests/integration/test_react_code_builder.py``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.nodes.code import CodeNode, CodeNodeConfig, strip_code_fences
from stargraph.nodes.react import trajectory_to_trace
from stargraph.registry.tools import Tool, ToolRegistry
from stargraph.security.capabilities import Capabilities, CapabilityClaim
from stargraph.tools.std.python_exec import python_exec

pytestmark = pytest.mark.unit


# ------------------------------------------------------------ pure helpers


def test_trajectory_to_trace_drops_finish() -> None:
    trajectory = {
        "thought_0": "look it up",
        "tool_name_0": "std_web_search",
        "tool_args_0": {"query": "stargraph"},
        "observation_0": "found it",
        "thought_1": "done",
        "tool_name_1": "finish",
        "tool_args_1": {},
        "observation_1": "Completed.",
    }
    assert trajectory_to_trace(trajectory) == [
        {
            "tool": "std_web_search",
            "args": {"query": "stargraph"},
            "observation": "found it",
        }
    ]


def test_trajectory_to_trace_empty() -> None:
    assert trajectory_to_trace({}) == []


def test_tool_callable_name_sanitizes() -> None:
    from stargraph.nodes.react import _tool_callable_name  # pyright: ignore[reportPrivateUsage]

    assert _tool_callable_name("std", "web_search") == "std_web_search"
    assert _tool_callable_name("my-ns", "do.thing") == "my_ns_do_thing"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("print(1)", "print(1)"),
        ("```python\nprint(1)\n```", "print(1)"),
        ("```\nprint(1)\n```", "print(1)"),
        ("  ```py\nx = 1\nprint(x)\n```  ", "x = 1\nprint(x)"),
    ],
)
def test_strip_code_fences(raw: str, expected: str) -> None:
    assert strip_code_fences(raw) == expected


# ------------------------------------------------------------ CodeNode


class _FakeGenerator(NodeBase):
    def __init__(self, code: str) -> None:
        self._code = code

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del state, ctx
        return {"code": self._code, "reasoning": "internal"}


class _State(BaseModel):
    task: str = "print hello"


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(cast("Tool", python_exec))
    return reg


def _caps() -> Capabilities:
    return Capabilities(granted={CapabilityClaim(name="tools", scope="std:exec")})  # pyright: ignore[reportUnhashable]


def _ctx(*, registry: ToolRegistry | None, capabilities: Any = None) -> Any:
    graph = None if registry is None else SimpleNamespace(registry=registry)
    return SimpleNamespace(
        run_id="run-code",
        step=0,
        graph=graph,
        fathom=None,
        capabilities=capabilities,
        is_replay=False,
        tool_cassette=None,
    )


def _node(code: str, **config: Any) -> CodeNode:
    return CodeNode(
        inner=_FakeGenerator(code),
        config=CodeNodeConfig.model_validate(config),
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_code_node_pass_verdict_on_exit_zero() -> None:
    node = _node("```python\nprint('hi')\n```")
    out = _run(node.execute(_State(), _ctx(registry=_registry(), capabilities=_caps())))
    assert out["verdict"] == "pass"
    assert out["code"] == "print('hi')"
    assert out["run_result"]["stdout"] == "hi\n"
    assert out["run_result"]["exit_code"] == 0


def test_code_node_fail_verdict_on_nonzero_exit() -> None:
    node = _node("import sys; sys.exit(3)")
    out = _run(node.execute(_State(), _ctx(registry=_registry(), capabilities=_caps())))
    assert out["verdict"] == "fail"
    assert out["run_result"]["exit_code"] == 3


def test_code_node_capability_default_deny() -> None:
    node = _node("print(1)")
    ctx = _ctx(registry=_registry(), capabilities=Capabilities(granted=set()))
    with pytest.raises(CapabilityError):
        _run(node.execute(_State(), ctx))


def test_code_node_empty_generation_fails_loud() -> None:
    node = _node("")
    with pytest.raises(StargraphRuntimeError, match="empty script"):
        _run(node.execute(_State(), _ctx(registry=_registry(), capabilities=_caps())))


def test_code_node_without_registry_fails_loud() -> None:
    node = _node("print(1)")
    with pytest.raises(StargraphRuntimeError, match="no tool registry"):
        _run(node.execute(_State(), _ctx(registry=None)))


def test_code_node_rejects_bare_context() -> None:
    node = _node("print(1)")
    bare: Any = SimpleNamespace(run_id="r1")
    with pytest.raises(AttributeError, match="is_replay"):
        _run(node.execute(_State(), bare))
