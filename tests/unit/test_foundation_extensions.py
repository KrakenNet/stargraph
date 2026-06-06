# SPDX-License-Identifier: Apache-2.0
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportAttributeAccessIssue=false
"""TDD-RED tests for FR-33 foundation extensions (design §3.4.1, §3.4.2).

These tests pin the contract for the engine's in-place foundation extensions:

* ``stargraph.tools.spec.SideEffects`` -- ``str`` enum with members
  ``none|read|write|external`` whose values are the lowercase names.
* ``stargraph.tools.spec.ReplayPolicy`` -- ``str`` enum with members
  ``must_stub|fail_loud|recorded_result`` whose values are kebab-cased
  (``"must-stub"`` / ``"fail-loud"`` / ``"recorded-result"``).
* :class:`stargraph.ir._models.ToolSpec` is extended (FR-33) so that
  ``side_effects`` accepts the :class:`SideEffects` enum (was ``bool``)
  and a new ``replay_policy`` field defaults to ``ReplayPolicy.must_stub``.

This is the RED side of the FR-33 TDD cycle. ``stargraph.tools.spec`` does
not exist yet (task 1.4 lands the GREEN implementation), so each test is
expected to raise ``ImportError`` when invoked. The imports happen inside
each test body so the module itself loads cleanly under ruff + pyright.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.mark.unit
def test_side_effects_enum_exists() -> None:
    """``SideEffects`` is a ``str`` enum with the four documented members."""
    from stargraph.tools.spec import SideEffects  # type: ignore[import-not-found]

    # Members exist with lowercase string values.
    assert SideEffects.none.value == "none"
    assert SideEffects.read.value == "read"
    assert SideEffects.write.value == "write"
    assert SideEffects.external.value == "external"

    # Enum is str-typed so values compare equal to their strings (FR-33 wire
    # compatibility -- IR YAML/JSON carries plain strings).
    assert SideEffects.write == "write"

    # No surprise members.
    assert {m.name for m in SideEffects} == {"none", "read", "write", "external"}


@pytest.mark.unit
def test_replay_policy_enum_exists() -> None:
    """``ReplayPolicy`` is a ``str`` enum with kebab-cased values per design §3.4.2."""
    from stargraph.tools.spec import ReplayPolicy  # type: ignore[import-not-found]

    # Names use Python snake_case; values are kebab-cased per design §3.4.2.
    assert ReplayPolicy.must_stub.value == "must-stub"
    assert ReplayPolicy.fail_loud.value == "fail-loud"
    assert ReplayPolicy.recorded_result.value == "recorded-result"

    # str-enum compatibility for IR wire format.
    assert ReplayPolicy.must_stub == "must-stub"

    # No surprise members.
    assert {m.name for m in ReplayPolicy} == {
        "must_stub",
        "fail_loud",
        "recorded_result",
    }


@pytest.mark.unit
def test_tool_spec_side_effects_field_is_enum_not_bool() -> None:
    """``ToolSpec.side_effects`` accepts ``SideEffects`` (FR-33: was ``bool``)."""
    from stargraph.ir._models import ToolSpec  # type: ignore[import-not-found]
    from stargraph.tools.spec import SideEffects  # type: ignore[import-not-found]

    spec = ToolSpec(
        name="search",
        namespace="t",
        version="1.0.0",
        description="d",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effects=SideEffects.write,
    )
    assert spec.side_effects is SideEffects.write
    assert isinstance(spec.side_effects, SideEffects)

    # Wire-format coercion: a plain string parses into the enum.
    spec2 = ToolSpec.model_validate(
        {
            "name": "x",
            "namespace": "t",
            "version": "1.0.0",
            "description": "d",
            "input_schema": {},
            "output_schema": {},
            "side_effects": "external",
        },
    )
    assert spec2.side_effects is SideEffects.external


@pytest.mark.unit
def test_tool_spec_replay_policy_defaults_to_must_stub() -> None:
    """``ToolSpec.replay_policy`` defaults to ``ReplayPolicy.must_stub`` (FR-33)."""
    from stargraph.ir._models import ToolSpec  # type: ignore[import-not-found]
    from stargraph.tools.spec import ReplayPolicy, SideEffects  # type: ignore[import-not-found]

    spec = ToolSpec(
        name="search",
        namespace="t",
        version="1.0.0",
        description="d",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        side_effects=SideEffects.none,
    )
    assert spec.replay_policy is ReplayPolicy.must_stub

    # Field is declared on the model (Pydantic v2 introspection).
    fields: dict[str, Any] = ToolSpec.model_fields
    assert "replay_policy" in fields
    assert fields["replay_policy"].default is ReplayPolicy.must_stub


# ---------------------------------------------------------------------------
# Stable-ID validators (FR-33, design §3.4.1) -- slug-format enforcement
# (lowercase start, ``[a-z0-9_\-.]`` continuation, total len 1..128) for
# node/rule/pack ids inside an ``IRDocument``. Validators live in
# ``stargraph.ir._ids`` and are invoked from ``stargraph.ir._validate.validate``
# (the canonical IR-load path). They are NOT wired through a
# ``model_validator`` decorator on ``IRDocument`` because FR-7 / AC-13.1
# ban Pydantic validator decorators in ``_models.py`` to keep the JSON
# Schema round-trip pure; the load-time guard is therefore the
# ``validate()`` function.
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stable_id_valid_accepts() -> None:
    """Valid lowercase slug ids (node/rule/pack) load without error (FR-33)."""
    from stargraph.ir._ids import (  # type: ignore[import-not-found]
        validate_node_id,
        validate_pack_id,
        validate_rule_id,
    )
    from stargraph.ir._validate import validate  # type: ignore[import-not-found]

    # Direct validator calls return the input unchanged.
    assert validate_node_id("node_a") == "node_a"
    assert validate_rule_id("r-advance") == "r-advance"
    assert validate_pack_id("pack.v1") == "pack.v1"

    # validate() returns [] for an IR doc with valid ids on every layer.
    errors = validate(
        {
            "ir_version": "1.0.0",
            "id": "run:test",
            "nodes": [{"id": "node_a", "kind": "echo"}],
            "rules": [{"id": "r-halt"}],
            "governance": [{"id": "pack.v1"}],
        },
    )
    assert errors == []


@pytest.mark.unit
def test_stable_id_uppercase_rejected() -> None:
    """Uppercase chars in any id raise ``ValueError`` (FR-33)."""
    from stargraph.ir._ids import validate_node_id  # type: ignore[import-not-found]
    from stargraph.ir._validate import validate  # type: ignore[import-not-found]

    # Direct call raises ValueError.
    with pytest.raises(ValueError, match="not a valid slug"):
        validate_node_id("Node_A")

    # Through validate() the violation surfaces as a structured
    # ValidationError entry pointing at the offending node id.
    errors = validate(
        {
            "ir_version": "1.0.0",
            "id": "run:test",
            "nodes": [{"id": "Node_A", "kind": "echo"}],
        },
    )
    assert len(errors) == 1
    assert errors[0].context["path"] == "/nodes/0/id"
    assert errors[0].context["actual"] == "Node_A"


@pytest.mark.unit
def test_stable_id_length_overflow_rejected() -> None:
    """Ids longer than 128 chars raise ``ValueError`` (FR-33)."""
    from stargraph.ir._ids import validate_rule_id  # type: ignore[import-not-found]
    from stargraph.ir._validate import validate  # type: ignore[import-not-found]

    overflow = "r" + "a" * 128  # 129 chars
    assert len(overflow) == 129

    with pytest.raises(ValueError, match="exceeds 128 chars"):
        validate_rule_id(overflow)

    errors = validate(
        {
            "ir_version": "1.0.0",
            "id": "run:test",
            "nodes": [{"id": "node_a", "kind": "echo"}],
            "rules": [{"id": overflow}],
        },
    )
    assert len(errors) == 1
    assert errors[0].context["path"] == "/rules/0/id"
