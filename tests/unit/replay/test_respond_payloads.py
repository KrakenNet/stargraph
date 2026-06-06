# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: ``CounterfactualMutation.respond_payloads`` (FR-56, design §4.5).

Pins the Pydantic shape for the 6th counterfactual mutation field per
locked Decision #2 (cf:<actor> provenance prefix) before the live wiring
in :mod:`stargraph.replay.counterfactual` lands. Currently RED until task
2.32 adds ``respond_payloads`` to :class:`CounterfactualMutation`.

Cases:

1. ``test_respond_payloads_default_none`` -- the field defaults to
   ``None`` so existing five-field mutations keep working untouched.
2. ``test_respond_payloads_round_trip`` -- ``dict[int, dict[str, Any]]``
   maps step_n to a respond payload dict; validates and round-trips.
3. ``test_respond_payloads_in_extra_forbid_set`` -- a typo'd field name
   is still rejected (``extra='forbid'`` is preserved).
"""

from __future__ import annotations

import pytest

from stargraph.replay.counterfactual import CounterfactualMutation


def test_respond_payloads_default_none() -> None:
    """``respond_payloads`` defaults to ``None`` (no override)."""
    m = CounterfactualMutation()
    assert m.respond_payloads is None


def test_respond_payloads_round_trip() -> None:
    """``dict[int, dict[str, Any]]`` round-trips via ``model_dump``."""
    m = CounterfactualMutation(
        respond_payloads={5: {"decision": "reject", "reason": "out of scope"}},
    )
    assert m.respond_payloads is not None
    assert m.respond_payloads[5]["decision"] == "reject"
    dumped = m.model_dump(exclude_none=True, mode="json")
    # JSON dict keys are strings on the wire; pydantic preserves int keys
    # in Python-mode dump but JSON-mode coerces to str.
    assert "respond_payloads" in dumped


def test_respond_payloads_extra_forbid_still_active() -> None:
    """``extra='forbid'`` rejects unknown fields after the 6-field extension."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CounterfactualMutation(unknown_typo_field="boom")  # pyright: ignore[reportCallIssue]


def test_respond_payloads_combined_with_other_fields() -> None:
    """``respond_payloads`` composes with the existing 5 fields without conflict."""
    m = CounterfactualMutation(
        state_overrides={"x": 1},
        respond_payloads={3: {"approve": True}},
    )
    assert m.state_overrides == {"x": 1}
    assert m.respond_payloads == {3: {"approve": True}}
