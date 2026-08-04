# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the ported gauntlet math (splits / PBO / metrics / gate).

The arithmetic under test is a port of the upstream gauntlet -- the library
behind the ppo-v4 admission refusal -- so these tests pin its *invariants*
(ordering, fail-closed defaults, Pareto truth table, refusal reasons).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stargraph.rl.gauntlet import (
    GateVerdict,
    aggregate,
    cscv_pbo,
    gate,
    materialize,
    pareto_beats,
    rollout,
    three_way,
)
from tests.fixtures import rl_doubles as dbl

pytestmark = pytest.mark.unit


# -- splits ----------------------------------------------------------------


def test_three_way_is_deterministic_and_time_ordered() -> None:
    events = dbl.make_events()
    split = three_way(events)
    assert split == three_way(list(reversed(events)))  # input order is irrelevant
    assert len(split.train) + len(split.admission) + len(split.deploy) == len(events)
    # partitions are disjoint and strictly ordered on the timeline
    by_id = {e["event_id"]: e for e in events}

    def first_epoch(event_id: str) -> float:
        return float(by_id[event_id]["cdms"][0]["creation_epoch"])

    assert max(map(first_epoch, split.train)) < min(map(first_epoch, split.admission))
    assert max(map(first_epoch, split.admission)) < min(map(first_epoch, split.deploy))


def test_three_way_rejects_bad_fracs_and_tiny_datasets() -> None:
    events = dbl.make_events()
    with pytest.raises(ValueError, match="sum to 1"):
        three_way(events, fracs=(0.5, 0.2, 0.2))
    with pytest.raises(ValueError, match="not enough events"):
        three_way(events[:2])


def test_materialize_pins_partition_shas(tmp_path: Path) -> None:
    events = dbl.make_events()
    split = three_way(events)
    materialize(events, split, tmp_path)
    for name, ids in (("train", split.train), ("admission", split.admission)):
        manifest = json.loads((tmp_path / name / "split.json").read_text())
        assert manifest["n_events"] == len(ids)
        assert len(manifest["sha256"]) == 64
    # deterministic: re-materializing yields byte-identical manifests
    again = tmp_path / "again"
    materialize(events, split, again)
    assert (again / "train" / "split.json").read_text() == (
        tmp_path / "train" / "split.json"
    ).read_text()


# -- cscv pbo --------------------------------------------------------------


def test_cscv_pbo_fails_closed() -> None:
    assert cscv_pbo([]) == 1.0
    assert cscv_pbo([[1.0] * 100]) == 1.0  # a lone config proves nothing
    assert cscv_pbo([[1.0, 2.0], [2.0, 1.0]]) == 1.0  # fewer events than blocks


def test_cscv_pbo_separates_robust_from_degenerate_families() -> None:
    # A dominates B in AND out of sample everywhere -> never overfit.
    a = [1.0, 2.0] * 20
    b = [-1.0, -2.0] * 20
    assert cscv_pbo([a, b]) == 0.0
    # Two identical streams: the IS winner can never beat its clone OOS -> 1.0.
    assert cscv_pbo([list(a), list(a)]) == 1.0


# -- metrics ---------------------------------------------------------------


def _cfg() -> dict[str, float]:
    return {"screening_threshold": dbl.THRESHOLD}


def test_aggregate_counts_risk_fuel_and_false_maneuvers() -> None:
    results = [
        (-0.1, {"pc_post": 0.9, "pc_noaction": 0.9, "maneuvered": False, "dv_spent_ms": 0.0}),
        (-0.2, {"pc_post": 0.2, "pc_noaction": 0.9, "maneuvered": True, "dv_spent_ms": 0.1}),
        (-0.3, {"pc_post": 0.1, "pc_noaction": 0.1, "maneuvered": True, "dv_spent_ms": 0.2}),
        (0.0, {"pc_post": 0.1, "pc_noaction": 0.1, "maneuvered": False, "dv_spent_ms": 0.0}),
    ]
    m = aggregate(results, _cfg())
    assert m["n_events"] == 4
    assert m["risk_rate"] == 1 / 4  # one event left above threshold
    assert m["dv_total_ms"] == pytest.approx(0.3)  # pyright: ignore[reportUnknownMemberType]
    assert m["maneuver_rate"] == 2 / 4
    assert m["false_maneuver_rate"] == 1 / 4  # the quiet event that burned anyway
    assert m["mean_reward"] == pytest.approx(-0.15)  # pyright: ignore[reportUnknownMemberType]


def test_pareto_truth_table() -> None:
    base = {"risk_rate": 0.1, "dv_total_ms": 1.0}
    assert pareto_beats({"risk_rate": 0.1, "dv_total_ms": 0.9}, base)  # same risk, less fuel
    assert pareto_beats({"risk_rate": 0.05, "dv_total_ms": 1.0}, base)  # less risk, same fuel
    assert not pareto_beats({"risk_rate": 0.1, "dv_total_ms": 1.0}, base)  # a tie is no win
    assert not pareto_beats({"risk_rate": 0.05, "dv_total_ms": 1.1}, base)  # risk-fuel trade
    assert not pareto_beats({"risk_rate": 0.2, "dv_total_ms": 0.5}, base)  # fuel-risk trade


def test_rollout_walks_every_event_through_the_episode_protocol() -> None:
    events = dbl.make_events(10)
    results = rollout(
        dbl.ThresholdDouble(dv_bin=1),
        events,
        dbl.load_cfg(),
        dbl.backend,
        env_factory=dbl.TinyEventEnv,
    )
    assert len(results) == len(events)
    hot = [info for _r, info in results if info["maneuvered"]]
    assert len(hot) == 2  # events 0 and 5 are hot in a 10-event slice
    assert all(info["pc_post"] < dbl.THRESHOLD for _r, info in results)


# -- admission gate --------------------------------------------------------


def _gate(candidate: object, family: list[object] | None = None) -> GateVerdict:
    events = dbl.make_events()
    split = three_way(events)
    by_id = {e["event_id"]: e for e in events}
    admission_events = [by_id[i] for i in split.admission]
    return gate(
        candidate,  # type: ignore[arg-type]
        admission_events,
        dbl.make_baseline({}),
        dbl.load_cfg(),
        family,  # type: ignore[arg-type]
        backend=dbl.backend,
        env_factory=dbl.TinyEventEnv,
    )


def test_gate_admits_a_pareto_improvement() -> None:
    verdict = _gate(dbl.load_candidate_good(Path()))
    assert verdict.admitted and verdict.reasons == []
    cand = verdict.metrics["candidate"]
    base = verdict.metrics["baseline"]
    assert cand["risk_rate"] == base["risk_rate"] == 0.0
    assert cand["dv_total_ms"] < base["dv_total_ms"]


def test_gate_refuses_non_improvement_with_explicit_reason() -> None:
    verdict = _gate(dbl.load_candidate_bad(Path()))
    assert not verdict.admitted
    assert any(r.startswith("pareto:") for r in verdict.reasons)


def test_gate_refuses_candidate_trained_on_the_gate_backend() -> None:
    verdict = _gate(dbl.GateTrainedDouble())
    assert not verdict.admitted
    assert verdict.reasons == [
        f"toolchain: candidate trained on the gate backend {dbl.backend.IMPL_ID}"
    ]
    assert verdict.metrics == {}  # refused before any rollout ran


def test_gate_refuses_degenerate_config_family_via_pbo() -> None:
    candidate = dbl.load_candidate_good(Path())
    clone = dbl.ThresholdDouble(dv_bin=1, policy_id="clone")  # identical behavior
    verdict = _gate(candidate, family=[candidate, clone])
    assert not verdict.admitted
    assert verdict.metrics["pbo"] == 1.0
    assert any(r.startswith("pbo:") for r in verdict.reasons)


def test_gate_family_pbo_reported_when_it_clears() -> None:
    candidate = dbl.load_candidate_good(Path())
    verdict = _gate(candidate, family=dbl.make_family(candidate, {}))
    assert verdict.admitted
    assert 0.0 <= verdict.metrics["pbo"] <= 0.5
