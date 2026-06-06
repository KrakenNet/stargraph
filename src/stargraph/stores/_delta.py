# SPDX-License-Identifier: Apache-2.0
"""Shared :data:`MemoryDelta` validation + replace-resolution helpers (FR-6/FR-28).

Both :meth:`stargraph.stores.sqlite_memory.SQLiteMemoryStore.consolidate` (delta
emission) and :meth:`stargraph.stores.sqlite_fact.SQLiteFactStore.apply_delta`
(delta application) need to enforce the same provenance contract before a
typed delta lands in the ``facts.lineage`` column (design §4.2). Centralising
the checks here prevents drift between the producer and consumer seams.

Provenance contract (design §3.4 / FR-29):

* Every variant carries non-empty ``rule_id``, ``source_episode_ids``, and
  ``promotion_ts``; ``confidence`` must be a real (finite) value.
* ``UpdateDelta`` and ``DeleteDelta`` additionally require a non-empty
  ``replaces`` list -- they unpin / replace existing fact ids and an empty
  ``replaces`` would silently no-op.
* :func:`_resolve_replaces` looks each ``replaces`` id up in the supplied
  :class:`stargraph.stores.fact.FactStore` so callers can unpin only the rows
  that actually exist (Phase-3 pattern matching land later; today the
  helper does an id-equality lookup via :class:`FactPattern`).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from stargraph.errors import ConsolidationRuleError
from stargraph.stores.fact import FactPattern
from stargraph.stores.memory import DeleteDelta, UpdateDelta

if TYPE_CHECKING:
    from stargraph.stores.fact import Fact, FactStore
    from stargraph.stores.memory import MemoryDelta

__all__ = ["_resolve_replaces", "_validate_delta_provenance"]


def _validate_delta_provenance(delta: MemoryDelta) -> None:
    """Reject deltas missing required provenance fields (FR-29 lineage).

    Pydantic enforces presence at construction; this guards against empty
    strings / empty lists / NaN confidence slipping through into the
    ``facts.lineage`` column where they would silently break replay
    (design §4.2). UPDATE / DELETE additionally require non-empty
    ``replaces`` so the unpin step actually targets a row.

    Raises :class:`stargraph.errors.ConsolidationRuleError` with structured
    ``rule_id`` / ``delta_type`` / ``violation`` context per the design §7
    error matrix.
    """
    if not delta.rule_id:
        _raise(delta, "rule_id must be non-empty")
    if not delta.source_episode_ids:
        _raise(delta, "source_episode_ids must be non-empty")
    if not math.isfinite(delta.confidence):
        _raise(delta, "confidence must be finite")
    if isinstance(delta, UpdateDelta | DeleteDelta) and not delta.replaces:
        _raise(delta, "replaces must be non-empty for update/delete")


async def _resolve_replaces(
    fact_store: FactStore,
    replaces: list[str],
) -> list[Fact]:
    """Resolve a ``replaces`` id list to the concrete :class:`Fact` rows.

    POC-compatible lookup: queries ``fact_store`` once per id (FactPattern
    has no id slot today; full pattern matching lands in Phase 3) and
    filters the result by ``Fact.id`` equality. Missing ids are dropped --
    UPDATE / DELETE callers are expected to unpin every id regardless,
    so the helper's job is only to surface the rows that *do* exist for
    audit / lineage chaining.
    """
    resolved: list[Fact] = []
    seen: set[str] = set()
    for fact_id in replaces:
        if fact_id in seen:
            continue
        seen.add(fact_id)
        rows = await fact_store.query(FactPattern())
        resolved.extend(row for row in rows if row.id == fact_id)
    return resolved


def _raise(delta: MemoryDelta, violation: str) -> None:
    """Raise :class:`ConsolidationRuleError` carrying structured context."""
    raise ConsolidationRuleError(
        f"MemoryDelta.{violation}",
        rule_id=delta.rule_id,
        delta_type=delta.kind,
        violation=violation,
    )
