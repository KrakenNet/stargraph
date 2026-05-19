# SPDX-License-Identifier: Apache-2.0
"""Bosun ``budgets`` reference pack -- Stage-6 scaffold stubs (task T12).

Scaffold-stage declarations. Real bodies land in Ralph-Loop T12 (mirror
``bosun/safety_pii/__init__.py`` shape: ``_PATTERNS`` tuple, frozen
``BudgetDecision`` dataclass, sync ``decide()`` function, ``_PACK_ROOT``
constant for ``bosun/signing.sign_pack``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

__all__ = ["BudgetDecision", "decide"]


# Pack-root constant consumed by ``bosun.signing.sign_pack(tree=_PACK_ROOT, ...)``.
_PACK_ROOT: Path = Path(__file__).parent

_LOG: logging.Logger = logging.getLogger("harbor.bosun.budgets")

# Per-kind labels mirroring the three ``rules.clp`` budget rules. Each
# entry: (budget_kind slot, reason-label fragment). Order is insertion
# order; first match wins.
_PATTERNS: tuple[tuple[str, str], ...] = (
    ("tokens", "token allowance"),
    ("latency", "latency allowance"),
    ("cost", "cost allowance"),
)

# Throttle threshold: when used/limit crosses this fraction the pack
# emits ``action="throttle"`` instead of allow, giving the engine a
# nudge before the hard deny boundary.
_THROTTLE_RATIO: float = 0.9


@dataclass(frozen=True)
class BudgetDecision:
    """Result of :func:`decide` -- the action a Bosun budgets rule emits."""

    action: Literal["allow", "throttle", "deny"]
    remaining: float
    reason: str


def decide(*, budget_kind: str, used: float, limit: float) -> BudgetDecision:
    """Map ``(budget_kind, used, limit)`` to a :class:`BudgetDecision`.

    Mirrors the rules.clp ``budget-exhausted-*`` rules: when ``used >=
    limit`` the pack emits ``deny`` (halt severity in CLIPS); once usage
    crosses :data:`_THROTTLE_RATIO` of the limit, ``throttle`` warns the
    engine to back off; otherwise ``allow``. The reason string carries
    the per-kind label from :data:`_PATTERNS`.
    """
    remaining = limit - used
    label = next((lbl for kind, lbl in _PATTERNS if kind == budget_kind), budget_kind)
    if used >= limit:
        _LOG.info("budgets.decide deny kind=%s used=%.3f limit=%.3f", budget_kind, used, limit)
        return BudgetDecision(action="deny", remaining=remaining, reason=f"{label} exhausted")
    if used >= _THROTTLE_RATIO * limit:
        _LOG.info(
            "budgets.decide throttle kind=%s used=%.3f limit=%.3f", budget_kind, used, limit
        )
        return BudgetDecision(
            action="throttle", remaining=remaining, reason=f"{label} near exhaustion"
        )
    return BudgetDecision(action="allow", remaining=remaining, reason="under budget")
