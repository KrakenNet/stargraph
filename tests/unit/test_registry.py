# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.registry` (FR-23, design §3.5).

Covers the in-memory :class:`ToolRegistry` surface:

* ``list_tools`` returns all registered tools, optionally namespace-filtered.
* ``get_tool`` retrieves by canonical id and raises
  :class:`PluginLoadError` (not :class:`KeyError`) on miss -- the
  loud-failure contract from AC-5.5.
* ``search_skills`` and ``compatible_with`` honor their Phase-1 stub
  contracts (empty list / all tools, respectively).
* Duplicate-id registration raises :class:`PluginLoadError` with both
  ``tool_id`` and ``namespace`` populated in ``context``.
"""

from __future__ import annotations

import pytest

from stargraph.errors import PluginLoadError
from stargraph.registry import Tool, ToolRegistry
from stargraph.tools import ReplayPolicy, SideEffects, tool


def _make_tool(name: str, namespace: str = "fs", version: str = "1.0.0") -> Tool:
    """Construct a registered :class:`Tool` via the public ``@tool`` decorator."""

    @tool(
        name=name,
        namespace=namespace,
        version=version,
        side_effects=SideEffects.none,
        replay_policy=ReplayPolicy.recorded_result,
    )
    def _fn(x: int) -> int:
        return x

    return _fn  # type: ignore[return-value]  -- decorator attaches .spec


@pytest.mark.unit
def test_list_tools_returns_all_when_namespace_omitted() -> None:
    reg = ToolRegistry()
    a = _make_tool("read", namespace="fs")
    b = _make_tool("fetch", namespace="net")
    reg.register(a)
    reg.register(b)

    tools = reg.list_tools()
    assert len(tools) == 2
    assert a in tools
    assert b in tools


@pytest.mark.unit
def test_list_tools_filters_by_namespace() -> None:
    reg = ToolRegistry()
    a = _make_tool("read", namespace="fs")
    b = _make_tool("write", namespace="fs")
    c = _make_tool("fetch", namespace="net")
    for t in (a, b, c):
        reg.register(t)

    fs_tools = reg.list_tools(namespace="fs")
    assert {t.spec.name for t in fs_tools} == {"read", "write"}

    net_tools = reg.list_tools(namespace="net")
    assert [t.spec.name for t in net_tools] == ["fetch"]


@pytest.mark.unit
def test_list_tools_unknown_namespace_returns_empty() -> None:
    reg = ToolRegistry()
    reg.register(_make_tool("read", namespace="fs"))
    assert reg.list_tools(namespace="missing") == []


@pytest.mark.unit
def test_get_tool_returns_registered() -> None:
    reg = ToolRegistry()
    t = _make_tool("read", namespace="fs", version="1.0.0")
    reg.register(t)

    fetched = reg.get_tool("fs.read@1.0.0")
    assert fetched is t


@pytest.mark.unit
def test_get_tool_unknown_raises_plugin_load_error() -> None:
    reg = ToolRegistry()
    with pytest.raises(PluginLoadError) as ei:
        reg.get_tool("fs.missing@1.0.0")
    assert ei.value.context.get("tool_id") == "fs.missing@1.0.0"


@pytest.mark.unit
def test_register_duplicate_id_raises_plugin_load_error() -> None:
    reg = ToolRegistry()
    a = _make_tool("read", namespace="fs", version="1.0.0")
    b = _make_tool("read", namespace="fs", version="1.0.0")
    reg.register(a)

    with pytest.raises(PluginLoadError) as ei:
        reg.register(b)
    assert ei.value.context.get("tool_id") == "fs.read@1.0.0"
    assert ei.value.context.get("namespace") == "fs"


@pytest.mark.unit
def test_search_skills_phase1_stub_returns_empty() -> None:
    reg = ToolRegistry()
    assert reg.search_skills("anything") == []


@pytest.mark.unit
def test_compatible_with_phase1_stub_returns_all_tools() -> None:
    reg = ToolRegistry()
    a = _make_tool("read", namespace="fs")
    b = _make_tool("fetch", namespace="net")
    reg.register(a)
    reg.register(b)

    # Phase-1 stub ignores the graph argument and returns every registered tool.
    compatible = reg.compatible_with(graph=None)  # type: ignore[arg-type]
    assert set(compatible) == {a, b}
