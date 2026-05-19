# SPDX-License-Identifier: Apache-2.0
"""ToolRegistry -- in-memory tool/skill registry (FR-23, design §3.5).

Open Q5 resolution: **in-memory dict for v1; SQLite-backed cache deferred.**

The registry is populated at plugin-load time via the foundation's pluggy
hooks (``register_tools``, ``register_skills`` in
:mod:`harbor.plugin.hookspecs`). The two-stage discipline from research
§1.4 is preserved by the foundation loader: pluggy manifest pre-validation
runs *before* ``pm.register()``; engine surfaces loud failures via
:class:`harbor.errors.PluginLoadError` per AC-5.5.

Tool identity follows design §3.5: ``f"{namespace}.{name}@{version}"`` is
the registry key. The ``Tool`` Protocol uses structural typing because the
:func:`harbor.tools.tool` decorator returns the original callable with
``.spec`` attached (not a wrapper class) -- structural matching is the
correct shape for that surface.

Phase 1 scope:

- ``register`` / ``list_tools`` / ``get_tool`` are real (the POC needs them).
- ``list_skills`` / ``search_skills`` / ``compatible_with`` are stubs that
  return trivial values; full implementations land alongside the skill
  registry / capability filter work in Phase 3 (tasks 3.13+).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from harbor.errors import CapabilityError, PluginLoadError

if TYPE_CHECKING:
    from harbor.graph import Graph
    from harbor.ir._models import SkillSpec, ToolSpec

__all__ = ["Tool", "ToolRegistry"]


@runtime_checkable
class Tool(Protocol):
    """Structural type for a registered tool.

    The :func:`harbor.tools.tool` decorator returns the original callable
    with a ``.spec: ToolSpec`` attribute attached via ``setattr`` -- so any
    callable with a ``spec`` attribute satisfies this Protocol. Concrete
    classes are not required.
    """

    spec: ToolSpec

    def __call__(self, *args: Any, **kwargs: Any) -> Any: ...


def _tool_id(spec: ToolSpec) -> str:
    """Compose the registry key from a ToolSpec per design §3.5.

    Format: ``f"{namespace}.{name}@{version}"``. Encoded as a free function
    so callers (CLI, tests) can construct the same key without importing
    the registry class.
    """
    return f"{spec.namespace}.{spec.name}@{spec.version}"


class ToolRegistry:
    """In-memory tool registry (design §3.5).

    Two indices:

    - ``_by_id`` maps the canonical tool id (``namespace.name@version``)
      to the :class:`Tool` callable.
    - ``_by_namespace`` maps each namespace to the list of tools in it
      (insertion order, useful for ``list_tools(namespace=...)``).

    Both are populated atomically by :meth:`register`; a duplicate id is
    a fatal :class:`PluginLoadError` (AC-5.5).
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Tool] = {}
        self._by_namespace: dict[str, list[Tool]] = defaultdict(list)
        # Scaffold stub -- task T06 (skill shadow store; mirrors ``_by_id``
        # for tools). Real ``register_skill`` / ``list_skills`` consumers
        # land in Ralph-Loop T06/T07.
        self._by_id_skill: dict[str, SkillSpec] = {}

    def register_skill(self, spec: SkillSpec) -> None:
        """Register a :class:`SkillSpec` in the skill shadow store.

        Key is ``f"{spec.namespace}/{spec.name}"`` -- SkillSpec has no
        standalone ``id`` field (IRBase forbids extras), so the composite
        identity is synthesized at registration time. Mirrors
        :func:`_tool_id` for tools.
        """
        self._by_id_skill[f"{spec.namespace}/{spec.name}"] = spec

    def register(self, tool: Tool) -> None:
        """Register a tool. Raises :class:`PluginLoadError` on id conflict.

        Conflict semantics match the foundation pluggy loader: first
        registration wins; duplicates are loud. The conflict message
        carries both ``tool_id`` and ``namespace`` keys in the structured
        ``context`` dict so log scrapers and CLI ``harbor inspect`` can
        render a useful diagnostic without re-parsing the message.
        """
        spec = tool.spec
        tool_id = _tool_id(spec)
        if tool_id in self._by_id:
            raise PluginLoadError(
                f"tool id {tool_id!r} already registered (namespace conflict)",
                tool_id=tool_id,
                namespace=spec.namespace,
            )
        self._by_id[tool_id] = tool
        self._by_namespace[spec.namespace].append(tool)

    def list_tools(self, namespace: str | None = None) -> list[Tool]:
        """List registered tools, optionally filtered by namespace.

        Returns a fresh list each call (callers may mutate it without
        corrupting the registry). When ``namespace`` is unknown, returns
        ``[]`` rather than raising -- "no tools in that namespace" is a
        valid empty-result query, not an error.
        """
        if namespace is None:
            return list(self._by_id.values())
        return list(self._by_namespace.get(namespace, []))

    def get_tool(self, id: str) -> Tool:  # noqa: A002 -- "id" matches design §3.5 sig
        """Look up a tool by its canonical id.

        Raises :class:`PluginLoadError` (not :class:`KeyError`) so the
        engine's loud-failure contract is honored: a missing tool at
        runtime is a plugin-loading bug, not a programming oversight.
        """
        try:
            return self._by_id[id]
        except KeyError as exc:
            raise PluginLoadError(
                f"tool id {id!r} not found in registry",
                tool_id=id,
            ) from exc

    def list_skills(self) -> list[SkillSpec]:
        """List registered skills in registration order."""
        return list(self._by_id_skill.values())

    def search_skills(self, query: str) -> list[SkillSpec]:
        """Case-insensitive substring scan over registered skills.

        Matches when ``query.lower()`` is a substring of ``spec.name`` or
        ``spec.description`` (both lowered). Empty / whitespace-only query
        returns the full list (mirrors ``list_skills`` ordering).
        """
        q = query.strip().lower()
        if not q:
            return self.list_skills()
        return [
            spec
            for spec in self._by_id_skill.values()
            if q in spec.name.lower() or q in spec.description.lower()
        ]

    def compatible_with(self, graph: Graph) -> list[Tool]:
        """List tools whose capability requirements are satisfied by ``graph``.

        Mirrors :func:`harbor.graph.loop._check_tool_capabilities` semantics:
        ``graph.capabilities is None`` returns the full tool list (no gate);
        otherwise ``capabilities.check(spec)`` is invoked per tool and a
        raised :class:`CapabilityError` excludes the tool. Order is the
        underlying ``_by_id`` insertion order.
        """
        caps = getattr(graph, "capabilities", None)
        if caps is None:
            return self.list_tools()
        out: list[Tool] = []
        for tool in self.list_tools():
            try:
                caps.check(tool.spec)
            except CapabilityError:
                continue
            out.append(tool)
        return out
