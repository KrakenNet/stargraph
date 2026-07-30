# SPDX-License-Identifier: Apache-2.0
"""Policy evaluation + the Pareto admission criterion: a candidate is only an
improvement if it does no worse on risk AND strictly better on fuel, or
strictly better on risk at no more fuel. Outcomes are judged by the backend
passed in -- the gate passes the evaluator toolchain, never the training one.

Ported from ARLO (``arlo/gauntlet/metrics.py``); the aggregation and Pareto
arithmetic are intentionally IDENTICAL. The single adaptation: ARLO's hard
``CaEventEnv`` import became the :data:`EnvFactory` seam, so any env honoring
the event-episode protocol (gymnasium 5-tuple ``step``, ``reset`` with
``options={"scenario_index": i}``, terminal ``info`` carrying ``pc_post`` /
``pc_noaction`` / ``maneuvered`` / ``dv_spent_ms``) plugs in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from stargraph.rl.gauntlet.splits import Event


@runtime_checkable
class EventPolicy(Protocol):
    """Anything with ``act(obs, info) -> int`` (ARLO's policy calling convention)."""

    def act(self, obs: Any, info: dict[str, Any]) -> int: ...


@runtime_checkable
class Backend(Protocol):
    """An outcome toolchain (module-like): identified by ``IMPL_ID``."""

    IMPL_ID: str


class EpisodeEnv(Protocol):
    """The event-episode env protocol :func:`rollout` drives (gymnasium-shaped)."""

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, Any]]: ...

    def step(self, action: int) -> tuple[Any, float, bool, bool, dict[str, Any]]: ...


type EnvFactory = Callable[[list[Event], dict[str, Any], Backend], EpisodeEnv]
"""Builds an :class:`EpisodeEnv` over ``(events, expert_cfg, backend)`` -- the seam
that replaced ARLO's hard ``CaEventEnv`` import (``CaEventEnv`` itself satisfies it)."""


def rollout(
    policy: EventPolicy,
    events: list[Event],
    expert_cfg: dict[str, Any],
    backend: Backend,
    *,
    env_factory: EnvFactory,
) -> list[tuple[float, dict[str, Any]]]:
    """One (terminal reward, terminal info) per event, in event order -- the single
    episode protocol every consumer (Pareto metrics, PBO utility streams) shares."""
    env = env_factory(events, expert_cfg, backend)
    results: list[tuple[float, dict[str, Any]]] = []
    for i in range(len(events)):
        obs, info = env.reset(options={"scenario_index": i})
        while True:
            obs, reward, terminated, truncated, info = env.step(policy.act(obs, info))
            if terminated or truncated:
                break
        results.append((reward, info))
    return results


def aggregate(
    results: list[tuple[float, dict[str, Any]]], expert_cfg: dict[str, Any]
) -> dict[str, Any]:
    """Fold terminal infos into the admission metrics dict (risk/fuel/burden rates)."""
    thresh = expert_cfg["screening_threshold"]
    missed = 0
    dv_total = 0.0
    false_maneuvers = 0
    maneuvers = 0
    for _reward, info in results:
        if info["pc_post"] > thresh:
            missed += 1
        if info["maneuvered"]:
            maneuvers += 1
            dv_total += info["dv_spent_ms"]
            if info["pc_noaction"] < thresh:
                false_maneuvers += 1
    n = len(results)
    return {
        "n_events": n,
        "risk_rate": missed / n,
        "dv_total_ms": dv_total,
        "maneuver_rate": maneuvers / n,
        "false_maneuver_rate": false_maneuvers / n,
        "mean_reward": sum(r for r, _ in results) / n,
    }


def evaluate(
    policy: EventPolicy,
    events: list[Event],
    expert_cfg: dict[str, Any],
    backend: Backend,
    *,
    env_factory: EnvFactory,
) -> dict[str, Any]:
    """:func:`rollout` + :func:`aggregate` in one call."""
    return aggregate(
        rollout(policy, events, expert_cfg, backend, env_factory=env_factory), expert_cfg
    )


def pareto_beats(candidate: dict[str, Any], baseline: dict[str, Any]) -> bool:
    """True iff the candidate Pareto-dominates the baseline on (risk, fuel)."""
    risk_c, risk_b = candidate["risk_rate"], baseline["risk_rate"]
    fuel_c, fuel_b = candidate["dv_total_ms"], baseline["dv_total_ms"]
    return (risk_c <= risk_b and fuel_c < fuel_b) or (risk_c < risk_b and fuel_c <= fuel_b)
