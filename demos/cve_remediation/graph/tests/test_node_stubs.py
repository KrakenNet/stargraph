# SPDX-License-Identifier: Apache-2.0
"""Contract tests for cve_remediation POC stub nodes.

Each stub must:
  - subclass :class:`harbor.nodes.base.NodeBase`
  - construct with zero args (factory contract enforced by
    ``harbor.cli.run._build_node_registry``)
  - return a ``dict`` from ``execute(state, ctx)``
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest
from pydantic import BaseModel

from harbor.nodes.base import NodeBase

from demos.cve_remediation.graph import nodes as cve_nodes
from demos.cve_remediation.graph.state import CveRemState


_STUB_NAMES = [
    "PassthroughStub",
    "ToolStub",
    "BrokerStub",
    "WriteArtifactStub",
    "InterruptStub",
    "MLStub",
    "DSPyStub",
    "SubgraphStub",
]


class _Ctx(BaseModel):
    run_id: str = "test-run"


@pytest.mark.parametrize("name", _STUB_NAMES)
def test_stub_subclasses_nodebase(name: str) -> None:
    cls = getattr(cve_nodes, name)
    assert inspect.isclass(cls)
    assert issubclass(cls, NodeBase), f"{name} must subclass NodeBase"


@pytest.mark.parametrize("name", _STUB_NAMES)
def test_stub_zero_arg_constructor(name: str) -> None:
    cls = getattr(cve_nodes, name)
    instance = cls()
    assert isinstance(instance, NodeBase)


@pytest.mark.parametrize("name", _STUB_NAMES)
def test_stub_execute_returns_dict(name: str) -> None:
    cls = getattr(cve_nodes, name)
    node = cls()
    state = CveRemState(run_id="t-1")
    ctx: Any = _Ctx()
    out = asyncio.run(node.execute(state, ctx))
    assert isinstance(out, dict), f"{name}.execute must return dict"


def test_interrupt_stub_emits_approve_response() -> None:
    """Interrupt stub bypasses durability with a synthesized approve."""
    node = cve_nodes.InterruptStub()
    state = CveRemState(run_id="t-2")
    ctx: Any = _Ctx()
    out = asyncio.run(node.execute(state, ctx))
    assert "response" in out
    assert out["response"].decision == "approve"
    assert out["response"].actor == "cve-rem-stub"


def test_all_promoted_kinds_resolve() -> None:
    """Every promoted IR ``module:ClassName`` reference imports
    successfully and yields a NodeBase subclass with a zero-arg ctor."""
    import importlib

    refs = [
        f"demos.cve_remediation.graph.nodes:{name}" for name in _STUB_NAMES
    ]
    for ref in refs:
        module_path, _, class_name = ref.partition(":")
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        assert issubclass(cls, NodeBase)
        cls()  # zero-arg construct
