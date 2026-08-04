# SPDX-License-Identifier: Apache-2.0
"""``mpc-ca-burn`` -- convex-MPC-style burn-option planner over the CA burn problem.

The reference :class:`~stargraph.rl.planners.Planner` implementation. The
burn-option problem (the upstream design, which pairs RL with convex
optimization): given a recorded conjunction event, choose *when* to burn
(which CDM epoch), *which way* (+/- along-track) and *how hard* (a dv bin)
to minimize fuel subject to the post-burn collision probability clearing the
maneuver threshold.

Structure, MPC-style: enumerate the (burn epoch x direction) option lattice;
per option pick the fuel-minimal admissible impulse -- the objective is
linear in dv and post-burn Pc is monotone along the CW displacement ray in
the admissible regime, so the smallest clearing bin on the dv grid is the
per-option optimum (the same smallest-clearing-bin selection the upstream ops
baseline uses); rank options by fuel cost. Dynamics and Pc come verbatim
from the ported geometry (:mod:`~stargraph.rl.planners._ca_geometry` --
closed-form Clohessy-Wiltshire response + Foster encounter-plane Pc), NOT
invented here.

Honest scope notes:

* This is an **offline option study over the full recorded event** -- unlike
  the env's lookahead-firewalled policies, the planner sees every CDM. Use
  it for burn-option analysis / candidate generation, not as a deployable
  policy.
* It reasons on the F0 (training-side) geometry; judged outcomes belong to
  an independent evaluator backend (the upstream toolchain split). Re-score plans
  through :class:`~stargraph.rl.planners.PlannerNode`'s ``rollout_ref`` seam
  against the evaluator env for judged rankings.

``observation`` contract: an upstream-shaped event dict
(``{"cdms": [...], "ownship": {"dv_budget_ms": ..., "dv_reserve_ms": ...}}``);
``context`` must carry ``{"expert_cfg": {...}}`` with ``pc_threshold`` /
``dv_bins_ms`` / ``mean_motion_rad_s`` / ``hbr_m``.

Action encoding is the upstream ``Discrete(1 + 2K)``: ``0`` = hold/advance;
``1..2K`` = burn now, ``+1`` block then ``-1`` block over the dv bins
(inverse of the upstream env's ``decode_action``). A plan is therefore
``[0] * burn_cdm_index + [burn_action]``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from stargraph.errors import RLNodeError
from stargraph.rl.planners import CandidatePlan
from stargraph.rl.planners import _ca_geometry as geo

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["CaBurnMpcPlanner", "encode_action"]


def encode_action(direction: int, bin_index: int, n_bins: int) -> int:
    """Inverse of the upstream ``decode_action``: (direction, dv-bin index) -> Discrete id."""
    return 1 + (0 if direction == 1 else n_bins) + bin_index


class CaBurnMpcPlanner:
    """Fuel-minimal burn-option planner (see module docstring). Deterministic."""

    def plan(self, observation: Any, *, k: int, context: Mapping[str, Any]) -> list[CandidatePlan]:
        """Propose up to ``k`` clearing burn plans, cheapest fuel first.

        When no option clears the threshold inside the fuel budget, returns
        the single hold-out plan (never burn) with the reason in ``info`` --
        an explicit "no admissible burn" answer, not an empty list.
        """
        raw_cfg: Any = context.get("expert_cfg")
        if not isinstance(raw_cfg, dict):
            raise RLNodeError(
                "mpc-ca-burn requires context={'expert_cfg': {...}}",
                planner="mpc-ca-burn",
            )
        cfg = cast("dict[str, Any]", raw_cfg)
        event: dict[str, Any] = observation
        cdms: list[dict[str, Any]] = event["cdms"]
        ownship: dict[str, Any] = event["ownship"]
        bins: list[float] = list(cfg["dv_bins_ms"])
        n: float = float(cfg["mean_motion_rad_s"])
        hbr: float = float(cfg["hbr_m"])
        thresh: float = float(cfg["pc_threshold"])
        budget = float(ownship["dv_budget_ms"]) - float(ownship.get("dv_reserve_ms", 0.0))

        options: list[CandidatePlan] = []
        for i, cdm in enumerate(cdms):
            dt = float(cdm["tca"]) - float(cdm["creation_epoch"])
            for direction in (1, -1):
                # Fuel-minimal admissible impulse for this (epoch, direction):
                # smallest dv bin whose post-burn operational Pc clears the
                # threshold (cdm.pc * geometric post/pre ratio -- the upstream
                # judge convention).
                for b_idx, dv in enumerate(bins):
                    if dv > budget:
                        break  # bins are ascending; nothing larger fits either
                    shift = geo.maneuver_shift(dv, direction, dt, n)
                    pc_post = float(cdm["pc"]) * geo.pc_ratio(cdm, hbr, shift)
                    if pc_post < thresh:
                        options.append(
                            CandidatePlan(
                                actions=[0] * i + [encode_action(direction, b_idx, len(bins))],
                                score=-dv,  # fuel-minimizing: higher score = less fuel
                                info={
                                    "burn_cdm_index": i,
                                    "direction": direction,
                                    "dv_ms": dv,
                                    "pc_pre": float(cdm["pc"]),
                                    "pc_post": pc_post,
                                },
                            )
                        )
                        break

        if not options:
            return [
                CandidatePlan(
                    actions=[0] * len(cdms),
                    score=0.0,
                    info={"reason": "no admissible burn clears pc_threshold within budget"},
                )
            ]
        # Cheapest fuel first; earlier burn breaks ties (more margin to react).
        options.sort(key=lambda p: (-p.score, p.info["burn_cdm_index"]))
        return options[:k]
