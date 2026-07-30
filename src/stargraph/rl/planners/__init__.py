# SPDX-License-Identifier: Apache-2.0
"""stargraph.rl.planners -- the planning extension point (W7).

Planners are discovered through the ``stargraph.planners`` entry-point group
(exactly like triggers/tools): each entry names a zero-arg-constructible class
satisfying the :class:`Planner` protocol -- *propose k candidate action
sequences* for an observation. :class:`PlannerNode` puts a named planner in a
graph.

World models are a **contract slot**, not a shipped component: a planner that
consumes a learned dynamics model plugs it in behind its own ``plan`` (and/or
behind :class:`PlannerNode`'s ``rollout_ref`` scoring seam, which accepts any
``fn(observation, actions, context) -> float`` -- a simulator rollout or a
learned model's rollout, indistinguishably).

Built-in reference implementation: ``mpc-ca-burn``
(:class:`stargraph.rl.planners.mpc.CaBurnMpcPlanner`), a convex-MPC-style
burn-option planner over the ARLO collision-avoidance geometry.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from stargraph.errors import RLNodeError
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

__all__ = ["CandidatePlan", "Planner", "PlannerNode", "load_planner"]

ENTRY_POINT_GROUP = "stargraph.planners"


@dataclass
class CandidatePlan:
    """One proposed action sequence. Higher ``score`` = preferred."""

    actions: list[int]
    score: float
    info: dict[str, Any] = field(default_factory=dict[str, Any])


@runtime_checkable
class Planner(Protocol):
    """The ``stargraph.planners`` contract: propose ``k`` candidate action sequences.

    ``observation`` is planner-defined (a raw env observation, or a richer
    record like ARLO's conjunction-event dict); ``context`` carries
    planner-specific configuration (e.g. ``{"expert_cfg": ...}``). Planners
    must be deterministic in ``(observation, k, context)`` -- stochastic
    search belongs behind a seeded, pinned context knob.
    """

    def plan(
        self, observation: Any, *, k: int, context: Mapping[str, Any]
    ) -> list[CandidatePlan]: ...


def load_planner(name: str) -> Planner:
    """Instantiate the planner registered under ``name`` in ``stargraph.planners``."""
    eps = entry_points(group=ENTRY_POINT_GROUP)
    try:
        ep = eps[name]
    except KeyError:
        raise RLNodeError(
            f"no planner {name!r} registered in the {ENTRY_POINT_GROUP!r} entry-point group",
            hint="register a Planner class under [project.entry-points.'stargraph.planners']",
            planner=name,
            available=sorted(e.name for e in eps),
        ) from None
    planner_cls: Any = ep.load()
    planner: Planner = planner_cls()
    return planner


class PlannerNode(NodeBase):
    """Graph node running a registered planner over a state observation.

    Reads ``state.<observation_field>`` (and, when present,
    ``state.<context_field>`` -- a dict), asks the planner for ``k``
    candidates, optionally re-scores them through a dotted ``rollout_ref``
    (``"pkg.mod:fn"``, ``fn(observation, actions, context) -> float`` -- an
    env rollout or a learned-dynamics rollout), and writes the ranked plans
    to ``state.<plans_field>`` as a list of ``CandidatePlan`` dicts.

    Construction is eager: an unknown planner name fails at definition time.
    """

    def __init__(
        self,
        *,
        planner: str,
        k: int = 3,
        observation_field: str = "observation",
        plans_field: str = "plans",
        context_field: str = "planner_context",
        rollout_ref: str | None = None,
    ) -> None:
        self.planner_name = planner
        self.k = k
        self.observation_field = observation_field
        self.plans_field = plans_field
        self.context_field = context_field
        self.rollout_ref = rollout_ref
        self._planner = load_planner(planner)
        if rollout_ref is None:
            self._rollout: Any = None
        else:
            from stargraph.rl.gauntlet.stations import resolve_ref

            self._rollout = resolve_ref(rollout_ref)

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        """Plan (and optionally rollout-score) in a worker thread; write plan dicts."""
        del ctx
        observation: Any = getattr(state, self.observation_field)
        raw_context: Any = getattr(state, self.context_field, None)
        context: dict[str, Any] = dict(raw_context) if raw_context else {}
        plans = await asyncio.to_thread(self._plan_and_score, observation, context)
        return {self.plans_field: [asdict(p) for p in plans]}

    def _plan_and_score(self, observation: Any, context: dict[str, Any]) -> list[CandidatePlan]:
        plans = self._planner.plan(observation, k=self.k, context=context)
        if self._rollout is None:
            return plans
        scored = [
            CandidatePlan(
                actions=list(p.actions),
                score=float(self._rollout(observation, list(p.actions), context)),
                info={**p.info, "planner_score": p.score},
            )
            for p in plans
        ]
        scored.sort(key=lambda p: p.score, reverse=True)
        return scored
