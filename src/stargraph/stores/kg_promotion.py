# SPDX-License-Identifier: Apache-2.0
"""Knowledge-graph triple → :class:`Fact` promotion (FR-30 / AC-6.x).

Phase-1 POC of design §3.13: a free function that materialises rows from a
filter Cypher query into pinned :class:`Fact` rows on a :class:`FactStore`,
attaching standard provenance via :class:`stargraph.fathom.FathomAdapter`.

Pipeline:

1. The supplied ``filter_cypher`` is checked against
   :class:`stargraph.stores.cypher.Linter`. Mutating queries
   (``Linter.requires_write``) are rejected with
   :class:`stargraph.errors.UnportableCypherError`; non-portable queries raise
   the same error from :meth:`Linter.check`.
2. ``graph_store.query(filter_cypher)`` materialises a :class:`ResultSet`.
3. For each row we build a ``{subject, predicate, object, source}`` slot
   bundle and call ``fathom_adapter.assert_with_provenance`` with a
   :class:`ProvenanceBundle` carrying ``origin='graph_promotion'``,
   ``source=f'ryugraph:{path}'``, the caller's ``rule_id`` / ``agent_id``,
   ``row.confidence`` (defaulting to ``Decimal("1.0")`` if absent), and a
   timezone-aware ``datetime.now(UTC)``. Engine assertion is best-effort in
   the POC: an exception in the adapter is logged and skipped so that
   FactStore promotion still proceeds.
4. The same row is pinned as a :class:`Fact` on ``fact_store``. The pinned
   ``lineage`` records the originating ``triple_id`` (synthesised from the
   triple slots when not supplied), ``rule_id``, ``agent_id``, and
   promotion timestamp.

**One-way semantics**: triple deletion in the underlying graph does NOT
auto-retract the promoted :class:`Fact`. Callers that need bidirectional
linkage must invoke :meth:`FactStore.unpin` themselves.

Returns the list of pinned :class:`Fact` rows (the canonical "promotion"
output -- the Fathom assertion side is observability/rule-engine state,
not the persistence record).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from stargraph.errors import UnportableCypherError
from stargraph.stores.cypher import Linter
from stargraph.stores.fact import Fact

if TYPE_CHECKING:
    from stargraph.fathom import FathomAdapter, ProvenanceBundle
    from stargraph.stores.fact import FactStore
    from stargraph.stores.graph import GraphStore

__all__ = ["PromoteTriplesToFacts"]


_log = logging.getLogger(__name__)

_TRIPLE_SLOTS = ("subject", "predicate", "object", "source")


def _coerce_str(value: Any, fallback: str = "") -> str:
    return value if isinstance(value, str) else (str(value) if value is not None else fallback)


def _row_confidence(row: dict[str, Any]) -> Decimal:
    raw = row.get("confidence")
    if isinstance(raw, Decimal):
        return raw
    if isinstance(raw, int):
        return Decimal(raw)
    if isinstance(raw, str):
        try:
            return Decimal(raw)
        except (ArithmeticError, ValueError):
            return Decimal("1.0")
    # POC: floats and missing values fall back to 1.0 (see module docstring).
    return Decimal("1.0")


def _graph_source(graph_store: GraphStore) -> str:
    path = getattr(graph_store, "_path", None)
    return f"ryugraph:{path}" if path is not None else "ryugraph:unknown"


async def PromoteTriplesToFacts(  # noqa: N802 -- spec-mandated PascalCase rule name
    graph_store: GraphStore,
    fact_store: FactStore,
    fathom_adapter: FathomAdapter,
    *,
    filter_cypher: str,
    rule_id: str,
    agent_id: str,
) -> list[Fact]:
    """Promote rows from ``filter_cypher`` into pinned :class:`Fact` records.

    Parameters
    ----------
    graph_store:
        Source :class:`GraphStore`. Must already be ``bootstrap()``-ed.
    fact_store:
        Target :class:`FactStore`. Each promoted row becomes one
        ``pin()``-ed :class:`Fact`.
    fathom_adapter:
        :class:`FathomAdapter` used for the AC-6.2 provenance assertion
        side-channel. Adapter failures are logged and tolerated -- the
        FactStore promotion is the authoritative output.
    filter_cypher:
        Read-only Cypher selecting the rows to promote. Must return at
        least the ``subject`` / ``predicate`` / ``object`` columns; an
        optional ``confidence`` column overrides the default.
    rule_id:
        Identifier of the promotion rule (lineage ``rule_id``).
    agent_id:
        Identifier of the asserting agent (lineage ``agent_id``).

    Raises
    ------
    UnportableCypherError
        If ``filter_cypher`` falls outside the portable subset, or if it
        mutates graph state (``MERGE`` / ``CREATE`` / ``SET`` / ``DELETE``
        etc. -- promotion must be read-only).
    """
    linter = Linter()
    linter.check(filter_cypher)
    if linter.requires_write(filter_cypher):
        raise UnportableCypherError(
            "PromoteTriplesToFacts requires a read-only filter query",
            cypher=filter_cypher,
            violation="write-in-promotion",
            rule="write-in-promotion",
        )

    result = await graph_store.query(filter_cypher)
    source = _graph_source(graph_store)
    promoted: list[Fact] = []

    for row in result.rows:
        subject = _coerce_str(row.get("subject"))
        predicate = _coerce_str(row.get("predicate"))
        obj = _coerce_str(row.get("object"))
        confidence = _row_confidence(row)
        now = datetime.now(UTC)
        triple_id = _coerce_str(
            row.get("triple_id"),
            fallback=f"{subject}|{predicate}|{obj}",
        )

        slots: dict[str, Any] = {
            "subject": subject,
            "predicate": predicate,
            "object": obj,
            "source": source,
        }
        provenance: ProvenanceBundle = {
            "origin": "graph_promotion",
            "source": source,
            "run_id": rule_id,
            "step": 0,
            "confidence": confidence,
            "timestamp": now,
        }
        try:
            fathom_adapter.assert_with_provenance(
                template="stargraph.evidence",
                slots=slots,
                provenance=provenance,
            )
        except Exception:  # best-effort POC side-channel; engine wiring lands later
            _log.warning(
                "FathomAdapter.assert_with_provenance failed; FactStore promotion continues",
                exc_info=True,
            )

        fact = Fact(
            id=str(uuid4()),
            user="",
            agent=agent_id,
            payload={
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "source": source,
            },
            lineage=[
                {
                    "triple_id": triple_id,
                    "rule_id": rule_id,
                    "agent_id": agent_id,
                    "promotion_ts": now.isoformat(),
                }
            ],
            confidence=float(confidence),
            pinned_at=now,
        )
        await fact_store.pin(fact)
        promoted.append(fact)

    return promoted
