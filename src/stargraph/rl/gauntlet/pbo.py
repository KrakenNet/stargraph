# SPDX-License-Identifier: Apache-2.0
"""Probability of Backtest Overfitting via CSCV (Lopez de Prado 2015),
re-expressed over episode scenarios: ``series`` holds one per-event utility
stream per policy config, events in timeline order. Fail-closed: a single
config, or too few events, scores PBO 1.0.

Ported from the upstream collision-avoidance governed-RL pipeline, itself
derived from an earlier quantitative-research gauntlet. The CSCV
combinatorics and Sharpe arithmetic are intentionally IDENTICAL -- this is
the PBO behind the ppo-v4 admission verdict; only imports/typing were
adapted (plus the ``env_factory`` seam threaded through
:func:`event_utilities`).
"""

from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING, Any

from stargraph.rl.gauntlet import metrics

if TYPE_CHECKING:
    from stargraph.rl.gauntlet.metrics import Backend, EnvFactory, EventPolicy
    from stargraph.rl.gauntlet.splits import Event

CSCV_BLOCKS = 8


def _sharpe(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return 0.0
    return mean / math.sqrt(var)


def cscv_pbo(series: list[list[float]], blocks: int = CSCV_BLOCKS) -> float:
    """CSCV PBO over per-config utility streams (fail-closed to 1.0)."""
    n_configs = len(series)
    t = len(series[0]) if series else 0
    if n_configs < 2 or t < blocks:
        return 1.0
    bounds = [round(i * t / blocks) for i in range(blocks + 1)]
    block_idx = [list(range(bounds[i], bounds[i + 1])) for i in range(blocks)]

    def sr(indices: list[int], c: int) -> float:
        return _sharpe([series[c][i] for i in indices])

    below = 0
    combos = list(itertools.combinations(range(blocks), blocks // 2))
    for is_blocks in combos:
        is_set = set(is_blocks)
        is_idx = [i for b in is_blocks for i in block_idx[b]]
        oos_idx = [i for b in range(blocks) if b not in is_set for i in block_idx[b]]
        is_sr = [sr(is_idx, c) for c in range(n_configs)]
        best = max(range(n_configs), key=lambda c: is_sr[c])
        oos_sr = [sr(oos_idx, c) for c in range(n_configs)]
        rank = sum(1 for c in range(n_configs) if c != best and oos_sr[c] < oos_sr[best])
        if rank / (n_configs - 1) < 0.5:
            below += 1
    return below / len(combos)


def event_utilities(
    policy: EventPolicy,
    events: list[Event],
    expert_cfg: dict[str, Any],
    backend: Backend,
    *,
    env_factory: EnvFactory,
) -> list[float]:
    """One utility per event (timeline order): the terminal env reward. Feed one
    stream per config into :func:`cscv_pbo`."""
    return [
        reward
        for reward, _info in metrics.rollout(
            policy, events, expert_cfg, backend, env_factory=env_factory
        )
    ]
