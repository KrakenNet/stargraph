# SPDX-License-Identifier: Apache-2.0
"""Tests for ``harbor.cli.run._build_subgraph`` (S4)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import typer
import yaml

from harbor.cli.run import (
    _build_node_registry,  # pyright: ignore[reportPrivateUsage]
    _build_subgraph,  # pyright: ignore[reportPrivateUsage]
)
from harbor.ir._models import NodeSpec
from harbor.nodes.base import EchoNode
from harbor.nodes.subgraph import SubGraphNode

if TYPE_CHECKING:
    from pathlib import Path


def _write_subir(tmp_path: Path, body: dict[str, object]) -> Path:
    p = tmp_path / "child.yaml"
    p.write_text(yaml.safe_dump(body), encoding="utf-8")
    return p


def test_subgraph_empty_spec_falls_back_to_echo() -> None:
    spec = NodeSpec(id="sub", kind="subgraph")
    node = _build_subgraph(spec)
    assert isinstance(node, EchoNode)


def test_subgraph_resolves_relative_path(tmp_path: Path) -> None:
    sub_path = _write_subir(
        tmp_path,
        {
            "ir_version": "1.0.0",
            "id": "graph:child",
            "nodes": [
                {"id": "n1", "kind": "echo"},
                {"id": "n2", "kind": "passthrough"},
            ],
            "rules": [],
        },
    )
    parent = NodeSpec(id="parent_sub", kind="subgraph", spec="child.yaml")
    registry = _build_node_registry([parent], ir_dir=sub_path.parent)
    built = registry["parent_sub"]
    assert isinstance(built, SubGraphNode)
    assert built.subgraph_id == "parent_sub"


def test_subgraph_absolute_path(tmp_path: Path) -> None:
    sub_path = _write_subir(
        tmp_path,
        {
            "ir_version": "1.0.0",
            "id": "graph:abs",
            "nodes": [{"id": "only", "kind": "echo"}],
            "rules": [],
        },
    )
    parent = NodeSpec(id="abs_parent", kind="subgraph", spec=str(sub_path))
    registry = _build_node_registry([parent], ir_dir=None)
    assert isinstance(registry["abs_parent"], SubGraphNode)


def test_subgraph_missing_file_raises(tmp_path: Path) -> None:
    parent = NodeSpec(id="missing", kind="subgraph", spec="does_not_exist.yaml")
    with pytest.raises(typer.BadParameter, match="child IR not found"):
        _build_node_registry([parent], ir_dir=tmp_path)


def test_subgraph_relative_no_ir_dir_raises() -> None:
    parent = NodeSpec(id="x", kind="subgraph", spec="rel.yaml")
    with pytest.raises(typer.BadParameter, match="no parent IR directory"):
        _build_node_registry([parent], ir_dir=None)


def test_subgraph_nested_resolves_against_child_dir(tmp_path: Path) -> None:
    """Nested sub-graph resolves its own relative spec against its parent's dir."""
    grand_dir = tmp_path / "deep"
    grand_dir.mkdir()
    grand_path = _write_subir(
        grand_dir,
        {
            "ir_version": "1.0.0",
            "id": "graph:grand",
            "nodes": [{"id": "leaf", "kind": "echo"}],
            "rules": [],
        },
    )
    # child.yaml lives in tmp_path and references grand at deep/child.yaml
    child_path = tmp_path / "child.yaml"
    child_path.write_text(
        yaml.safe_dump(
            {
                "ir_version": "1.0.0",
                "id": "graph:child",
                "nodes": [
                    {
                        "id": "nested",
                        "kind": "subgraph",
                        "spec": "deep/child.yaml",
                    }
                ],
                "rules": [],
            }
        ),
        encoding="utf-8",
    )
    parent = NodeSpec(id="root", kind="subgraph", spec="child.yaml")
    registry = _build_node_registry([parent], ir_dir=tmp_path)
    assert isinstance(registry["root"], SubGraphNode)
    assert grand_path.is_file()  # used; sanity check fixture
