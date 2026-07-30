# SPDX-License-Identifier: Apache-2.0
"""stargraph.rl.trainer -- ``@tool`` wrapping stable-baselines3 PPO (W7).

The model-free RL experimentation door: train a PPO policy on a **registered**
gymnasium env (same id resolution as :class:`~stargraph.rl.envs.GymEnvNode`)
and leave an SB3 ``.zip`` + ``policy_meta.json`` behind. Serve the result
through the governed path: publish it as ONNX via
:func:`stargraph.ml.export.export_sb3_policy` and run it with
:class:`~stargraph.rl.policy.PolicyNode`, or gate it with
:mod:`stargraph.rl.gauntlet` before anything downstream consumes it.

Hyperparameter defaults mirror ARLO's trainer (``arlo/train/ppo_train.py``:
``n_steps=512, batch_size=128``). ``stable_baselines3`` is imported lazily
inside the function per the ``ml/export.py`` optional-dep seam; install::

    pip install 'stargraph[rl]'

``side_effects=write`` (model files land on disk), so the FR-21 default
replay policy is ``must_stub`` -- a replayed run never silently retrains.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from typing import Any

from stargraph.errors import RLNodeError
from stargraph.rl.envs import make_env
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["train_ppo"]

_EXTRA_HINT = "install the RL extra: pip install 'stargraph[rl]'"


def _require_sb3() -> Any:
    """Import stable-baselines3 lazily; typed, actionable error when absent."""
    try:
        return importlib.import_module("stable_baselines3")
    except ImportError as exc:
        raise RLNodeError(
            "stable-baselines3 is required for train_ppo but is not installed",
            hint=_EXTRA_HINT,
        ) from exc


@tool(
    name="train_ppo",
    namespace="rl",
    version="1",
    side_effects=SideEffects.write,
    requires_capability="tools:rl_train_ppo",
    description=(
        "Train a stable-baselines3 PPO policy on a registered gymnasium env; "
        "writes <out_dir>/ppo_policy.zip + policy_meta.json and returns the "
        "meta dict. Requires the stargraph[rl] extra."
    ),
)
async def train_ppo(
    env_id: str,
    out_dir: str,
    total_timesteps: int = 10_000,
    seed: int = 0,
    n_steps: int = 512,
    batch_size: int = 128,
    policy: str = "MlpPolicy",
) -> dict[str, Any]:
    """Train PPO on ``env_id`` (training runs in a worker thread).

    :param env_id: gymnasium registry id (``"module.path:EnvId"`` supported).
    :param out_dir: Directory for ``ppo_policy.zip`` + ``policy_meta.json``.
    :param total_timesteps: SB3 ``learn`` budget.
    :param seed: PPO seed (pins the run for reproducibility claims).
    :param n_steps: Rollout-buffer length per update (ARLO default).
    :param batch_size: Minibatch size (ARLO default).
    :param policy: SB3 policy class name (e.g. ``"MlpPolicy"``).
    :returns: The written ``policy_meta.json`` contents plus ``model_path``.
    """
    sb3 = _require_sb3()
    env = make_env(env_id)

    def _train() -> dict[str, Any]:
        model = sb3.PPO(
            policy,
            env,
            seed=seed,
            n_steps=n_steps,
            batch_size=batch_size,
            verbose=0,
        )
        model.learn(total_timesteps=total_timesteps, progress_bar=False)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        model.save(out / "ppo_policy")
        meta: dict[str, Any] = {
            "policy_id": f"ppo-{env_id}-seed{seed}-{total_timesteps}",
            "algo": "PPO",
            "env_id": env_id,
            "seed": seed,
            "timesteps": total_timesteps,
            "n_steps": n_steps,
            "batch_size": batch_size,
        }
        (out / "policy_meta.json").write_text(json.dumps(meta, indent=2))
        return {"model_path": str(out / "ppo_policy.zip"), **meta}

    try:
        return await asyncio.to_thread(_train)
    finally:
        env.close()
