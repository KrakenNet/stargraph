# SPDX-License-Identifier: Apache-2.0
"""MemoryDelta provenance + discriminator validation tests (FR-6, FR-28, AC-5.4).

Pins the typed-delta contract that fences the consolidation→fact promotion
seam (design §3.4 / §4.2):

* Pydantic enforces presence of ``source_episode_ids`` / ``promotion_ts``
  / ``rule_id`` / ``confidence`` on every variant at construction.
* :func:`stargraph.stores._delta._validate_delta_provenance` rejects empty
  ``source_episode_ids`` / ``replaces`` post-construction (raises
  :class:`stargraph.errors.ConsolidationRuleError`).
* :class:`stargraph.stores.memory.NoopDelta` does *not* require ``replaces``
  -- it is audit-only.
* The :data:`stargraph.stores.memory.MemoryDelta` discriminated union routes
  on ``kind`` so JSON round-trips land in the right concrete model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from stargraph.errors import ConsolidationRuleError
from stargraph.stores._delta import _validate_delta_provenance
from stargraph.stores.memory import (
    AddDelta,
    DeleteDelta,
    MemoryDelta,
    NoopDelta,
    UpdateDelta,
)

_TS = datetime(2026, 4, 29, tzinfo=UTC)


def _add_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "add",
        "fact_payload": {"subject": "alice", "predicate": "likes", "object": "tea"},
        "source_episode_ids": ["ep-1"],
        "promotion_ts": _TS,
        "rule_id": "rule-1",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


@pytest.mark.knowledge
@pytest.mark.unit
def test_provenance_fields_required() -> None:
    """AddDelta missing / empty ``source_episode_ids`` is rejected.

    Pydantic raises :class:`pydantic.ValidationError` on an outright
    missing field; :func:`_validate_delta_provenance` raises
    :class:`ConsolidationRuleError` on a constructed-but-empty list.
    Both arms gate the lineage column equally, so we assert both.
    """
    with pytest.raises(ValidationError):
        AddDelta(**_add_kwargs(source_episode_ids=...))  # type: ignore[arg-type]

    delta = AddDelta(**_add_kwargs(source_episode_ids=[]))
    with pytest.raises(ConsolidationRuleError) as exc:
        _validate_delta_provenance(delta)
    assert exc.value.context["violation"] == "source_episode_ids must be non-empty"
    assert exc.value.context["delta_type"] == "add"


@pytest.mark.knowledge
@pytest.mark.unit
def test_replaces_required_on_update_delete() -> None:
    """UpdateDelta / DeleteDelta with empty ``replaces`` raise at validation.

    Pydantic enforces the *field*; the runtime helper enforces the
    *non-empty* invariant -- an empty ``replaces`` would silently
    no-op the unpin step.
    """
    with pytest.raises(ValidationError):
        UpdateDelta(  # type: ignore[call-arg]
            kind="update",
            fact_payload={"x": 1},
            source_episode_ids=["ep-1"],
            promotion_ts=_TS,
            rule_id="rule-1",
            confidence=0.8,
        )

    update = UpdateDelta(
        kind="update",
        replaces=[],
        fact_payload={"x": 1},
        source_episode_ids=["ep-1"],
        promotion_ts=_TS,
        rule_id="rule-1",
        confidence=0.8,
    )
    with pytest.raises(ConsolidationRuleError) as exc_update:
        _validate_delta_provenance(update)
    assert "replaces must be non-empty" in exc_update.value.context["violation"]

    delete = DeleteDelta(
        kind="delete",
        replaces=[],
        source_episode_ids=["ep-1"],
        promotion_ts=_TS,
        rule_id="rule-1",
        confidence=0.8,
    )
    with pytest.raises(ConsolidationRuleError) as exc_delete:
        _validate_delta_provenance(delete)
    assert "replaces must be non-empty" in exc_delete.value.context["violation"]


@pytest.mark.knowledge
@pytest.mark.unit
def test_noop_delta_no_replaces() -> None:
    """NoopDelta has no ``replaces`` slot and validates without one."""
    delta = NoopDelta(
        kind="noop",
        source_episode_ids=["ep-1"],
        promotion_ts=_TS,
        rule_id="rule-1",
        confidence=1.0,
    )
    assert not hasattr(delta, "replaces")
    _validate_delta_provenance(delta)


@pytest.mark.knowledge
@pytest.mark.unit
def test_discriminator_kind() -> None:
    """``kind`` discriminator routes JSON-shaped dicts to the right variant."""
    adapter: TypeAdapter[MemoryDelta] = TypeAdapter(MemoryDelta)

    add = adapter.validate_python(_add_kwargs())
    assert isinstance(add, AddDelta)

    update = adapter.validate_python(
        {
            "kind": "update",
            "replaces": ["fact-1"],
            "fact_payload": {"x": 2},
            "source_episode_ids": ["ep-1"],
            "promotion_ts": _TS,
            "rule_id": "rule-1",
            "confidence": 0.5,
        },
    )
    assert isinstance(update, UpdateDelta)

    delete = adapter.validate_python(
        {
            "kind": "delete",
            "replaces": ["fact-1"],
            "source_episode_ids": ["ep-1"],
            "promotion_ts": _TS,
            "rule_id": "rule-1",
            "confidence": 0.5,
        },
    )
    assert isinstance(delete, DeleteDelta)

    noop = adapter.validate_python(
        {
            "kind": "noop",
            "source_episode_ids": ["ep-1"],
            "promotion_ts": _TS,
            "rule_id": "rule-1",
            "confidence": 1.0,
        },
    )
    assert isinstance(noop, NoopDelta)
