# SPDX-License-Identifier: Apache-2.0
"""``ToolRegistry.compatible_with(graph)`` Phase-1 contract (FR-23, AC-3).

Phase-1 status: :meth:`ToolRegistry.compatible_with` is intentionally a
no-op pass-through that returns every registered tool without inspecting
graph capabilities. The capability-driven filter lands with the
security/capabilities wiring in Phase 3 (task 3.13+); when that work
arrives this test should be rewritten with real allow/deny assertions
against ``ToolSpec.permissions`` vs ``graph.capabilities``.

Until then we pin two invariants here:

1. :class:`ToolRegistry` exists and exposes a callable ``compatible_with``
   method.
2. The Phase-1 stub returns every registered tool unchanged (insertion
   order preserved), regardless of any graph argument.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from stargraph.ir._models import ReplayPolicy, ToolSpec
from stargraph.registry import ToolRegistry
from stargraph.tools.spec import SideEffects

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def _make_tool(namespace: str, name: str, *, permissions: list[str] | None = None) -> Any:
    """Synthesize a Tool-protocol-compliant callable with the given spec."""
    spec = ToolSpec(
        name=name,
        namespace=namespace,
        version="1.0.0",
        description=f"{namespace}.{name} (test fixture)",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effects=SideEffects.none,
        replay_policy=ReplayPolicy.must_stub,
        permissions=permissions or [],
        idempotency_key=None,
        cost_estimate=Decimal("0"),
    )

    def _impl(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": True}

    _impl.spec = spec  # type: ignore[attr-defined]
    return _impl


def test_compatible_with_surface_exists() -> None:
    """Phase-1 precondition: registry exposes a callable ``compatible_with``."""
    reg = ToolRegistry()
    assert hasattr(reg, "compatible_with")
    assert callable(reg.compatible_with)


def test_compatible_with_returns_all_tools_phase1_stub() -> None:
    """Phase-1 stub returns every registered tool, in insertion order.

    Replace this assertion with the real capability filter once Phase 3
    (task 3.13) lands the security/capabilities wiring.
    """
    reg = ToolRegistry()
    t_alpha = _make_tool("alpha", "search", permissions=["net.read"])
    t_beta = _make_tool("beta", "write", permissions=["fs.write"])
    t_gamma = _make_tool("gamma", "noperm", permissions=[])
    reg.register(t_alpha)
    reg.register(t_beta)
    reg.register(t_gamma)

    # Pass any object as the graph stand-in; the Phase-1 stub does not
    # inspect it. Once Phase 3 lands a real graph fixture replaces this.
    sentinel_graph: Any = object()
    out = reg.compatible_with(sentinel_graph)

    assert out == [t_alpha, t_beta, t_gamma], (
        f"Phase-1 contract requires identity pass-through; got {out!r}"
    )


def test_compatible_with_empty_registry_returns_empty_list() -> None:
    """Empty registry returns ``[]`` regardless of graph capabilities."""
    reg = ToolRegistry()
    sentinel_graph: Any = object()
    assert reg.compatible_with(sentinel_graph) == []
