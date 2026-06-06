# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``ToolRegistry`` skill registry + capability filter (T06/T07/T08).

Pins:

* ``list_skills()`` returns the shadow store contents in registration order
  (T06: replaces the Phase-1 ``return []`` stub).
* ``search_skills(query)`` does a case-insensitive substring scan over
  ``name`` + ``description`` (T07).
* ``compatible_with(graph)`` mirrors ``graph/loop.py:_check_tool_capabilities``
  semantics: ``graph.capabilities is None`` returns the full tool list; a
  capability mismatch excludes the tool (T08).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import pytest

from stargraph.ir._models import ReplayPolicy, SkillSpec, ToolSpec
from stargraph.registry import ToolRegistry
from stargraph.tools.spec import SideEffects

if TYPE_CHECKING:
    from stargraph.graph import Graph

pytestmark = [pytest.mark.unit, pytest.mark.knowledge]


def _skill(skill_id: str, *, name: str | None = None, description: str | None = None) -> SkillSpec:
    """Build a minimal :class:`SkillSpec` for registry tests.

    SkillSpec (stargraph/ir/_models.py:399) has no standalone ``id`` field
    (IRBase forbids extras); ``skill_id`` parameter populates ``name`` by
    default so test assertions of the form ``s.name == "alpha"`` work.
    The composite registry key is ``f"{namespace}/{name}"`` per
    :meth:`ToolRegistry.register_skill`.
    """
    return SkillSpec(
        name=name or skill_id,
        namespace="reg",
        version="1.0",
        description=description or f"{skill_id} description",
        kind="utility",
    )


def _tool(namespace: str, name: str, *, permissions: list[str] | None = None) -> Any:
    """Synthesize a Tool-protocol-compliant callable with the given spec."""
    spec = ToolSpec(
        name=name,
        namespace=namespace,
        version="1.0.0",
        description=f"{namespace}.{name}",
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


# ---------------------------------------------------------------------------
# T06: list_skills
# ---------------------------------------------------------------------------


def test_list_skills_empty_registry_returns_empty_list() -> None:
    """A fresh ``ToolRegistry`` has no skills."""
    reg = ToolRegistry()
    assert reg.list_skills() == []


def test_list_skills_returns_registered_specs_in_order() -> None:
    """``list_skills()`` returns skills in registration order (T06)."""
    reg = ToolRegistry()
    s1 = _skill("alpha")
    s2 = _skill("beta")
    s3 = _skill("gamma")
    reg.register_skill(s1)
    reg.register_skill(s2)
    reg.register_skill(s3)
    assert reg.list_skills() == [s1, s2, s3]


# ---------------------------------------------------------------------------
# T07: search_skills
# ---------------------------------------------------------------------------


def test_search_skills_substring_name_match() -> None:
    """Substring of ``spec.name`` matches case-insensitively (T07)."""
    reg = ToolRegistry()
    reg.register_skill(_skill("rag", name="RagSkill"))
    reg.register_skill(_skill("auto", name="AutoresearchSkill"))
    out = reg.search_skills("rag")
    assert [s.name for s in out] == ["RagSkill"]


def test_search_skills_substring_description_match() -> None:
    """Substring of ``spec.description`` matches (T07)."""
    reg = ToolRegistry()
    reg.register_skill(_skill("a", name="A", description="retrieval augmented"))
    reg.register_skill(_skill("b", name="B", description="autoresearch loop"))
    out = reg.search_skills("autoresearch")
    assert [s.name for s in out] == ["B"]


def test_search_skills_case_insensitive() -> None:
    """Query is lower-cased before substring comparison (T07)."""
    reg = ToolRegistry()
    reg.register_skill(_skill("a", name="RagSkill"))
    out = reg.search_skills("RAGSKILL")
    assert [s.name for s in out] == ["RagSkill"]


def test_search_skills_empty_query_returns_all() -> None:
    """Empty / whitespace-only query returns ``list_skills()`` result (T07)."""
    reg = ToolRegistry()
    reg.register_skill(_skill("a"))
    reg.register_skill(_skill("b"))
    assert reg.search_skills("") == reg.list_skills()
    assert reg.search_skills("   ") == reg.list_skills()


def test_search_skills_unknown_query_returns_empty() -> None:
    """A query that matches nothing returns ``[]`` (T07)."""
    reg = ToolRegistry()
    reg.register_skill(_skill("a", name="Alpha", description="alpha"))
    assert reg.search_skills("zzz") == []


# ---------------------------------------------------------------------------
# T08: compatible_with
# ---------------------------------------------------------------------------


def test_compatible_with_no_capabilities_returns_all_tools() -> None:
    """``graph.capabilities is None`` returns the full tool list (T08)."""
    reg = ToolRegistry()
    t1 = _tool("alpha", "search", permissions=["net.read"])
    t2 = _tool("beta", "write", permissions=["fs.write"])
    reg.register(t1)
    reg.register(t2)

    class _GraphNoCaps:
        capabilities = None

    out = reg.compatible_with(cast("Graph", _GraphNoCaps()))
    assert out == [t1, t2]


def test_compatible_with_filters_disallowed_tools() -> None:
    """A tool requiring a capability not granted by ``graph.capabilities`` is
    excluded from ``compatible_with``'s return (T08).

    Mirrors ``graph/loop.py:_check_tool_capabilities`` semantics. The
    capabilities object exposes a ``check(spec)`` method that raises on
    deny; tools that pass the check are included.
    """
    reg = ToolRegistry()
    t_allowed = _tool("a", "allowed", permissions=["net.read"])
    t_denied = _tool("b", "denied", permissions=["fs.write"])
    reg.register(t_allowed)
    reg.register(t_denied)

    class _Caps:
        def check(self, spec: ToolSpec) -> None:
            if "fs.write" in spec.permissions:
                from stargraph.errors import CapabilityError

                raise CapabilityError(
                    f"capability not granted for {spec.name}",
                    permission="fs.write",
                )

    class _Graph:
        capabilities = _Caps()

    out = reg.compatible_with(cast("Graph", _Graph()))
    assert out == [t_allowed]
