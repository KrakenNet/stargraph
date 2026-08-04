# SPDX-License-Identifier: Apache-2.0
"""stargraph.rl.gauntlet -- RL admission gauntlet, ported from upstream (W7).

The evaluation discipline that produced the upstream pipeline's cited ppo-v4
refusal decision, promoted into Stargraph as a library:

* :mod:`~stargraph.rl.gauntlet.splits` -- 3-way disjoint event+time split.
* :mod:`~stargraph.rl.gauntlet.pbo` -- CSCV Probability of Backtest
  Overfitting (Lopez de Prado 2015).
* :mod:`~stargraph.rl.gauntlet.metrics` -- the single episode protocol +
  Pareto admission criterion.
* :mod:`~stargraph.rl.gauntlet.admission` -- the admission gate
  (default = refusal; explicit reasons).
* :mod:`~stargraph.rl.gauntlet.stations` -- wall/train/gate/shield graph
  nodes for the reference eval graph (``eval-graph.yaml`` next to this
  file; transitions decided by its Fathom rule pack, not static edges).

The math in ``splits`` / ``pbo`` / ``metrics`` / ``admission`` is
intentionally IDENTICAL to the upstream implementation -- only imports and
typing were adapted, and the hard ``CaEventEnv`` import was replaced by the
``env_factory`` seam (:data:`~stargraph.rl.gauntlet.metrics.EnvFactory`) so
any event-episode env can plug in. Everything here is pure Python -- no
``rl`` extra required.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.rl.gauntlet.admission import PBO_MAX, GateVerdict, gate
from stargraph.rl.gauntlet.metrics import (
    Backend,
    EnvFactory,
    EpisodeEnv,
    EventPolicy,
    aggregate,
    evaluate,
    pareto_beats,
    rollout,
)
from stargraph.rl.gauntlet.pbo import CSCV_BLOCKS, cscv_pbo, event_utilities
from stargraph.rl.gauntlet.splits import Split, materialize, three_way

__all__ = [
    "CSCV_BLOCKS",
    "PBO_MAX",
    "Backend",
    "EnvFactory",
    "EpisodeEnv",
    "EventPolicy",
    "GateVerdict",
    "Split",
    "aggregate",
    "cscv_pbo",
    "eval_graph_path",
    "evaluate",
    "event_utilities",
    "gate",
    "materialize",
    "pareto_beats",
    "rollout",
    "three_way",
]


def eval_graph_path() -> Path:
    """Absolute path of the packaged reference eval graph (``eval-graph.yaml``)."""
    return Path(__file__).resolve().parent / "eval-graph.yaml"
