# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the planner extension point + the mpc-ca-burn reference planner.

``load_planner`` resolves through the real installed ``stargraph.planners``
entry-point group (the package registers ``mpc-ca-burn``); the planner's
geometry is the upstream port, so the assertions here pin *decision structure*
(fuel-minimality, encoding round-trip, hold-out honesty) on a hand-checkable
synthetic event, not re-derived orbital numbers.
"""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.errors import RLNodeError
from stargraph.rl.planners import CandidatePlan, Planner, PlannerNode, load_planner
from stargraph.rl.planners.mpc import CaBurnMpcPlanner, encode_action

pytestmark = pytest.mark.unit


# -- a hand-checkable conjunction event ------------------------------------

_CFG: dict[str, Any] = {
    "pc_threshold": 1e-4,
    "dv_bins_ms": [0.05, 0.1, 0.2, 0.4],
    "mean_motion_rad_s": 0.00113,  # ~LEO
    "hbr_m": 20.0,
}


def _tri21(diag: float) -> list[float]:
    """21-element upper-triangular 6x6 with ``diag`` on the diagonal."""
    tri = [0.0] * 21
    for idx in (0, 6, 11, 15, 18, 20):  # (i,i) positions in row-major upper-tri
        tri[idx] = diag
    return tri


def _event(budget: float = 5.0, reserve: float = 0.5) -> dict[str, Any]:
    """One event, 3 CDMs, upstream CDM-schema geometry chosen to be hand-checkable.

    50 m along-track miss with sigma = 20 m per axis (200 m^2 per object,
    combined 400): recorded operational pc 3e-3 sits ~2.5 sigma out, and any
    bin's CW along-track displacement over ~19 h dwarfs sigma, so even the
    smallest bin clears the 1e-4 threshold at every burn epoch.
    """
    cdms = [
        {
            "creation_epoch": float(k * 3600),
            "tca": 72_000.0,
            "pc": 3e-3,
            "rel_pos_rtn": [0.0, 50.0, 0.0],
            "rel_vel_rtn": [10_000.0, 0.0, 0.0],
            "cov_obj1": _tri21(200.0),
            "cov_obj2": _tri21(200.0),
        }
        for k in range(3)
    ]
    return {"cdms": cdms, "ownship": {"dv_budget_ms": budget, "dv_reserve_ms": reserve}}


# -- entry-point discovery -------------------------------------------------


def test_load_planner_resolves_registered_entry_point() -> None:
    planner = load_planner("mpc-ca-burn")
    assert isinstance(planner, CaBurnMpcPlanner)
    assert isinstance(planner, Planner)  # runtime_checkable protocol


def test_load_planner_unknown_name_lists_available() -> None:
    with pytest.raises(RLNodeError, match="no planner 'nope'") as err:
        load_planner("nope")
    assert "mpc-ca-burn" in err.value.context["available"]


# -- action encoding -------------------------------------------------------


def test_encode_action_is_inverse_of_upstream_decode() -> None:
    # Upstream decode_action: 1..K = +1 over bins, K+1..2K = -1 over bins.
    n_bins = 4
    assert encode_action(1, 0, n_bins) == 1
    assert encode_action(1, 3, n_bins) == 4
    assert encode_action(-1, 0, n_bins) == 5
    assert encode_action(-1, 3, n_bins) == 8


# -- the reference planner -------------------------------------------------


def test_mpc_plans_are_fuel_ranked_and_clear_threshold() -> None:
    plans = CaBurnMpcPlanner().plan(_event(), k=4, context={"expert_cfg": _CFG})
    assert 1 <= len(plans) <= 4
    for plan in plans:
        assert plan.info["pc_post"] < _CFG["pc_threshold"]
        assert plan.score == -plan.info["dv_ms"]
        # actions = hold until the burn CDM, then one burn action
        assert plan.actions[:-1] == [0] * plan.info["burn_cdm_index"]
        assert plan.actions[-1] >= 1
    scores = [p.score for p in plans]
    assert scores == sorted(scores, reverse=True)  # cheapest fuel first


def test_mpc_is_deterministic() -> None:
    ctx = {"expert_cfg": _CFG}
    a = CaBurnMpcPlanner().plan(_event(), k=3, context=ctx)
    b = CaBurnMpcPlanner().plan(_event(), k=3, context=ctx)
    assert a == b


def test_mpc_returns_explicit_holdout_when_nothing_clears() -> None:
    # Budget below the smallest bin: no admissible burn exists.
    plans = CaBurnMpcPlanner().plan(
        _event(budget=0.04, reserve=0.0), k=3, context={"expert_cfg": _CFG}
    )
    assert len(plans) == 1
    assert plans[0].actions == [0, 0, 0]
    assert "no admissible burn" in plans[0].info["reason"]


def test_mpc_requires_expert_cfg_loudly() -> None:
    with pytest.raises(RLNodeError, match="expert_cfg"):
        CaBurnMpcPlanner().plan(_event(), k=3, context={})


# -- PlannerNode -----------------------------------------------------------


def test_planner_node_unknown_planner_fails_at_definition_time() -> None:
    with pytest.raises(RLNodeError, match="no planner"):
        PlannerNode(planner="does-not-exist")


@pytest.mark.asyncio
async def test_planner_node_writes_ranked_plan_dicts() -> None:
    from pydantic import BaseModel

    class _State(BaseModel):
        observation: dict[str, Any]
        planner_context: dict[str, Any]
        plans: list[dict[str, Any]] = []

    class _Ctx:
        run_id = "run-planner-test"

    node = PlannerNode(planner="mpc-ca-burn", k=2)
    state = _State(observation=_event(), planner_context={"expert_cfg": _CFG})
    out = await node.execute(state, _Ctx())
    plans = out["plans"]
    assert 1 <= len(plans) <= 2
    assert set(plans[0]) == {"actions", "score", "info"}  # CandidatePlan asdict


@pytest.mark.asyncio
async def test_planner_node_rollout_ref_rescoring_reranks() -> None:
    from pydantic import BaseModel

    class _State(BaseModel):
        observation: dict[str, Any]
        planner_context: dict[str, Any]

    class _Ctx:
        run_id = "run-planner-test"

    # tests.fixtures.rl_doubles:rollout_prefers_late scores later burns higher,
    # inverting the planner's own fuel ranking -> proves the seam re-ranks.
    node = PlannerNode(
        planner="mpc-ca-burn",
        k=4,
        rollout_ref="tests.fixtures.rl_doubles:rollout_prefers_late",
    )
    out = await node.execute(
        _State(observation=_event(), planner_context={"expert_cfg": _CFG}), _Ctx()
    )
    plans = [CandidatePlan(**p) for p in out["plans"]]
    burn_cdms = [p.info["burn_cdm_index"] for p in plans]
    assert burn_cdms == sorted(burn_cdms, reverse=True)  # rollout ranking won
    assert all("planner_score" in p.info for p in plans)  # original kept
