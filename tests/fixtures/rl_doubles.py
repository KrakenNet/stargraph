# SPDX-License-Identifier: Apache-2.0
"""Deterministic doubles for the RL gauntlet + eval graph (no numpy, no RNG).

A miniature conjunction-shaped world satisfying every gauntlet seam
(``EventPolicy`` / ``Backend`` / ``EnvFactory`` and the stations' dotted-ref
contracts) with arithmetic simple enough to reason about in a test:

* 40 events, 3 CDMs each; every 5th event is "hot" (final pc 0.9 > threshold
  0.5), the rest quiet (0.1).
* Any burn scales the no-action pc by ``backend.RATIO`` (0.4), so a burn
  always clears a hot event; dv spent = the bin picked.
* ``ThresholdDouble(dv_bin)`` burns on hot events only. Small-bin (0.1)
  vs large-bin (0.2) makes Pareto admission decidable by hand: same risk,
  less fuel -> admitted.

Referenced by dotted names from the eval-graph integration test, e.g.
``events_loader=tests.fixtures.rl_doubles:load_events``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from pathlib import Path

N_EVENTS = 40
HOT_EVERY = 5
PC_HOT = 0.9
PC_QUIET = 0.1
THRESHOLD = 0.5
RATIO = 0.4
DV_BINS = [0.1, 0.2, 0.3]  # action 1 / 2 / 3


def make_events(n: int = N_EVENTS) -> list[dict[str, Any]]:
    """Deterministic event list (timeline-ordered, ARLO-shaped)."""
    events: list[dict[str, Any]] = []
    for i in range(n):
        hot = i % HOT_EVERY == 0
        final_pc = PC_HOT if hot else PC_QUIET
        cdms = [
            {
                "creation_epoch": float(i * 100 + k),
                "tca": float(i * 100 + 10),
                "pc": final_pc if k == 2 else PC_QUIET,
            }
            for k in range(3)
        ]
        events.append(
            {
                "event_id": f"double-{i:03d}",
                "cdms": cdms,
                "ownship": {"dv_budget_ms": 1.0, "dv_reserve_ms": 0.1},
            }
        )
    return events


def load_events(path: Path) -> list[dict[str, Any]]:
    """Stations' ``events_loader`` double -- the path is accepted and ignored."""
    del path
    return make_events()


def load_events_malformed(path: Path) -> list[dict[str, Any]]:
    """Wall double: valid events plus one missing ``tca`` and one with no cdms."""
    del path
    events = make_events(6)
    del events[1]["cdms"][0]["tca"]
    events[2]["cdms"] = []
    return events


def load_events_raises(path: Path) -> list[dict[str, Any]]:
    """Wall double: loader that blows up (fail-closed path)."""
    raise OSError(f"cannot read {path}")


def load_cfg() -> dict[str, Any]:
    """Stations' ``expert_cfg_loader`` double."""
    return {
        "version": "double-v0",
        "pc_threshold": THRESHOLD,
        "screening_threshold": THRESHOLD,
        "dv_bins_ms": list(DV_BINS),
    }


class _Backend:
    IMPL_ID: str = "double-f2"
    RATIO: float = RATIO


backend = _Backend()


class TinyEventEnv:
    """EpisodeEnv double: hold to advance CDMs; any burn ends the episode."""

    def __init__(
        self, events: list[dict[str, Any]], expert_cfg: dict[str, Any], backend: Any
    ) -> None:
        self.events = events
        self.cfg = expert_cfg
        self.backend = backend
        self._event: dict[str, Any] | None = None
        self._k = 0

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, Any]]:
        del seed
        idx = (options or {}).get("scenario_index", 0)
        self._event = self.events[idx]
        self._k = 0
        return self._obs(), self._info()

    def step(self, action: int) -> tuple[Any, float, bool, bool, dict[str, Any]]:
        assert self._event is not None
        cdms = self._event["cdms"]
        if action == 0:
            if self._k + 1 < len(cdms):
                self._k += 1
                return self._obs(), 0.0, False, False, self._info()
            return self._terminal(dv=None)
        return self._terminal(dv=DV_BINS[action - 1])

    def _obs(self) -> float:
        assert self._event is not None
        return float(self._event["cdms"][self._k]["pc"])

    def _info(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self._event is not None
        info: dict[str, Any] = {
            "event_id": self._event["event_id"],
            "pc_current": self._event["cdms"][self._k]["pc"],
            "final_pc": self._event["cdms"][-1]["pc"],
            "maneuvered": False,
        }
        if extra:
            info.update(extra)
        return info

    def _terminal(self, dv: float | None) -> tuple[Any, float, bool, bool, dict[str, Any]]:
        assert self._event is not None
        pc_noaction = float(self._event["cdms"][-1]["pc"])
        if dv is None:
            maneuvered, dv_spent, pc_post = False, 0.0, pc_noaction
        else:
            maneuvered, dv_spent, pc_post = True, dv, pc_noaction * self.backend.RATIO
        reward = -pc_post - 0.05 * dv_spent
        info = self._info(
            {
                "maneuvered": maneuvered,
                "dv_spent_ms": dv_spent,
                "pc_post": pc_post,
                "pc_noaction": pc_noaction,
            }
        )
        return self._obs(), reward, True, False, info


class ThresholdDouble:
    """Burn ``dv_bin`` when the CURRENT pc breaches the threshold; else hold."""

    def __init__(self, dv_bin: int, policy_id: str = "threshold-double") -> None:
        self.dv_bin = dv_bin
        self.policy_id = policy_id
        self.trained_backends: list[str] = ["double-f0"]

    def act(self, obs: Any, info: dict[str, Any]) -> int:
        del obs
        if info["pc_current"] > THRESHOLD:
            return self.dv_bin
        return 0


class AlwaysBurnDouble:
    """Burns the largest bin immediately on every event (fuel-wasteful)."""

    policy_id = "always-burn-double"
    trained_backends: ClassVar[list[str]] = ["double-f0"]

    def act(self, obs: Any, info: dict[str, Any]) -> int:
        del obs, info
        return 3


class GateTrainedDouble(ThresholdDouble):
    """Poisoned toolchain: trained on the gate backend (must be refused)."""

    def __init__(self) -> None:
        super().__init__(dv_bin=1, policy_id="gate-trained-double")
        self.trained_backends = [backend.IMPL_ID]


# -- dotted-ref factories for the stations ---------------------------------


def load_candidate_good(model_dir: Path) -> ThresholdDouble:
    """Small-bin threshold policy: same risk as baseline, less fuel -> admitted."""
    del model_dir
    return ThresholdDouble(dv_bin=1, policy_id="good-double")


def load_candidate_bad(model_dir: Path) -> AlwaysBurnDouble:
    """Burns everything: no Pareto improvement over the baseline -> refused."""
    del model_dir
    return AlwaysBurnDouble()


def make_baseline(cfg: dict[str, Any]) -> ThresholdDouble:
    """Large-bin ops baseline."""
    del cfg
    return ThresholdDouble(dv_bin=2, policy_id="baseline-double")


def make_family(candidate: Any, cfg: dict[str, Any]) -> list[Any]:
    """Candidate + distinct siblings (a healthy, non-degenerate family)."""
    del cfg
    return [candidate, ThresholdDouble(dv_bin=2, policy_id="fam-mid"), AlwaysBurnDouble()]


def trainer_double(
    train_events: list[dict[str, Any]], cfg: dict[str, Any], model_dir: Path, timesteps: int
) -> None:
    """TrainStation double: records the canonical train-split sha like ARLO's trainer."""
    del cfg, timesteps
    model_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(json.dumps(train_events, sort_keys=True).encode()).hexdigest()
    (model_dir / "policy_meta.json").write_text(
        json.dumps({"policy_id": "double-trained", "train_split_sha": sha})
    )


def trainer_raises(
    train_events: list[dict[str, Any]], cfg: dict[str, Any], model_dir: Path, timesteps: int
) -> None:
    """TrainStation double: training run that dies (fail-closed path)."""
    del train_events, cfg, model_dir, timesteps
    raise RuntimeError("gpu on fire")


# -- shield double (arlo-style rule dataclasses, evaluated deterministically) --


@dataclass
class DoubleShieldVerdict:
    approved: bool
    facts: dict[str, Any] = field(default_factory=dict[str, Any])
    reasons: list[str] = field(default_factory=list[str])


class DoubleShield:
    """Two deterministic gates over the recommendation, ARLO ``ShieldVerdict`` shape."""

    def __init__(self, cfg: dict[str, Any], backend: Any) -> None:
        self.cfg = cfg
        self.backend = backend

    def evaluate(self, rec: Any, ctx: dict[str, Any]) -> DoubleShieldVerdict:
        budget = float(ctx["event"]["ownship"]["dv_budget_ms"])
        reserve = float(ctx["event"]["ownship"]["dv_reserve_ms"])
        facts = {
            "fuel_floor_ok": bool(budget - float(rec.dv_ms) >= reserve),
            "direction_valid": rec.direction in (1, -1),
        }
        reasons = [f"{g}=false" for g, ok in facts.items() if not ok]
        return DoubleShieldVerdict(approved=not reasons, facts=facts, reasons=reasons)


def make_shield(cfg: dict[str, Any], backend: Any) -> DoubleShield:
    return DoubleShield(cfg, backend)


class _RaisingShield:
    def evaluate(self, rec: Any, ctx: dict[str, Any]) -> DoubleShieldVerdict:
        del rec, ctx
        raise RuntimeError("shield evaluator crashed")


def make_shield_raises(cfg: dict[str, Any], backend: Any) -> _RaisingShield:
    """ShieldStation double: evaluator that blows up (fail-closed path)."""
    del cfg, backend
    return _RaisingShield()


# -- planner rollout double -------------------------------------------------


def rollout_prefers_late(observation: Any, actions: list[int], context: dict[str, Any]) -> float:
    """PlannerNode ``rollout_ref`` double: score = leading holds (later burn wins).

    Deliberately inverts the MPC planner's own earlier-burn tie-break so the
    re-ranking seam is observable.
    """
    del observation, context
    return float(len(actions) - 1)
