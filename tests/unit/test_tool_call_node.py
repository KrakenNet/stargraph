# SPDX-License-Identifier: Apache-2.0
"""ToolCallNode tests (P0.4 -- the first real ``execute_tool`` call site).

Drives the ``kind: tool`` node end-to-end against a real
:class:`~stargraph.registry.tools.ToolRegistry` and a real ``@tool``
callable: config validation via the registry builder, state-field ->
tool-arg projection, the nine-step pipeline's capability gate +
provenance facts, and every loud-fail path (no registry wired, unknown
tool id, missing state field).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from stargraph.errors import (
    CapabilityError,
    IRValidationError,
    PluginLoadError,
    StargraphRuntimeError,
)
from stargraph.ir._models import NodeSpec
from stargraph.nodes.base import EchoNode
from stargraph.nodes.registry import build_node_registry
from stargraph.nodes.tool_call import ToolCallNode, ToolCallNodeConfig
from stargraph.registry.tools import Tool, ToolRegistry
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@tool(
    name="greet",
    namespace="test",
    version="1",
    side_effects=SideEffects.none,
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string"}, "punct": {"type": "string"}},
        "required": ["name"],
    },
    output_schema={
        "type": "object",
        "properties": {"greeting": {"type": "string"}},
        "required": ["greeting"],
    },
)
def greet_tool(name: str, punct: str = "!") -> dict[str, Any]:
    return {"greeting": f"hello {name}{punct}"}


@tool(
    name="locked",
    namespace="test",
    version="1",
    side_effects=SideEffects.write,
    requires_capability="fs.write:/data/*",
    input_schema={"type": "object"},
    output_schema={"type": "object"},
)
def locked_tool() -> dict[str, Any]:
    return {"ok": True}


class _RecordingFathom:
    """Minimal FathomAdapter stand-in -- records assert_with_provenance calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_with_provenance(
        self,
        template: str,
        slots: dict[str, Any],
        provenance: dict[str, Any],
    ) -> None:
        del provenance
        self.calls.append((template, dict(slots)))


class _State(BaseModel):
    user_name: str = "sam"
    tool_result: dict[str, Any] = {}


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    # The decorator attaches ``.spec`` via setattr; pyright can't see the
    # structural Tool match on the plain function type, hence the cast.
    reg.register(cast("Tool", greet_tool))
    reg.register(cast("Tool", locked_tool))
    return reg


def _ctx(
    *,
    registry: ToolRegistry | None,
    fathom: _RecordingFathom | None = None,
    capabilities: Any = None,
) -> Any:
    graph = None if registry is None else SimpleNamespace(registry=registry)
    return SimpleNamespace(
        run_id="run-tool-call",
        graph=graph,
        fathom=fathom,
        capabilities=capabilities,
    )


def _node(**config: Any) -> ToolCallNode:
    return ToolCallNode(config=ToolCallNodeConfig.model_validate(config))


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Registry builder paths.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_empty_config_keeps_echo_placeholder() -> None:
    registry = build_node_registry([NodeSpec(id="t", kind="tool")])
    assert isinstance(registry["t"], EchoNode)


@pytest.mark.unit
def test_configured_tool_kind_builds_tool_call_node() -> None:
    registry = build_node_registry([NodeSpec(id="t", kind="tool", config={"tool": "test.greet@1"})])
    assert isinstance(registry["t"], ToolCallNode)


@pytest.mark.unit
def test_config_missing_tool_key_rejected() -> None:
    with pytest.raises(IRValidationError, match="invalid config"):
        build_node_registry([NodeSpec(id="t", kind="tool", config={"out": "x"})])


@pytest.mark.unit
def test_config_unknown_key_rejected() -> None:
    with pytest.raises(IRValidationError, match="invalid config"):
        build_node_registry(
            [NodeSpec(id="t", kind="tool", config={"tool": "test.greet@1", "nope": 1})]
        )


@pytest.mark.unit
def test_config_overlapping_inputs_and_static_rejected() -> None:
    with pytest.raises(IRValidationError, match="both inputs and static"):
        build_node_registry(
            [
                NodeSpec(
                    id="t",
                    kind="tool",
                    config={
                        "tool": "test.greet@1",
                        "inputs": {"name": "user_name"},
                        "static": {"name": "sam"},
                    },
                )
            ]
        )


# ---------------------------------------------------------------------------
# Execute paths.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_execute_projects_state_inputs_and_merges_out() -> None:
    node = _node(tool="test.greet@1", inputs={"name": "user_name"}, static={"punct": "?"})
    out = _run(node.execute(_State(user_name="ada"), _ctx(registry=_registry())))
    assert out == {"tool_result": {"greeting": "hello ada?"}}


@pytest.mark.unit
def test_execute_custom_out_field() -> None:
    node = _node(tool="test.greet@1", inputs={"name": "user_name"}, out="greeting_blob")
    out = _run(node.execute(_State(), _ctx(registry=_registry())))
    assert list(out) == ["greeting_blob"]


@pytest.mark.unit
def test_execute_emits_tool_call_and_result_facts() -> None:
    """Verification item: the node goes THROUGH the pipeline (facts prove it)."""
    fathom = _RecordingFathom()
    node = _node(tool="test.greet@1", inputs={"name": "user_name"})
    _run(node.execute(_State(), _ctx(registry=_registry(), fathom=fathom)))
    templates = [t for t, _ in fathom.calls]
    assert templates == ["stargraph.tool-call", "stargraph.tool-result"]
    assert fathom.calls[0][1]["tool_id"] == "greet"


@pytest.mark.unit
def test_execute_capability_gate_denies_by_default() -> None:
    """A permissioned tool + no granted capabilities -> CapabilityError."""
    from stargraph.security.capabilities import Capabilities

    node = _node(tool="test.locked@1")
    ctx = _ctx(registry=_registry(), capabilities=Capabilities(granted=set()))
    with pytest.raises(CapabilityError):
        _run(node.execute(_State(), ctx))


@pytest.mark.unit
def test_execute_without_registry_fails_loud() -> None:
    node = _node(tool="test.greet@1")
    with pytest.raises(StargraphRuntimeError, match="no tool registry"):
        _run(node.execute(_State(), _ctx(registry=None)))


@pytest.mark.unit
def test_execute_unknown_tool_id_raises_plugin_load_error() -> None:
    node = _node(tool="test.missing@1")
    with pytest.raises(PluginLoadError, match="not found in registry"):
        _run(node.execute(_State(), _ctx(registry=_registry())))


@pytest.mark.unit
def test_execute_missing_state_field_fails_loud() -> None:
    node = _node(tool="test.greet@1", inputs={"name": "no_such_field"})
    with pytest.raises(StargraphRuntimeError, match="no_such_field"):
        _run(node.execute(_State(), _ctx(registry=_registry())))
