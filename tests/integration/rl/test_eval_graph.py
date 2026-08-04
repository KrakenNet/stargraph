# SPDX-License-Identifier: Apache-2.0
"""End-to-end runs of the reference RL eval graph (``eval-graph.yaml``).

The whole point of the graph shape: wall -> train -> gate -> shield with every
transition decided by the Fathom rule pack over mirrored ``*_verdict`` facts
(rules-not-edges), refusal a first-class terminal. Three journeys:

1. the full admitted path -- trainer runs, gate re-derives + binds the split,
   admits, shield approves, ``done`` halts;
2. a gate refusal -- the bad candidate halts the run at the gate rule with
   the refusal reason in the run output;
3. a wall refusal -- malformed data never reaches training.

All pluggable pieces are the deterministic doubles in
:mod:`tests.fixtures.rl_doubles`, injected as dotted references via
``--inputs`` (EvalState's contract) -- nothing here needs the rl extra.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from typer.testing import CliRunner

from stargraph.cli import app
from stargraph.rl.gauntlet import eval_graph_path

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

_DBL = "tests.fixtures.rl_doubles"


def _run(tmp_path: Path, *inputs: str) -> dict[str, Any]:
    args = ["run", str(eval_graph_path()), "--checkpoint", str(tmp_path / "ck.sqlite")]
    for pair in inputs:
        args += ["--inputs", pair]
    args += ["--quiet", "--summary-json"]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON summary in output: {result.stdout!r}"
    return json.loads(lines[-1])


def _base_inputs(*overrides: str) -> tuple[str, ...]:
    return (
        "dataset_path=double-dataset",
        f"events_loader={_DBL}:load_events",
        f"expert_cfg_loader={_DBL}:load_cfg",
        f"env_factory={_DBL}:TinyEventEnv",
        f"gate_backend={_DBL}:backend",
        f"candidate_loader={_DBL}:load_candidate_good",
        f"baseline_factory={_DBL}:make_baseline",
        f"config_family_factory={_DBL}:make_family",
        f"shield={_DBL}:make_shield",
        *overrides,
    )


def test_admitted_path_walks_all_four_stations(tmp_path: Path) -> None:
    payload = _run(
        tmp_path,
        *_base_inputs(
            f"model_dir={tmp_path / 'model'}",
            f"trainer={_DBL}:trainer_double",
            "shield_dv_ms=0.1",
        ),
    )
    assert payload["status"] == "done"
    state = payload["state_summary"]
    assert state["wall_verdict"] == "pass"
    assert state["train_verdict"] == "trained"
    assert state["gate_verdict"] == "admitted"
    assert state["shield_verdict"] == "approved"
    assert state["candidate_id"] == "double-trained"
    assert state["n_events"] == 40
    assert state["n_admission_events"] == 8
    # the trainer really left the split-bound candidate behind
    meta = json.loads((tmp_path / "model" / "policy_meta.json").read_text())
    assert len(meta["train_split_sha"]) == 64


def test_gate_refusal_halts_before_the_shield(tmp_path: Path) -> None:
    payload = _run(
        tmp_path,
        *_base_inputs(
            f"model_dir={tmp_path / 'model'}",
            f"trainer={_DBL}:trainer_double",
            f"candidate_loader={_DBL}:load_candidate_bad",
            "config_family_factory=",
        ),
    )
    assert payload["status"] == "done"  # a governed refusal is a *completed* run
    state = payload["state_summary"]
    assert state["train_verdict"] == "trained"
    assert state["gate_verdict"] == "refused"
    assert any(str(r).startswith("pareto:") for r in state["gate_reasons"])
    # halted at the gate rule: the shield never ran, its verdict never left
    # "pending"
    assert state["shield_verdict"] == "pending"


def test_wall_refusal_never_reaches_training(tmp_path: Path) -> None:
    payload = _run(
        tmp_path,
        *_base_inputs(
            f"events_loader={_DBL}:load_events_malformed",
            "dataset_path=malformed-dataset",
        ),
    )
    assert payload["status"] == "done"
    state = payload["state_summary"]
    assert state["wall_verdict"] == "refuse"
    assert any("malformed event" in str(r) for r in state["wall_reasons"])
    # halted at the wall rule: train/gate/shield never ran
    assert state["train_verdict"] == "pending"
    assert state["gate_verdict"] == "pending"
    assert state["shield_verdict"] == "pending"
