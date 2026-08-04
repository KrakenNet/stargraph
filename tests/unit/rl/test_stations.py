# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the eval-graph stations (wall / train / gate / shield).

Stations are driven directly (``await station.execute(state, ctx)``) against
the deterministic doubles in :mod:`tests.fixtures.rl_doubles`; the rule-routed
end-to-end run of ``eval-graph.yaml`` lives in
``tests/integration/rl/test_eval_graph.py``. The invariants pinned here are
the upstream postures the port must keep: default = refusal, evaluation failures
fail CLOSED (a verdict, never a silent pass), misconfiguration fails LOUD
(:class:`~stargraph.errors.RLNodeError`), and the gate binds the candidate's
train-split sha to its OWN re-derived split.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from stargraph.errors import RLNodeError
from stargraph.rl.gauntlet.eval_state import EvalState
from stargraph.rl.gauntlet.stations import (
    GateStation,
    ShieldStation,
    TrainStation,
    WallStation,
    resolve_ref,
)
from tests.fixtures import rl_doubles as dbl

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit

_DBL = "tests.fixtures.rl_doubles"


class _Ctx:
    """Duck-typed execution context (the stations read nothing from it)."""

    run_id = "run-rl-test"


def _state(**overrides: Any) -> EvalState:
    """EvalState fully wired to the doubles; override per test."""
    defaults: dict[str, Any] = {
        "dataset_path": "ignored-by-doubles",
        "events_loader": f"{_DBL}:load_events",
        "expert_cfg_loader": f"{_DBL}:load_cfg",
        "env_factory": f"{_DBL}:TinyEventEnv",
        "gate_backend": f"{_DBL}:backend",
        "candidate_loader": f"{_DBL}:load_candidate_good",
        "baseline_factory": f"{_DBL}:make_baseline",
        "config_family_factory": f"{_DBL}:make_family",
    }
    defaults.update(overrides)
    return EvalState(**defaults)


# -- resolve_ref -----------------------------------------------------------


def test_resolve_ref_module_attr_and_bare_module() -> None:
    assert resolve_ref(f"{_DBL}:THRESHOLD") == dbl.THRESHOLD
    assert resolve_ref(_DBL) is dbl  # bare module = module-shaped backend
    assert resolve_ref(f"{_DBL}:backend.IMPL_ID") == "double-f2"  # attr path


def test_resolve_ref_misconfiguration_is_loud() -> None:
    with pytest.raises(RLNodeError, match="cannot import"):
        resolve_ref("no.such.module:thing")
    with pytest.raises(RLNodeError, match="no attribute"):
        resolve_ref(f"{_DBL}:no_such_attr")
    with pytest.raises(RLNodeError, match="empty module path"):
        resolve_ref(":attr")


# -- wall ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wall_passes_clean_dataset() -> None:
    out = await WallStation().execute(_state(), _Ctx())
    assert out["wall_verdict"] == "pass"
    assert out["wall_reasons"] == []
    assert out["n_events"] == dbl.N_EVENTS


@pytest.mark.asyncio
async def test_wall_refuses_malformed_events_naming_them() -> None:
    out = await WallStation().execute(
        _state(events_loader=f"{_DBL}:load_events_malformed", dataset_path="malformed"),
        _Ctx(),
    )
    assert out["wall_verdict"] == "refuse"
    named = " ".join(out["wall_reasons"])
    assert "double-001" in named and "double-002" in named


@pytest.mark.asyncio
async def test_wall_fails_closed_when_loader_raises() -> None:
    out = await WallStation().execute(
        _state(events_loader=f"{_DBL}:load_events_raises", dataset_path="raises"),
        _Ctx(),
    )
    assert out["wall_verdict"] == "refuse"
    assert any("fail-closed" in r for r in out["wall_reasons"])


@pytest.mark.asyncio
async def test_wall_raises_loud_on_unresolvable_loader() -> None:
    with pytest.raises(RLNodeError):  # misconfiguration, NOT a refusal verdict
        await WallStation().execute(
            _state(events_loader="no.such.module:loader", dataset_path="x"), _Ctx()
        )


# -- train -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_train_reports_cached_when_meta_exists(tmp_path: Path) -> None:
    (tmp_path / "policy_meta.json").write_text("{}")
    out = await TrainStation().execute(_state(model_dir=str(tmp_path)), _Ctx())
    assert out["train_verdict"] == "cached"


@pytest.mark.asyncio
async def test_train_refuses_without_candidate_or_trainer() -> None:
    out = await TrainStation().execute(_state(model_dir=""), _Ctx())
    assert out["train_verdict"] == "refuse"


@pytest.mark.asyncio
async def test_train_runs_trainer_on_train_partition_only(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    out = await TrainStation().execute(
        _state(model_dir=str(model_dir), trainer=f"{_DBL}:trainer_double"), _Ctx()
    )
    assert out["train_verdict"] == "trained"
    meta = json.loads((model_dir / "policy_meta.json").read_text())
    assert meta["policy_id"] == "double-trained"
    assert len(meta["train_split_sha"]) == 64


@pytest.mark.asyncio
async def test_train_fails_closed_when_trainer_raises(tmp_path: Path) -> None:
    out = await TrainStation().execute(
        _state(model_dir=str(tmp_path / "m"), trainer=f"{_DBL}:trainer_raises"), _Ctx()
    )
    assert out["train_verdict"] == "refuse"
    assert any("gpu on fire" in r for r in out["train_reasons"])


# -- gate ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_admits_good_candidate_with_metrics() -> None:
    out = await GateStation().execute(_state(), _Ctx())
    assert out["gate_verdict"] == "admitted"
    assert out["candidate_id"] == "good-double"
    assert (
        out["gate_metrics"]["candidate"]["dv_total_ms"]
        < (out["gate_metrics"]["baseline"]["dv_total_ms"])
    )
    # 40 events * 0.2 admission fraction
    assert out["n_admission_events"] == 8


@pytest.mark.asyncio
async def test_gate_refuses_bad_candidate_with_reason() -> None:
    out = await GateStation().execute(
        _state(
            candidate_loader=f"{_DBL}:load_candidate_bad",
            config_family_factory="",
        ),
        _Ctx(),
    )
    assert out["gate_verdict"] == "refused"
    assert any(r.startswith("pareto:") for r in out["gate_reasons"])


@pytest.mark.asyncio
async def test_gate_binds_split_sha_and_refuses_mismatch(tmp_path: Path) -> None:
    (tmp_path / "policy_meta.json").write_text(
        json.dumps({"policy_id": "tampered", "train_split_sha": "0" * 64})
    )
    out = await GateStation().execute(_state(model_dir=str(tmp_path)), _Ctx())
    assert out["gate_verdict"] == "refused"
    assert any("split-binding" in r for r in out["gate_reasons"])
    assert out["candidate_id"] == "tampered"


@pytest.mark.asyncio
async def test_gate_accepts_sha_recorded_by_its_own_trainer(tmp_path: Path) -> None:
    # trainer_double records the canonical train-partition sha, exactly like
    # the upstream trainer; the gate re-derives the split and must agree.
    model_dir = tmp_path / "model"
    await TrainStation().execute(
        _state(model_dir=str(model_dir), trainer=f"{_DBL}:trainer_double"), _Ctx()
    )
    out = await GateStation().execute(_state(model_dir=str(model_dir)), _Ctx())
    assert out["gate_verdict"] == "admitted"
    assert out["candidate_id"] == "double-trained"  # meta identity wins


@pytest.mark.asyncio
async def test_gate_max_admission_events_truncates() -> None:
    out = await GateStation().execute(_state(max_admission_events=3), _Ctx())
    assert out["n_admission_events"] == 3


# -- shield ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_shield_approves_within_fuel_reserve() -> None:
    out = await ShieldStation().execute(
        _state(shield=f"{_DBL}:make_shield", shield_dv_ms=0.1, shield_direction=1),
        _Ctx(),
    )
    assert out["shield_verdict"] == "approved"
    assert out["shield_facts"] == {"fuel_floor_ok": True, "direction_valid": True}


@pytest.mark.asyncio
async def test_shield_refuses_burn_that_breaks_fuel_floor() -> None:
    out = await ShieldStation().execute(
        _state(shield=f"{_DBL}:make_shield", shield_dv_ms=0.95, shield_direction=1),
        _Ctx(),
    )
    assert out["shield_verdict"] == "refused"
    assert out["shield_reasons"] == ["fuel_floor_ok=false"]


@pytest.mark.asyncio
async def test_shield_fails_closed_when_evaluator_raises() -> None:
    out = await ShieldStation().execute(
        _state(shield=f"{_DBL}:make_shield_raises", shield_dv_ms=0.1), _Ctx()
    )
    assert out["shield_verdict"] == "refused"
    assert out["shield_facts"] == {"fail_closed": True}
