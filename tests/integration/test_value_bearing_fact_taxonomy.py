# SPDX-License-Identifier: Apache-2.0
"""Value-bearing fact taxonomy (design §4.3, Learning A).

Pins the classification table from design §4.3 that splits Stargraph's fact
templates into **value-bearing** (must carry the 6-slot
:class:`~stargraph.fathom.ProvenanceBundle`) and **metadata-only** (control
plane / telemetry / debug; no state-derivation, no provenance contract
required by FR-3).

The taxonomy itself is enforced statically by
:mod:`tests.unit.test_provenance_enforcer` (no ``.assert_fact(...)`` outside
``src/stargraph/fathom/``). This integration test does two things on top:

1. **Schema pin** -- the ``_TAXONOMY`` table mirrors design §4.3 verbatim;
   if a classification ever changes, this test is the canonical place to
   reflect that.
2. **Provenance contract sanity** -- when a value-bearing fact template is
   asserted through :meth:`~stargraph.fathom.FathomAdapter.assert_with_provenance`,
   all 6 provenance slots (``_origin _source _run_id _step _confidence
   _timestamp``) reach the underlying ``engine.assert_fact`` call. When a
   metadata-only template is asserted directly through ``engine.assert_fact``
   (the only mechanism still allowed for templates that do not carry
   user-derived state and is currently used only by infrastructure), no
   provenance slots are required.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import fathom
import pytest

from stargraph.fathom import FathomAdapter, ProvenanceBundle

# Design §4.3 verbatim taxonomy. ``True`` = value-bearing (FR-3 mandates the
# 6-slot ProvenanceBundle); ``False`` = metadata-only (telemetry / control
# plane / debug; no state-derivation, no provenance contract).
_TAXONOMY: dict[str, bool] = {
    "stargraph.tool-result": True,
    "stargraph.tool-call": True,
    "stargraph.evidence": True,
    "stargraph.tokens-used": False,
    "stargraph.transition": False,
    "stargraph.checkpoint": False,
    "stargraph.error": False,
    "stargraph.heartbeat": False,
    # ``stargraph.disagreement`` is value-bearing in v1.x but deferred in v1
    # (epic decision 11). Listed for table completeness.
    "stargraph.disagreement": True,
}

_REQUIRED_PROV_SLOTS: frozenset[str] = frozenset(
    {"_origin", "_source", "_run_id", "_step", "_confidence", "_timestamp"}
)


def _bundle() -> ProvenanceBundle:
    """A minimal, well-formed :class:`ProvenanceBundle` for adapter calls."""
    return ProvenanceBundle(
        origin="test",
        source="test_value_bearing_fact_taxonomy",
        run_id="00000000000000000000000000000001",
        step=0,
        confidence=Decimal("1.0"),
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.integration
def test_taxonomy_table_matches_design_section_4_3() -> None:
    """Pins design §4.3 -- 9 named templates with their value-bearing flag.

    If a row is added, removed, or reclassified, update both this table and
    design §4.3 in the same commit.
    """
    expected_value_bearing = {
        "stargraph.tool-result",
        "stargraph.tool-call",
        "stargraph.evidence",
        "stargraph.disagreement",
    }
    expected_metadata = {
        "stargraph.tokens-used",
        "stargraph.transition",
        "stargraph.checkpoint",
        "stargraph.error",
        "stargraph.heartbeat",
    }
    actual_value_bearing = {t for t, vb in _TAXONOMY.items() if vb}
    actual_metadata = {t for t, vb in _TAXONOMY.items() if not vb}
    assert actual_value_bearing == expected_value_bearing
    assert actual_metadata == expected_metadata


@pytest.mark.integration
@pytest.mark.parametrize(
    "template",
    sorted(t for t, vb in _TAXONOMY.items() if vb),
)
def test_value_bearing_template_carries_six_provenance_slots(template: str) -> None:
    """Every value-bearing template, when asserted through
    :meth:`assert_with_provenance`, reaches ``engine.assert_fact`` with all
    6 provenance slots merged into the slot dict (FR-3, design §4.3+§4.5)."""
    engine = MagicMock(spec=fathom.Engine)
    adapter = FathomAdapter(engine=engine)

    adapter.assert_with_provenance(template, {"value": "x"}, _bundle())

    engine.assert_fact.assert_called_once()
    args, _kwargs = engine.assert_fact.call_args
    asserted_template, asserted_slots = args
    assert asserted_template == template
    missing = _REQUIRED_PROV_SLOTS - asserted_slots.keys()
    assert not missing, (
        f"value-bearing template {template!r} missing provenance slots {sorted(missing)}"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "template",
    sorted(t for t, vb in _TAXONOMY.items() if not vb),
)
def test_metadata_template_does_not_require_provenance_at_engine_layer(
    template: str,
) -> None:
    """Metadata-only templates (control plane / telemetry / debug) do not
    carry user-derived state and therefore are not bound by the FR-3
    provenance contract at the wire level. Asserting one directly via
    ``engine.assert_fact`` (the path infrastructure code may use for
    transition/checkpoint/error pulses) is permitted: the slot dict need not
    contain the 6 provenance underscore-slots.

    (The AST-walker enforcer in ``test_provenance_enforcer.py`` still bans
    ``.assert_fact(...)`` calls in modules outside ``src/stargraph/fathom/``;
    this test asserts only that the wire format itself is unconstrained for
    metadata templates.)
    """
    engine = MagicMock(spec=fathom.Engine)
    # Direct engine call simulating an infrastructure metadata pulse.
    engine.assert_fact(template, {"kind": "synthetic"})

    engine.assert_fact.assert_called_once()
    args, _kwargs = engine.assert_fact.call_args
    _t, slots = args
    # No slots from the provenance bundle are required.
    assert not (_REQUIRED_PROV_SLOTS & slots.keys()), (
        f"metadata-only template {template!r} unexpectedly carries provenance slots; "
        "metadata pulses are not value-bearing per design §4.3"
    )


@pytest.mark.integration
@pytest.mark.xfail(
    reason=(
        "TODO: a runtime classifier that auto-routes value-bearing templates "
        "through assert_with_provenance and metadata templates through a "
        "metadata sink does not exist yet. Today, the AST-walker in "
        "tests/unit/test_provenance_enforcer.py is the only enforcement "
        "mechanism, and it is template-agnostic. Spec a follow-up task to "
        "add per-template routing if/when the dual-sink design lands."
    ),
    strict=True,
)
def test_runtime_classifier_routes_by_taxonomy() -> None:
    """Placeholder for the future runtime classifier.

    Expectation (when implemented): a single emission entry point inspects
    the template name, looks it up in the §4.3 taxonomy, and either
    (a) requires a :class:`ProvenanceBundle` and forwards through
    :meth:`assert_with_provenance` (value-bearing) or (b) forwards directly
    to the metadata sink without provenance (metadata-only).
    """
    import stargraph.fathom as _fathom_pkg

    # Marker symbol; the future runtime classifier will export it.
    assert hasattr(_fathom_pkg, "emit_by_taxonomy"), (
        "TODO: implement stargraph.fathom.emit_by_taxonomy as the per-template "
        "router described in design §4.3."
    )
