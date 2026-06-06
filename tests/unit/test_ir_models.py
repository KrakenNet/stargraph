# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.ir._models` (FR-6, FR-11, AC-9.1).

Covers:

* ``IRBase`` enforces ``extra='forbid'`` on every IR Pydantic type (FR-6, AC-9.1).
* The :data:`Action` discriminated union dispatches by ``kind`` (FR-11) and
  rejects unknown / missing tags with the ``union_tag_*`` Pydantic codes the
  ``_HINTS`` table maps to friendly hints.
* :class:`IRDocument` requires ``ir_version``, ``id``, ``nodes`` and accepts
  defaulted optional sections.
* :class:`ToolSpec` accepts ``Decimal`` (not ``float``) for ``cost_estimate``
  per FR-9.
* :class:`PluginManifest` ``order`` is bounded ``[0, 10000]`` (D1 design
  decision) and ``api_version`` is pinned to ``"1"``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from stargraph.ir._models import (
    Action,
    AssertAction,
    FactTemplate,
    GotoAction,
    HaltAction,
    IRBase,
    IRDocument,
    NodeSpec,
    ParallelAction,
    PluginManifest,
    RetractAction,
    RetryAction,
    RuleSpec,
    SkillSpec,
    SlotDef,
    ToolSpec,
)
from stargraph.tools.spec import SideEffects

_ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)


# ---------------------------------------------------------------------------
# IRBase: extra='forbid' contract (FR-6, AC-9.1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_irbase_forbids_extra_keys_on_simple_subclass() -> None:
    """``IRBase`` rejects unknown keys on every subclass (NodeSpec sample)."""
    with pytest.raises(ValidationError) as excinfo:
        NodeSpec.model_validate({"id": "n1", "kind": "task", "extra": "nope"})
    errs = excinfo.value.errors()
    assert any(e["type"] == "extra_forbidden" for e in errs)


@pytest.mark.unit
def test_irbase_forbids_extra_keys_on_irdocument() -> None:
    """Top-level ``IRDocument`` rejects unknown sections (forward-compat is explicit)."""
    with pytest.raises(ValidationError) as excinfo:
        IRDocument.model_validate(
            {"ir_version": "1.0.0", "id": "run:t", "nodes": [], "future_section": []},
        )
    assert any(e["type"] == "extra_forbidden" for e in excinfo.value.errors())


@pytest.mark.unit
def test_irbase_subclass_inherits_forbid_config() -> None:
    """A custom ``IRBase`` subclass inherits ``extra='forbid'`` automatically."""

    class Custom(IRBase):
        x: int

    with pytest.raises(ValidationError):
        Custom.model_validate({"x": 1, "y": 2})


# ---------------------------------------------------------------------------
# Action discriminated union (FR-11)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "expected_cls"),
    [
        ({"kind": "goto", "target": "n2"}, GotoAction),
        ({"kind": "halt", "reason": "done"}, HaltAction),
        ({"kind": "parallel", "targets": ["a", "b"]}, ParallelAction),
        ({"kind": "retry", "target": "n1", "backoff_ms": 100}, RetryAction),
        ({"kind": "assert", "fact": "f"}, AssertAction),
        ({"kind": "retract", "pattern": "p"}, RetractAction),
    ],
)
def test_action_dispatches_by_kind(payload: dict[str, Any], expected_cls: type[IRBase]) -> None:
    """Every one of the six FR-11 verbs round-trips through the discriminated union."""
    obj = _ACTION_ADAPTER.validate_python(payload)
    assert isinstance(obj, expected_cls)


@pytest.mark.unit
def test_action_unknown_kind_emits_union_tag_invalid() -> None:
    """Unknown ``kind`` triggers ``union_tag_invalid`` (mapped to a hint by ``_HINTS``)."""
    with pytest.raises(ValidationError) as excinfo:
        _ACTION_ADAPTER.validate_python({"kind": "wat", "target": "x"})
    errs = excinfo.value.errors()
    assert errs[0]["type"] == "union_tag_invalid"


@pytest.mark.unit
def test_action_missing_kind_emits_union_tag_not_found() -> None:
    """Missing ``kind`` triggers ``union_tag_not_found`` (also in the ``_HINTS`` table)."""
    with pytest.raises(ValidationError) as excinfo:
        _ACTION_ADAPTER.validate_python({"target": "x"})
    errs = excinfo.value.errors()
    assert errs[0]["type"] == "union_tag_not_found"


@pytest.mark.unit
def test_rule_spec_then_accepts_mixed_action_variants() -> None:
    """A single ``RuleSpec.then`` may mix multiple Action variants (top-level only)."""
    rule = RuleSpec.model_validate(
        {
            "id": "r1",
            "when": "?x <- (foo)",
            "then": [
                {"kind": "goto", "target": "n2"},
                {"kind": "halt", "reason": ""},
                {"kind": "retract", "pattern": "stale"},
            ],
        },
    )
    assert [type(a).__name__ for a in rule.then] == ["GotoAction", "HaltAction", "RetractAction"]


# ---------------------------------------------------------------------------
# IRDocument required + optional fields
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_irdocument_minimum_required_fields() -> None:
    """``IRDocument(ir_version, id, nodes=[])`` constructs with empty optional sections."""
    doc = IRDocument(ir_version="1.0.0", id="run:t", nodes=[])
    assert doc.rules == [] and doc.tools == [] and doc.skills == []
    assert doc.stores == [] and doc.parallel == []
    assert doc.state_schema == {}
    assert doc.governance == [] and doc.migrate == []


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["ir_version", "id", "nodes"])
def test_irdocument_missing_required_field(missing: str) -> None:
    """Each of ``ir_version`` / ``id`` / ``nodes`` is required at the top level."""
    payload: dict[str, Any] = {"ir_version": "1.0.0", "id": "run:t", "nodes": []}
    payload.pop(missing)
    with pytest.raises(ValidationError) as excinfo:
        IRDocument.model_validate(payload)
    errs = excinfo.value.errors()
    assert any(e["type"] == "missing" and missing in e["loc"] for e in errs)


# ---------------------------------------------------------------------------
# Sub-models (SlotDef, FactTemplate)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_fact_template_with_typed_slots_round_trips() -> None:
    """:class:`FactTemplate` carries a list of :class:`SlotDef` with default ``None``."""
    tpl = FactTemplate(
        name="evidence",
        slots=[SlotDef(name="kind", type="STRING"), SlotDef(name="step", type="INT", default="0")],
    )
    assert tpl.slots[0].default is None
    assert tpl.slots[1].default == "0"


# ---------------------------------------------------------------------------
# ToolSpec / SkillSpec / PluginManifest
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_toolspec_cost_estimate_is_decimal_not_float() -> None:
    """:class:`ToolSpec.cost_estimate` is ``Decimal | None`` per FR-9 (no float)."""
    spec = ToolSpec(
        name="search",
        namespace="t",
        version="1.0.0",
        description="d",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effects=SideEffects.none,
        cost_estimate=Decimal("0.0125"),
    )
    assert isinstance(spec.cost_estimate, Decimal)
    # JSON-string input is also accepted (Pydantic Decimal coercion).
    spec2 = ToolSpec.model_validate(
        {
            "name": "x",
            "namespace": "t",
            "version": "1.0.0",
            "description": "d",
            "input_schema": {},
            "output_schema": {},
            "side_effects": "write",
            "cost_estimate": "0.5",
        },
    )
    assert spec2.cost_estimate == Decimal("0.5")


@pytest.mark.unit
def test_skillspec_kind_literal_is_constrained() -> None:
    """:class:`SkillSpec.kind` is ``Literal['agent','workflow','utility']`` only."""
    SkillSpec(
        name="s",
        namespace="ns",
        version="1.0.0",
        description="d",
        kind="agent",
    )
    with pytest.raises(ValidationError):
        SkillSpec.model_validate(
            {
                "name": "s",
                "namespace": "ns",
                "version": "1.0.0",
                "description": "d",
                "kind": "service",  # not a valid Literal
            },
        )


@pytest.mark.unit
def test_pluginmanifest_order_bounded_zero_to_ten_thousand() -> None:
    """``PluginManifest.order`` is bounded ``[0, 10000]`` (D1 design decision)."""
    PluginManifest(
        name="p",
        version="1.0.0",
        api_version="1",
        namespaces=["ns"],
        provides=["tool"],
        order=0,
    )
    PluginManifest(
        name="p",
        version="1.0.0",
        api_version="1",
        namespaces=["ns"],
        provides=["tool"],
        order=10000,
    )
    with pytest.raises(ValidationError):
        PluginManifest(
            name="p",
            version="1.0.0",
            api_version="1",
            namespaces=["ns"],
            provides=["tool"],
            order=-1,
        )
    with pytest.raises(ValidationError):
        PluginManifest(
            name="p",
            version="1.0.0",
            api_version="1",
            namespaces=["ns"],
            provides=["tool"],
            order=10001,
        )


@pytest.mark.unit
def test_pluginmanifest_api_version_pinned_to_one() -> None:
    """``api_version`` is pinned to ``"1"``; bumps must be explicit (FR-6)."""
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(
            {
                "name": "p",
                "version": "1.0.0",
                "api_version": "2",
                "namespaces": ["ns"],
                "provides": ["tool"],
            },
        )


@pytest.mark.unit
def test_pluginmanifest_provides_constrained_to_known_kinds() -> None:
    """``provides`` accepts only ``tool``/``skill``/``store``/``pack``."""
    PluginManifest(
        name="p",
        version="1.0.0",
        api_version="1",
        namespaces=["ns"],
        provides=["tool", "skill", "store", "pack"],
        order=5000,
    )
    with pytest.raises(ValidationError):
        PluginManifest.model_validate(
            {
                "name": "p",
                "version": "1.0.0",
                "api_version": "1",
                "namespaces": ["ns"],
                "provides": ["tool", "agent"],  # 'agent' not in Literal
            },
        )
