# SPDX-License-Identifier: Apache-2.0
"""Action vocabulary translator -- IR :data:`Action` list → :data:`RoutingDecision` (Learning F).

Foundation's :func:`harbor.fathom.extract_actions` produces a list of typed IR
:data:`Action` instances from CLIPS ``harbor_action`` fact slot dicts, including
the five native Fathom kinds (``allow|deny|escalate|scope|route``) extended in
task 1.17. The runtime loop (`design §3.1.2 step 5`) then needs a single
routing decision per node tick. :func:`translate_actions` is that bridge.

A :data:`RoutingDecision` is a Pydantic discriminated union over four
``kind``-tagged variants -- the three IR routing actions plus a
:class:`ContinueAction` sentinel for the "no routing change" case:

* :class:`HaltAction` (also produced by ``deny`` extension, with
  ``reason="denied-by-rule"``) → the loop ``break``s.
* :class:`GotoAction` (also produced by ``escalate`` and ``route`` extensions)
  → the loop sets ``current = action.target`` and continues.
* :class:`ParallelAction` → the loop fans out via the parallel coordinator.
* :class:`ContinueAction` → the loop walks the static IR edge. This is the
  ``allow`` case (extract_actions emits no IR Action for ``allow``), the
  ``scope`` case (state filter is applied at the adapter layer; no routing
  change), and the empty-firings case.

Precedence (when multiple actions fire in the same tick): halt > goto >
parallel > continue. Halt always wins (fail-closed); among multiple
non-halt routing actions, the first wins (rule order is the IR's contract).

The IR :class:`HaltAction`, :class:`GotoAction`, and :class:`ParallelAction`
are reused directly: their slot shapes are semantically identical to a
runtime routing decision, so a parallel hierarchy of "runtime twins" would
be duplication without payoff.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from harbor.ir._models import (
    Action,
    GotoAction,
    HaltAction,
    InterruptAction,
    IRBase,
    ParallelAction,
)

__all__ = [
    "ContinueAction",
    "GotoAction",
    "HaltAction",
    "InterruptAction",
    "ParallelAction",
    "RoutingDecision",
    "translate_actions",
]


class ContinueAction(IRBase):
    """No routing change -- walk the static IR edge from the current node.

    Emitted when no halt/goto/parallel action fires (the ``allow`` case, the
    ``scope`` case, or an empty rule-firing).
    """

    kind: Literal["continue"] = "continue"


RoutingDecision = Annotated[
    ContinueAction | HaltAction | GotoAction | ParallelAction | InterruptAction,
    Field(discriminator="kind"),
]
"""Single routing decision produced by :func:`translate_actions` (design §3.1.2).

Pydantic discriminated union over the four tick-routing variants. Callers
dispatch on ``decision.kind`` (or ``isinstance``) -- pyright narrows the
variant's fields automatically.
"""


def translate_actions(actions: list[Action]) -> RoutingDecision:
    """Pick a single :data:`RoutingDecision` from a rule-firing's action list.

    Precedence: interrupt > halt > goto > parallel > continue. Interrupt and
    halt are both fail-closed (matches the ``deny`` extension's intent);
    interrupt takes precedence so a HITL gate rule that fires concurrently
    with a downstream goto isn't silently dropped (would let a run skip the
    interrupt boundary and re-enter the same gate node on the next tick,
    producing a routing hot-loop). Side-effect actions (:class:`AssertAction`,
    :class:`RetractAction`, :class:`RetryAction`) carry no routing semantics
    in v1 and are skipped; if no routing-bearing action fires, the result is
    a :class:`ContinueAction`.
    """
    interrupt: InterruptAction | None = None
    halt: HaltAction | None = None
    goto: GotoAction | None = None
    parallel: ParallelAction | None = None
    for action in actions:
        if isinstance(action, InterruptAction) and interrupt is None:
            interrupt = action
        elif isinstance(action, HaltAction) and halt is None:
            halt = action
        elif isinstance(action, GotoAction) and goto is None:
            goto = action
        elif isinstance(action, ParallelAction) and parallel is None:
            parallel = action
    if interrupt is not None:
        return interrupt
    if halt is not None:
        return halt
    if goto is not None:
        return goto
    if parallel is not None:
        return parallel
    return ContinueAction()
