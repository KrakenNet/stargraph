# SPDX-License-Identifier: Apache-2.0
"""P0.5 -- in-process builtin tool seeding + plugin->registry install path."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.errors import PluginLoadError
from stargraph.registry.tools import ToolRegistry, install_plugin_tools
from stargraph.tools.builtin import seed_builtin_tools
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects


@pytest.mark.unit
def test_seed_builtin_tools_registers_in_tree_tools() -> None:
    """Every hard-dep in-tree tool lands under its canonical id."""
    reg = seed_builtin_tools(ToolRegistry())
    ids = {f"{t.spec.namespace}.{t.spec.name}@{t.spec.version}" for t in reg.list_tools()}
    # Spot-check one per namespace; exact versions come from the specs.
    assert any(i.startswith("nautilus.broker_request@") for i in ids)
    assert any(i.startswith("servicenow.create_change_request@") for i in ids)
    assert any(i.startswith("servicenow.table_query@") for i in ids)
    # All seeded tools are invocable callables with a spec.
    assert all(callable(t) and t.spec is not None for t in reg.list_tools())


@pytest.mark.unit
def test_seed_builtin_tools_includes_full_std_pack() -> None:
    """The std pack always registers -- extras gate calls, not registration."""
    reg = seed_builtin_tools(ToolRegistry())
    std_names = {t.spec.name for t in reg.list_tools(namespace="std")}
    assert std_names == {
        "calculator",
        "file_read",
        "file_write",
        "file_list",
        "sql_query",
        "http_request",
        "fetch_page",
        "web_search",
        "wikipedia",
        "arxiv",
        "python_exec",
        "shell",
    }
    # The dual-use pair is capability-gated; the rest of the pack is not.
    by_name = {t.spec.name: t.spec for t in reg.list_tools(namespace="std")}
    assert by_name["python_exec"].permissions == ["tools:std:exec"]
    assert by_name["shell"].permissions == ["tools:std:shell"]
    assert by_name["calculator"].permissions == []


@pytest.mark.unit
def test_seed_builtin_tools_conflicts_are_loud() -> None:
    """Seeding the same registry twice duplicates ids -> PluginLoadError."""
    reg = seed_builtin_tools(ToolRegistry())
    with pytest.raises(PluginLoadError, match="already registered"):
        seed_builtin_tools(reg)


class _FakeHookRelay:
    def __init__(self, results: list[list[Any]]) -> None:
        self._results = results

    def register_tools(self) -> list[list[Any]]:
        return self._results


class _FakePM:
    def __init__(self, results: list[list[Any]]) -> None:
        self.hook = _FakeHookRelay(results)


@tool(
    name="plug",
    namespace="plugtest",
    version="1",
    side_effects=SideEffects.none,
    input_schema={"type": "object"},
    output_schema={"type": "object"},
)
def _plug_tool() -> dict[str, Any]:
    return {}


@pytest.mark.unit
def test_install_plugin_tools_registers_callables() -> None:
    reg = ToolRegistry()
    install_plugin_tools(_FakePM([[_plug_tool]]), reg)
    assert [t.spec.name for t in reg.list_tools()] == ["plug"]


@pytest.mark.unit
def test_install_plugin_tools_rejects_bare_specs() -> None:
    """Returning ToolSpec records (old docs shape) fails loudly."""
    reg = ToolRegistry()
    spec: Any = _plug_tool.spec  # pyright: ignore[reportFunctionMemberAccess]
    with pytest.raises(PluginLoadError, match="decorated callables"):
        install_plugin_tools(_FakePM([[spec]]), reg)
