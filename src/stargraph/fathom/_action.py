# SPDX-License-Identifier: Apache-2.0
"""Action extraction: ``stargraph_action`` fact dicts → typed ``Action`` instances (FR-4).

The six Action variants (one per Stargraph verb -- ``goto``, ``halt``, ``parallel``,
``retry``, ``assert``, ``retract``) live in :mod:`stargraph.ir._models` as Pydantic
:class:`~stargraph.ir.IRBase` subclasses with a ``kind: Literal[...]`` discriminator
(FR-11). This module re-exports them under ``stargraph.fathom`` for adapter
ergonomics and provides :func:`extract_actions`, which translates a list of
``stargraph_action`` fact slot dicts (as produced by the Fathom adapter) into the
typed Pydantic discriminated union. Unknown ``kind`` values raise
:class:`stargraph.errors.ValidationError` -- there is no silent fallback.
"""

from __future__ import annotations

from typing import Any

from stargraph.errors import ValidationError
from stargraph.ir._models import (
    Action,
    AssertAction,
    GotoAction,
    HaltAction,
    ParallelAction,
    RetractAction,
    RetryAction,
)

__all__ = [
    "Action",
    "AssertAction",
    "GotoAction",
    "HaltAction",
    "ParallelAction",
    "RetractAction",
    "RetryAction",
    "extract_actions",
]


def extract_actions(facts: list[dict[str, Any]]) -> list[Action]:
    """Translate ``stargraph_action`` fact slot dicts into typed :data:`Action` instances.

    Each fact must carry a ``kind`` slot drawn from the six Stargraph verbs
    (``goto``, ``halt``, ``parallel``, ``retry``, ``assert``, ``retract``) or
    one of the five native Fathom action kinds (``allow``, ``deny``,
    ``escalate``, ``scope``, ``route``) per design §3.1.4 / §4.4 (Learning F,
    FR-33). Native kinds are translated into the existing IR Action union:

    * ``deny`` → :class:`HaltAction` with ``reason="denied-by-rule"``.
    * ``escalate`` → :class:`GotoAction` with
      ``target = fact["escalation_target"]``.
    * ``route`` → :class:`GotoAction` with ``target = fact["target"]``.
    * ``allow`` → no Action emitted (continue is the empty case; the runtime
      translator walks the static IR edge).
    * ``scope`` → no Action emitted (state-filter side effect is applied at
      the adapter layer per design §3.1.4; no routing change).

    Unknown ``kind`` values raise :class:`ValidationError`; missing required
    variant fields propagate the underlying :class:`pydantic.ValidationError`
    from the Pydantic constructor.
    """
    actions: list[Action] = []
    for fact in facts:
        kind = fact.get("kind")
        if kind == "goto":
            actions.append(GotoAction(target=fact["target"]))
        elif kind == "halt":
            actions.append(HaltAction(reason=fact.get("reason", "")))
        elif kind == "parallel":
            actions.append(
                ParallelAction(
                    targets=list(fact.get("targets", [])),
                    join=fact.get("join", ""),
                    strategy=fact.get("strategy", "all"),
                )
            )
        elif kind == "retry":
            actions.append(
                RetryAction(
                    target=fact["target"],
                    backoff_ms=int(fact.get("backoff_ms", 0)),
                )
            )
        elif kind == "assert":
            actions.append(
                AssertAction(
                    fact=fact["fact"],
                    slots=fact.get("slots", ""),
                )
            )
        elif kind == "retract":
            actions.append(RetractAction(pattern=fact["pattern"]))
        elif kind == "deny":
            actions.append(HaltAction(reason="denied-by-rule"))
        elif kind == "escalate":
            actions.append(GotoAction(target=fact["escalation_target"]))
        elif kind == "route":
            actions.append(GotoAction(target=fact["target"]))
        elif kind in {"allow", "scope"}:
            # Native Fathom kinds with no IR Action mapping (design §3.1.4):
            # - ``allow``: continue current edge (empty list = continue).
            # - ``scope``: state filter applied at adapter layer; no routing
            #   decision. Both are recognized (not rejected) and emit nothing.
            continue
        else:
            raise ValidationError(
                f"unknown stargraph_action kind: {kind!r}",
                kind=str(kind),
            )
    return actions
