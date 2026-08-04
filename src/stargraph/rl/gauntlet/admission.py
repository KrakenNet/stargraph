# SPDX-License-Identifier: Apache-2.0
"""The admission gate: candidate vs baseline on the ADMISSION split, judged by
the evaluator toolchain. Refusal reasons are explicit; default is refusal.
When a config family is supplied, CSCV-PBO must clear the bar too -- a lone
winning config with overfit siblings is how backtest overfitting sneaks in.

Ported from the upstream collision-avoidance governed-RL pipeline; the
toolchain check, the Pareto criterion, the shared-rollout PBO stream and
``PBO_MAX`` are intentionally IDENTICAL to the upstream implementation --
this gate produced the ppo-v4 REFUSED verdict. Adaptations: imports/typing;
``backend`` lost its upstream-local ``j2mc`` default and is now a required
keyword (Stargraph ships no toolchain of its own); the ``env_factory`` seam
is threaded through.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from stargraph.rl.gauntlet import metrics, pbo

if TYPE_CHECKING:
    from stargraph.rl.gauntlet.metrics import Backend, EnvFactory, EventPolicy
    from stargraph.rl.gauntlet.splits import Event

PBO_MAX = 0.5


@dataclass
class GateVerdict:
    """Gate outcome: ``admitted`` iff ``reasons`` is empty; ``metrics`` carries
    the candidate/baseline aggregates (and ``pbo`` when a family was gated)."""

    admitted: bool
    reasons: list[str] = field(default_factory=list[str])
    metrics: dict[str, Any] = field(default_factory=dict[str, Any])


def gate(
    candidate: EventPolicy,
    admission_events: list[Event],
    baseline: EventPolicy,
    expert_cfg: dict[str, Any],
    config_family: list[EventPolicy] | None = None,
    *,
    backend: Backend,
    env_factory: EnvFactory,
) -> GateVerdict:
    """Gate ``candidate`` against ``baseline`` on the admission split.

    Refuses when the candidate was trained on the gate backend (toolchain
    split: generator never sole evaluator), when it fails the Pareto
    criterion, or when the config family's CSCV-PBO exceeds ``PBO_MAX``.
    """
    trained: set[str] = set(getattr(candidate, "trained_backends", []))
    if backend.IMPL_ID in trained:
        return GateVerdict(
            False,
            [f"toolchain: candidate trained on the gate backend {backend.IMPL_ID}"],
            {},
        )

    # One evaluator rollout of the candidate feeds BOTH the Pareto metrics and
    # its PBO utility stream -- the gate's dominant cost is the judge, never
    # duplicate it.
    cand_roll = metrics.rollout(
        candidate, admission_events, expert_cfg, backend, env_factory=env_factory
    )
    cand_m = metrics.aggregate(cand_roll, expert_cfg)
    base_m = metrics.evaluate(
        baseline, admission_events, expert_cfg, backend, env_factory=env_factory
    )
    out: dict[str, Any] = {"candidate": cand_m, "baseline": base_m}
    reasons: list[str] = []

    if not metrics.pareto_beats(cand_m, base_m):
        reasons.append(
            "pareto: candidate does not improve on baseline "
            f"(risk {cand_m['risk_rate']:.3f} vs {base_m['risk_rate']:.3f}, "
            f"fuel {cand_m['dv_total_ms']:.3f} vs {base_m['dv_total_ms']:.3f} m/s)"
        )

    if config_family is not None:
        series = [
            [r for r, _ in cand_roll]
            if cfg_policy is candidate
            else pbo.event_utilities(
                cfg_policy, admission_events, expert_cfg, backend, env_factory=env_factory
            )
            for cfg_policy in config_family
        ]
        out["pbo"] = pbo.cscv_pbo(series)
        if out["pbo"] > PBO_MAX:
            reasons.append(f"pbo: {out['pbo']:.3f} exceeds {PBO_MAX} — config family overfits")

    return GateVerdict(admitted=not reasons, reasons=reasons, metrics=out)
