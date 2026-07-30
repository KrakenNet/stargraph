# SPDX-License-Identifier: Apache-2.0
"""W7 ACCEPTANCE: reproduce the ARLO ppo-v4 admission refusal through the port.

The government-cited result: ARLO's admission gate REFUSED the ppo-v4 PPO
candidate on the real Kelvins admission split (``admitted=false``, n=2629,
candidate risk_rate ~5x the ops baseline for less total fuel improvement than
it costs, family PBO 4/70). This test mounts the *real* ARLO stack -- dataset
(``datasets/kelvins_train.parquet``), trained candidate
(``models/ppo-v4/``), expert config, F2 evaluator backend -- into the
Stargraph-ported gauntlet by running the reference eval graph end-to-end
(wall -> train(cached) -> gate -> halt-on-refusal, transitions decided by the
Fathom rule pack) and asserts the refusal reproduces number-for-number
against the frozen ``admission_report.json``.

Self-skips (importorskip/skipif, per ``test_export_ppo_v4.py`` conventions)
unless the heavy deps are importable and the ARLO checkout + assets exist;
point ``ARLO_DIR`` at a checkout to run it elsewhere. ARLO is a plain package
directory (its pyproject pins local path deps that don't resolve outside its
own workspace), so the checkout root is added to ``sys.path`` rather than
pip-installed -- the assets stay OUTSIDE this repo and nothing binary is
committed.

Run it from the repo root with the rl-extra environment, e.g.::

    UV_PROJECT_ENVIRONMENT=/tmp/venv-rl uv sync --group dev --extra rl
    /tmp/venv-rl/bin/python -m pytest tests/integration/rl/ -o addopts="" --runslow
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

# The arlo package is untyped and mounted from outside the repo (see module
# docstring) -- same waiver set as tests/integration/test_export_ppo_v4.py.
# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false

_ARLO_DIR = Path(os.environ.get("ARLO_DIR", "~/leagues/arlo")).expanduser()
_DATASET = _ARLO_DIR / "datasets" / "kelvins_train.parquet"
_MODEL_DIR = _ARLO_DIR / "models" / "ppo-v4"
_REPORT = _MODEL_DIR / "admission_report.json"

pytest.importorskip("numpy")
pytest.importorskip("scipy")
pytest.importorskip("pyarrow")
pytest.importorskip("gymnasium")
pytest.importorskip("torch")
pytest.importorskip("stable_baselines3")

if _ARLO_DIR.is_dir() and str(_ARLO_DIR) not in sys.path:
    sys.path.insert(0, str(_ARLO_DIR))
pytest.importorskip("arlo.config", reason=f"no importable arlo package under {_ARLO_DIR}")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.skipif(
        not (_DATASET.exists() and _REPORT.exists()),
        reason=f"ARLO assets missing under {_ARLO_DIR} (set ARLO_DIR)",
    ),
]


def make_family(candidate: Any, cfg: dict[str, Any]) -> list[Any]:
    """The exact config family from ``arlo/gauntlet/run_admission.py`` (verbatim)."""
    from arlo.gauntlet.run_admission import ThresholdVariant

    return [
        candidate,
        ThresholdVariant(cfg, cfg["pc_threshold"] * 0.3, cfg["decision_lead_days"], "tight"),
        ThresholdVariant(cfg, cfg["pc_threshold"] * 3.0, cfg["decision_lead_days"], "loose"),
        ThresholdVariant(cfg, cfg["pc_threshold"], cfg["decision_lead_days"] * 1.5, "early"),
    ]


def test_eval_graph_reproduces_ppo_v4_refusal(tmp_path: Path) -> None:
    """The full graph journey lands on the same refusal, number for number."""
    from typer.testing import CliRunner

    from stargraph.cli import app
    from stargraph.rl.gauntlet import eval_graph_path

    expected = json.loads(_REPORT.read_text())

    args = ["run", str(eval_graph_path()), "--checkpoint", str(tmp_path / "ck.sqlite")]
    for pair in (
        f"dataset_path={_DATASET}",
        f"model_dir={_MODEL_DIR}",
        "events_loader=arlo.envs.scenario_gen:replay",
        "expert_cfg_loader=arlo.config:load_expert_cfg",
        "env_factory=arlo.envs.ca_event_env:CaEventEnv",
        "gate_backend=arlo.envs.backends.j2mc",
        "candidate_loader=arlo.train.ppo_train:PPOPolicy.load",
        "baseline_factory=arlo.train.baseline_threshold:ThresholdPolicy",
        "config_family_factory=tests.integration.rl.test_arlo_admission_repro:make_family",
    ):
        args += ["--inputs", pair]
    args += ["--quiet", "--summary-json"]

    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON summary in output: {result.stdout!r}"
    payload = json.loads(lines[-1])
    state = payload["state_summary"]

    # -- journey shape: wall passed, candidate was cached, gate REFUSED ----
    assert payload["status"] == "done"
    assert state["wall_verdict"] == "pass"
    assert state["train_verdict"] == "cached"  # ppo-v4 ships policy_meta.json
    assert state["gate_verdict"] == "refused"
    assert state["shield_verdict"] == "pending"  # refusal halts before the shield
    assert state["candidate_id"] == expected["candidate"] == "ppo-ca-seed0-250000"

    # -- the refusal itself, against the frozen ARLO report ----------------
    assert expected["admitted"] is False
    assert state["gate_reasons"] == expected["reasons"]
    assert any(str(r).startswith("pareto:") for r in state["gate_reasons"])

    got = state["gate_metrics"]
    exp = expected["metrics"]
    # Counts and count-ratios are fully deterministic -> exact.
    assert state["n_admission_events"] == expected["n_admission_events"] == 2629
    for who in ("candidate", "baseline"):
        assert got[who]["n_events"] == exp[who]["n_events"] == 2629
        assert got[who]["risk_rate"] == exp[who]["risk_rate"]
        assert got[who]["maneuver_rate"] == exp[who]["maneuver_rate"]
        assert got[who]["false_maneuver_rate"] == exp[who]["false_maneuver_rate"]
    assert got["candidate"]["risk_rate"] == pytest.approx(0.0019018638, abs=1e-9)
    assert got["baseline"]["risk_rate"] == pytest.approx(0.0003803728, abs=1e-9)
    # Float accumulations -> tight tolerance.
    assert got["candidate"]["dv_total_ms"] == pytest.approx(exp["candidate"]["dv_total_ms"])
    assert got["baseline"]["dv_total_ms"] == pytest.approx(exp["baseline"]["dv_total_ms"])
    assert got["candidate"]["dv_total_ms"] == pytest.approx(0.14)
    assert got["baseline"]["dv_total_ms"] == pytest.approx(0.22)
    assert got["candidate"]["mean_reward"] == pytest.approx(exp["candidate"]["mean_reward"])
    assert got["baseline"]["mean_reward"] == pytest.approx(exp["baseline"]["mean_reward"])
    # CSCV-PBO is a ratio of combination counts (4/70) -> exact.
    assert got["pbo"] == exp["pbo"] == pytest.approx(0.05714285714285714, abs=1e-15)


def test_mpc_planner_node_on_held_out_conjunction() -> None:
    """PlannerNode + mpc-ca-burn end-to-end on one DEPLOY-split Kelvins event.

    The event is the riskiest held-out conjunction (max final-CDM operational
    Pc on the deploy partition -- data the ppo-v4 pipeline never trained or
    gated on). The planner must return fuel-ranked burn options whose
    projected post-burn operational Pc clears the maneuver threshold.
    """
    import asyncio

    from arlo.config import load_expert_cfg
    from arlo.envs.scenario_gen import replay
    from pydantic import BaseModel

    from stargraph.rl.gauntlet import three_way
    from stargraph.rl.planners import PlannerNode

    cfg = load_expert_cfg()
    events = replay(_DATASET)
    split = three_way(events)
    by_id = {e["event_id"]: e for e in events}
    deploy = [by_id[i] for i in split.deploy]
    event = max(deploy, key=lambda e: float(e["cdms"][-1]["pc"]))
    assert float(event["cdms"][-1]["pc"]) > cfg["pc_threshold"]  # a real conjunction

    class _State(BaseModel):
        observation: dict[str, Any]
        planner_context: dict[str, Any]

    class _Ctx:
        run_id = "run-mpc-acceptance"

    node = PlannerNode(planner="mpc-ca-burn", k=3)
    out = asyncio.run(
        node.execute(_State(observation=event, planner_context={"expert_cfg": cfg}), _Ctx())
    )
    plans = out["plans"]
    assert 1 <= len(plans) <= 3
    for plan in plans:
        info = plan["info"]
        assert info["pc_post"] < cfg["pc_threshold"]  # every option clears
        assert info["dv_ms"] in cfg["dv_bins_ms"]  # burns live on the dv grid
        assert plan["actions"][:-1] == [0] * info["burn_cdm_index"]  # hold, then burn
        budget = event["ownship"]["dv_budget_ms"] - event["ownship"]["dv_reserve_ms"]
        assert info["dv_ms"] <= budget
    scores = [p["score"] for p in plans]
    assert scores == sorted(scores, reverse=True)  # cheapest fuel first
