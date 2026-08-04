# SPDX-License-Identifier: Apache-2.0
"""Tests for node kind resolution (stargraph.nodes.registry)."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.cli.run import _build_node_registry  # pyright: ignore[reportPrivateUsage]
from stargraph.errors import IRValidationError, PluginLoadError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.base import EchoNode, NodeBase
from stargraph.nodes.registry import register_node_kind


# A minimal NodeBase subclass exposed for the import-resolution test.
class _DummyNode(NodeBase):
    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {}


# A config-taking NodeBase subclass for the config-binding tests.
class _ConfiguredNode(NodeBase):
    def __init__(self, greeting: str = "") -> None:
        self.greeting = greeting

    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {"greeting": self.greeting}


@pytest.mark.unit
def test_short_kind_still_resolves() -> None:
    """Backward compat: 'echo' keeps mapping to EchoNode."""
    registry = _build_node_registry([NodeSpec(id="n1", kind="echo")])
    assert isinstance(registry["n1"], EchoNode)


@pytest.mark.unit
def test_module_class_kind_resolves_via_importlib() -> None:
    # Use __name__ so the kind matches whichever module path pytest is using
    # for this file (tests/ has no __init__.py, so the dotted path varies).
    registry = _build_node_registry(
        [
            NodeSpec(id="dummy", kind=f"{__name__}:_DummyNode"),
        ]
    )
    assert isinstance(registry["dummy"], _DummyNode)


@pytest.mark.unit
def test_module_class_with_dotted_path_resolves() -> None:
    """Confirm fully-qualified module paths work (not just top-level)."""
    # EchoNode is at stargraph.nodes.base:EchoNode — works as a sanity check
    registry = _build_node_registry(
        [
            NodeSpec(id="e", kind="stargraph.nodes.base:EchoNode"),
        ]
    )
    assert isinstance(registry["e"], EchoNode)


@pytest.mark.unit
def test_unknown_short_kind_still_raises() -> None:
    with pytest.raises(IRValidationError, match="unknown node kind"):
        _build_node_registry([NodeSpec(id="x", kind="not_a_real_kind")])


@pytest.mark.unit
def test_module_class_import_failure_raises() -> None:
    with pytest.raises(IRValidationError, match="cannot import"):
        _build_node_registry(
            [
                NodeSpec(id="x", kind="stargraph.nonexistent.module:Foo"),
            ]
        )


@pytest.mark.unit
def test_module_class_missing_attribute_raises() -> None:
    with pytest.raises(IRValidationError, match="not found"):
        _build_node_registry(
            [
                NodeSpec(id="x", kind="stargraph.nodes.base:NoSuchClass"),
            ]
        )


@pytest.mark.unit
def test_module_class_not_nodebase_raises() -> None:
    with pytest.raises(IRValidationError, match="not a NodeBase"):
        _build_node_registry(
            [
                NodeSpec(id="x", kind="builtins:dict"),
            ]
        )


@pytest.mark.unit
def test_module_class_config_binds_as_constructor_kwargs() -> None:
    """A non-empty NodeSpec.config maps to constructor kwargs for class kinds."""
    registry = _build_node_registry(
        [
            NodeSpec(
                id="cfg",
                kind=f"{__name__}:_ConfiguredNode",
                config={"greeting": "hello"},
            ),
        ]
    )
    node = registry["cfg"]
    assert isinstance(node, _ConfiguredNode)
    assert node.greeting == "hello"


@pytest.mark.unit
def test_module_class_empty_config_constructs_zero_arg() -> None:
    """Back-compat: class kinds with no config still construct zero-arg."""
    registry = _build_node_registry([NodeSpec(id="z", kind=f"{__name__}:_ConfiguredNode")])
    node = registry["z"]
    assert isinstance(node, _ConfiguredNode)
    assert node.greeting == ""


@pytest.mark.unit
def test_dspy_bare_kind_requires_signature_or_stub() -> None:
    """``kind: dspy`` with no config fails fast (before any dspy import)."""
    with pytest.raises(IRValidationError, match=r"requires config\.signature"):
        _build_node_registry([NodeSpec(id="d", kind="dspy")])


@pytest.mark.unit
def test_dspy_stub_flag_builds_stub_node() -> None:
    """``config: {stub: true}`` is the only route to the offline stub."""
    from stargraph.nodes.registry import (
        _StubDSPyNode,  # pyright: ignore[reportPrivateUsage]
    )

    registry = _build_node_registry([NodeSpec(id="d", kind="dspy", config={"stub": True})])
    assert isinstance(registry["d"], _StubDSPyNode)


@pytest.mark.unit
def test_register_node_kind_extends_short_kind_table() -> None:
    """A registered custom kind resolves like a built-in; duplicates fail loud."""
    kind = "test_custom_kind_p03"

    def _builder(_spec: NodeSpec) -> NodeBase:
        return _DummyNode()

    register_node_kind(kind, _builder)
    try:
        registry = _build_node_registry([NodeSpec(id="c", kind=kind)])
        assert isinstance(registry["c"], _DummyNode)
        with pytest.raises(PluginLoadError, match="already registered"):
            register_node_kind(kind, _builder)
    finally:
        from stargraph.nodes import registry as node_registry_module

        node_registry_module._NODE_FACTORIES.pop(kind, None)  # pyright: ignore[reportPrivateUsage]
