# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: ``CounterfactualMutation`` Pydantic builder shape (FR-27).

Pins the Pydantic shape for the typed counterfactual mutation builder per
design §3.8.2 *before* the implementation lands in task 3.33. Currently
RED because :mod:`stargraph.replay.counterfactual` (and the
``CounterfactualMutation`` symbol it exports) does not yet exist; the
``importlib.import_module`` call fails first with :class:`ImportError`.

Cases (FR-27 amendment 6 / design §3.8.2):

1. ``test_mutation_default_construction`` -- all five fields default to
   ``None`` so the empty mutation is a valid, no-op builder.
2. ``test_mutation_accepts_all_documented_fields`` -- ``state_overrides``,
   ``facts_assert``, ``facts_retract``, ``rule_pack_version``,
   ``node_output_overrides`` round-trip via ``model_validate`` /
   ``model_dump``.
3. ``test_mutation_forbids_extra_fields`` -- ``model_config =
   ConfigDict(extra="forbid")`` rejects an unknown key with
   :class:`pydantic.ValidationError`.
4. ``test_mutation_state_overrides_typed_dict`` -- ``state_overrides``
   accepts ``dict[str, Any]`` (mixed value types per design §3.8.2).
5. ``test_mutation_node_output_overrides_keyed_by_node_id`` --
   ``node_output_overrides`` is a ``dict[str, Any]`` keyed by ``node_id``;
   the value at the cf-step replaces the recorded output (design §3.8.4
   step 6).
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest


def _mutation_cls() -> Any:
    """Import ``CounterfactualMutation`` (TDD-RED: not yet built)."""
    mod = importlib.import_module("stargraph.replay.counterfactual")
    return mod.CounterfactualMutation


def test_mutation_default_construction() -> None:
    """Empty builder is valid -- all five fields default to ``None``."""
    cls = _mutation_cls()
    mutation = cls()
    assert mutation.state_overrides is None
    assert mutation.facts_assert is None
    assert mutation.facts_retract is None
    assert mutation.rule_pack_version is None
    assert mutation.node_output_overrides is None


def test_mutation_accepts_all_documented_fields() -> None:
    """All five FR-27 mutation fields round-trip through ``model_dump``."""
    cls = _mutation_cls()
    mutation = cls(
        state_overrides={"x": 1, "y": "two"},
        facts_assert=[],
        facts_retract=[],
        rule_pack_version="v2.0.0",
        node_output_overrides={"node_a": {"result": 42}},
    )
    dumped = mutation.model_dump(exclude_none=True, mode="json")
    assert dumped["state_overrides"] == {"x": 1, "y": "two"}
    assert dumped["rule_pack_version"] == "v2.0.0"
    assert dumped["node_output_overrides"] == {"node_a": {"result": 42}}


def test_mutation_forbids_extra_fields() -> None:
    """``extra="forbid"`` rejects unknown keys (design §3.8.2)."""
    from pydantic import ValidationError

    cls = _mutation_cls()
    with pytest.raises(ValidationError):
        cls(unknown_field="boom")


def test_mutation_state_overrides_typed_dict() -> None:
    """``state_overrides`` is a ``dict[str, Any]`` -- mixed value types."""
    cls = _mutation_cls()
    mutation = cls(state_overrides={"int_field": 7, "list_field": [1, 2, 3]})
    assert mutation.state_overrides["int_field"] == 7
    assert mutation.state_overrides["list_field"] == [1, 2, 3]


def test_mutation_node_output_overrides_keyed_by_node_id() -> None:
    """``node_output_overrides`` keys are ``node_id`` strings (design §3.8.4)."""
    cls = _mutation_cls()
    mutation = cls(node_output_overrides={"classify_intent": {"label": "buy"}})
    assert "classify_intent" in mutation.node_output_overrides
