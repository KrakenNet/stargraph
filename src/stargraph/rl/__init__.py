# SPDX-License-Identifier: Apache-2.0
"""stargraph.rl -- RL toolkit: env/policy nodes, admission gauntlet, planners (W7).

Promotes the PROVEN machinery of the ARLO governed-RL pipeline
(``~/leagues/arlo``, the Area-4 collision-avoidance work) into Stargraph as a
reusable library:

* :class:`~stargraph.rl.envs.GymEnvNode` -- a graph node wrapping a registered
  gymnasium environment (``rl`` extra).
* :class:`~stargraph.rl.policy.PolicyNode` -- MLNode specialization over the
  ONNX policies published by :func:`stargraph.ml.export.export_sb3_policy`
  (observation -> actions / values / log_prob).
* :mod:`stargraph.rl.gauntlet` -- the 3-way split discipline, CSCV-PBO, Pareto
  metrics and the admission gate, ported math-identical from ARLO, plus the
  reference wall -> train -> gate -> shield eval graph.
* :mod:`stargraph.rl.planners` -- the ``stargraph.planners`` entry-point group,
  the :class:`~stargraph.rl.planners.PlannerNode` contract, and the reference
  convex-MPC burn-option planner.
* :func:`stargraph.rl.trainer.train_ppo` -- ``@tool`` wrapping SB3 PPO
  (``rl`` extra; lazily imported per the ``ml/export.py`` optional-dep seam).

The gauntlet library itself is pure Python; gymnasium / stable-baselines3 /
scipy / onnxruntime enter only through the lazy seams and require::

    pip install 'stargraph[rl]'
"""

from __future__ import annotations

from stargraph.rl.envs import GymEnvNode
from stargraph.rl.planners import CandidatePlan, Planner, PlannerNode, load_planner
from stargraph.rl.policy import PolicyNode

__all__ = [
    "CandidatePlan",
    "GymEnvNode",
    "Planner",
    "PlannerNode",
    "PolicyNode",
    "load_planner",
]
