# SPDX-License-Identifier: Apache-2.0
"""SalienceScorer Protocol + RuleBasedScorer (design §3.6, FR-31).

Salience gates episodic→semantic consolidation: episodes scoring below a
caller-chosen threshold are filtered before the rule body fires (avoids
promoting noise per AC-5.5). v1 ships :class:`RuleBasedScorer` (Park
2023 formula) with weights ``recency=1.0, relevance=0.0,
importance=0.0`` -- relevance + importance terms hold structural seats
for v2 embedding-similarity / v3 learned scorers swapped behind the
same Protocol.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from stargraph.stores.memory import Episode  # noqa: TC001

__all__ = [
    "RuleBasedScorer",
    "SalienceContext",
    "SalienceScorer",
]


def _default_weights() -> dict[str, float]:
    return {"recency": 1.0, "relevance": 0.0, "importance": 0.0}


class SalienceContext(BaseModel):
    """Per-call inputs to a :class:`SalienceScorer` (design §3.6).

    ``last_access_ts`` is the recency anchor (Park 2023 §4.1: decay since
    last access, NOT creation). ``access_count`` and
    ``rule_match_count`` shape the frequency / rule-affinity factors.
    ``query_embedding`` is reserved for v2 relevance scoring; v1
    rule-based default ignores it.
    """

    query_embedding: list[float] | None = None
    last_access_ts: datetime
    access_count: int
    rule_match_count: int
    weights: dict[str, float] = Field(default_factory=_default_weights)
    decay_tau_seconds: float = 86400.0


@runtime_checkable
class SalienceScorer(Protocol):
    """Pluggable salience scoring (FR-31).

    Implementations return a value in ``[0, 1]``. Protocol stable across
    v1 (rule-based) → v2 (embedding-similarity) → v3 (learned) — only
    weights and the scorer instance change.
    """

    async def score(self, memory: Episode, context: SalienceContext) -> float: ...


class RuleBasedScorer:
    """v1 default: recency * frequency * rule-match (design §3.6).

    Implements Park et al. 2023 formula structurally::

        score = w_recency * exp(-delta_t / tau)
              + w_relevance * cos(q_emb, m_emb)
              + w_importance * imp(m)

    Multiplied by frequency + rule-match factors and clamped to
    ``[0, 1]``. v1 weights default ``relevance=0.0`` and
    ``importance=0.0`` (rule-based-only constraint per epic decision);
    v2 swaps to embedding similarity, v3 swaps to a learned scorer.
    """

    async def score(self, memory: Episode, ctx: SalienceContext) -> float:
        _ = memory  # v1 ignores per-episode payload; v2 reads m_emb
        delta_t = (datetime.now(UTC) - ctx.last_access_ts).total_seconds()
        recency = math.exp(-delta_t / ctx.decay_tau_seconds)
        relevance = 0.0  # v1 rule-based only
        importance = 0.0  # v1 rule-based only
        frequency_factor = math.tanh(ctx.access_count / 10.0)  # bounded [0, ~1]
        rule_factor = math.tanh(ctx.rule_match_count / 5.0)
        weighted = (
            ctx.weights["recency"] * recency
            + ctx.weights["relevance"] * relevance
            + ctx.weights["importance"] * importance
        )
        score = weighted * frequency_factor * rule_factor
        return max(0.0, min(1.0, score))
