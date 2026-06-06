# SPDX-License-Identifier: Apache-2.0
"""Foundation v0.1 -> engine v0.2 IR migration test (FR-33).

Pins the four migration guarantees promised by FR-33:

1. The foundation-extension unit tests still pass against the v0.2
   surface (the contract from ``tests/unit/test_foundation_extensions.py``
   is re-exercised here in a focused subset to fail loud if a future
   refactor breaks the foundation-frozen surface area).
2. IR fragments authored against v0.1 (``side_effects: bool``) load via
   :func:`stargraph.ir._migrate.coerce_legacy_tool_spec` with a
   :class:`DeprecationWarning`.
3. Legacy IR with no ``replay_policy`` field defaults to
   :data:`stargraph.tools.spec.ReplayPolicy.must_stub` after migration.
4. Existing fixtures under ``tests/fixtures/*.yaml`` (the engine's POC
   sample graph and the FR-32 training-subgraph reference) still parse
   under the v0.2 schema.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest
import yaml

from stargraph.ir._migrate import coerce_legacy_tool_spec
from stargraph.ir._models import IRDocument, ToolSpec
from stargraph.tools.spec import ReplayPolicy, SideEffects

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


@pytest.mark.unit
def test_foundation_extension_contract_holds_on_v0_2_surface() -> None:
    """Re-run the v0.1 foundation-extension contract against the v0.2 surface.

    Mirrors the load-bearing assertions from
    ``tests/unit/test_foundation_extensions.py`` so any future surface
    drift surfaces here as a migration regression, not just a unit-test
    failure.
    """
    # SideEffects: four members, lowercase string values, str-enum compat.
    assert {m.name for m in SideEffects} == {"none", "read", "write", "external"}
    assert SideEffects.write == "write"

    # ReplayPolicy: three members, kebab-cased values, str-enum compat.
    assert ReplayPolicy.must_stub == "must-stub"
    assert ReplayPolicy.fail_loud == "fail-loud"
    assert ReplayPolicy.recorded_result == "recorded-result"

    # ToolSpec accepts the enum and defaults replay_policy to must_stub.
    spec = ToolSpec(
        name="search",
        namespace="t",
        version="1.0.0",
        description="d",
        input_schema={},
        output_schema={},
        side_effects=SideEffects.none,
    )
    assert spec.side_effects is SideEffects.none
    assert spec.replay_policy is ReplayPolicy.must_stub


@pytest.mark.unit
def test_legacy_bool_side_effects_loads_with_deprecation_warning() -> None:
    """v0.1 IR with ``side_effects: True`` migrates to ``SideEffects.write``."""
    legacy: dict[str, Any] = {
        "name": "search",
        "namespace": "t",
        "version": "1.0.0",
        "description": "d",
        "input_schema": {},
        "output_schema": {},
        "side_effects": True,  # v0.1 bool placeholder
    }

    with pytest.warns(DeprecationWarning, match="foundation v0.1"):
        migrated = coerce_legacy_tool_spec(legacy)

    spec = ToolSpec.model_validate(migrated)
    assert spec.side_effects is SideEffects.write

    # And the False -> none mapping (also warns):
    legacy_false = {**legacy, "side_effects": False}
    with pytest.warns(DeprecationWarning):
        spec_false = ToolSpec.model_validate(coerce_legacy_tool_spec(legacy_false))
    assert spec_false.side_effects is SideEffects.none


@pytest.mark.unit
def test_legacy_ir_defaults_replay_policy_to_must_stub() -> None:
    """Legacy IR (no ``replay_policy``) defaults to ``ReplayPolicy.must_stub``."""
    legacy: dict[str, Any] = {
        "name": "search",
        "namespace": "t",
        "version": "1.0.0",
        "description": "d",
        "input_schema": {},
        "output_schema": {},
        "side_effects": True,
        # no replay_policy -- v0.1 didn't have the field
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        spec = ToolSpec.model_validate(coerce_legacy_tool_spec(legacy))

    assert spec.replay_policy is ReplayPolicy.must_stub
    assert "replay_policy" in ToolSpec.model_fields
    assert ToolSpec.model_fields["replay_policy"].default is ReplayPolicy.must_stub


@pytest.mark.unit
def test_existing_fixtures_still_parse_under_v0_2() -> None:
    """``tests/fixtures/*.yaml`` parse under the v0.2 IR loader."""
    sample = yaml.safe_load((_FIXTURES / "sample-graph.yaml").read_text(encoding="utf-8"))
    doc = IRDocument.model_validate(sample)
    assert doc.id == "run:sample-graph"
    assert {n.id for n in doc.nodes} == {"node_a", "node_b"}

    # training-subgraph carries node ``spec`` blocks beyond IRDocument's
    # POC NodeSpec; per the FR-32 reference test it parses as a raw dict.
    # We assert the dict shape and that no ToolSpec-level v0.1 placeholders
    # leaked into the fixture (no top-level bool side_effects values).
    raw = yaml.safe_load(
        (_FIXTURES / "training-subgraph.yaml").read_text(encoding="utf-8"),
    )
    assert raw["ir_version"] == "1.0.0"
    assert raw["id"] == "run:training-subgraph"
    for node in raw["nodes"]:
        spec = node.get("spec", {})
        # No legacy bool side_effects in the fixture itself -- v0.2 enum only.
        assert not isinstance(spec.get("side_effects"), bool)
