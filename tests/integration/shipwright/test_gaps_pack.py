# SPDX-License-Identifier: Apache-2.0
"""stargraph.bosun.shipwright.gaps — gap-detection rules for graphs."""

from __future__ import annotations

import pytest
from fathom import Engine  # noqa: TC002

from stargraph.skills.shipwright._pack import fresh_engine, load_pack


@pytest.fixture
def engine() -> Engine:
    eng = fresh_engine()
    load_pack(eng, "gaps")
    return eng


def _gap_facts_for_slot(eng: Engine, slot: str) -> list[dict[str, object]]:
    """Return all `(spec_gap … (slot <slot>) …)` facts."""
    raw = eng._env.find_template("spec_gap").facts()  # pyright: ignore[reportPrivateUsage]
    return [dict(f) for f in raw if dict(f).get("slot") == slot]


@pytest.mark.integration
def test_purpose_gap_fires_when_slot_absent(engine: Engine) -> None:
    engine._env.assert_string('(spec.kind (value "graph"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    assert len(_gap_facts_for_slot(engine, "purpose")) == 1


@pytest.mark.integration
def test_purpose_gap_silent_when_slot_present(engine: Engine) -> None:
    engine._env.assert_string('(spec.kind (value "graph"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string('(spec.slot (name "purpose") (value "triage SOC alerts"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    assert _gap_facts_for_slot(engine, "purpose") == []


@pytest.mark.integration
@pytest.mark.parametrize(
    "missing_slot",
    ["purpose", "nodes", "state_fields", "stores", "triggers"],
)
def test_each_required_graph_slot_has_a_gap_rule(engine: Engine, missing_slot: str) -> None:
    engine._env.assert_string('(spec.kind (value "graph"))')  # pyright: ignore[reportPrivateUsage]
    for slot in ("purpose", "nodes", "state_fields", "stores", "triggers"):
        if slot == missing_slot:
            continue
        engine._env.assert_string(f'(spec.slot (name "{slot}") (value "x"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    matches = _gap_facts_for_slot(engine, missing_slot)
    assert len(matches) == 1, f"no rule fired for missing {missing_slot}"


@pytest.mark.integration
def test_no_annotated_field_emits_edge_case_gap(engine: Engine) -> None:
    engine._env.assert_string('(spec.kind (value "graph"))')  # pyright: ignore[reportPrivateUsage]
    for slot in ("purpose", "nodes", "stores", "triggers"):
        engine._env.assert_string(f'(spec.slot (name "{slot}") (value "x"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string('(spec.slot (name "state_fields") (value "[]"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string("(spec.annotated_count (value 0))")  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    matches = _gap_facts_for_slot(engine, "annotated_state")
    assert len(matches) == 1
    assert matches[0]["kind"] == "edge_case"


@pytest.mark.integration
def test_cleared_profile_requires_side_effects(engine: Engine) -> None:
    engine._env.assert_string('(spec.kind (value "graph"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string('(spec.profile (value "cleared"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.assert_string('(spec.node_missing_side_effects (node "browser_search"))')  # pyright: ignore[reportPrivateUsage]
    engine._env.run()  # pyright: ignore[reportPrivateUsage]
    matches = _gap_facts_for_slot(engine, "side_effects:browser_search")
    assert len(matches) == 1
    assert matches[0]["kind"] == "required"
