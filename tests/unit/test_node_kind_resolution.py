# SPDX-License-Identifier: Apache-2.0
"""Tests for module:Class node kind resolution in stargraph run."""

from __future__ import annotations

from typing import Any

import pytest
import typer

from stargraph.cli.run import _build_node_registry  # pyright: ignore[reportPrivateUsage]
from stargraph.ir._models import NodeSpec
from stargraph.nodes.base import EchoNode, NodeBase


# A minimal NodeBase subclass exposed for the import-resolution test.
class _DummyNode(NodeBase):
    async def execute(self, state: Any, ctx: Any) -> dict[str, Any]:
        return {}


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
    with pytest.raises(typer.BadParameter, match="unknown node kind"):
        _build_node_registry([NodeSpec(id="x", kind="not_a_real_kind")])


@pytest.mark.unit
def test_module_class_import_failure_raises_typer_error() -> None:
    with pytest.raises(typer.BadParameter, match="cannot import"):
        _build_node_registry(
            [
                NodeSpec(id="x", kind="stargraph.nonexistent.module:Foo"),
            ]
        )


@pytest.mark.unit
def test_module_class_missing_attribute_raises() -> None:
    with pytest.raises(typer.BadParameter, match="not found"):
        _build_node_registry(
            [
                NodeSpec(id="x", kind="stargraph.nodes.base:NoSuchClass"),
            ]
        )


@pytest.mark.unit
def test_module_class_not_nodebase_raises() -> None:
    with pytest.raises(typer.BadParameter, match="not a NodeBase"):
        _build_node_registry(
            [
                NodeSpec(id="x", kind="builtins:dict"),
            ]
        )
