# SPDX-License-Identifier: Apache-2.0
"""GymEnvNode + rl:train_ppo + export_sb3_policy + PolicyNode, live under the extra.

The full W7 node chain on a real gymnasium env: step a registered env as a
graph node, train a tiny PPO through the trainer tool, publish it as ONNX via
the W4 export path, and run inference through PolicyNode's inherited MLNode
session. Self-skips without the rl-extra deps (importorskip conventions per
``test_export_ppo_v4.py``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

pytest.importorskip("gymnasium")
pytest.importorskip("stable_baselines3")
pytest.importorskip("torch")
pytest.importorskip("onnxruntime")

# pyright: reportMissingImports=false

pytestmark = [pytest.mark.integration, pytest.mark.slow]


class _Ctx:
    run_id = "run-rl-nodes-test"


def _exists(path: Path) -> bool:
    """Sync filesystem helper (kept sync so the async test doesn't trip ASYNC240)."""
    return path.exists()


class _EnvState(BaseModel):
    action: int = 0
    observation: list[float] = []
    reward: float = 0.0
    terminated: bool = False
    truncated: bool = False


async def test_gym_env_node_reset_then_step() -> None:
    from stargraph.rl.envs import GymEnvNode

    node = GymEnvNode(env_id="CartPole-v1", seed=7)
    first = await node.execute(_EnvState(), _Ctx())
    assert len(first["observation"]) == 4  # CartPole observation, plain floats
    assert first["reward"] == 0.0 and first["terminated"] is False  # reset call
    second = await node.execute(_EnvState(**first, action=0), _Ctx())
    assert second["reward"] == 1.0  # CartPole pays 1.0 per survived step
    assert isinstance(second["observation"], list)


def test_gym_env_node_unknown_id_fails_at_definition_time() -> None:
    from stargraph.errors import RLNodeError
    from stargraph.rl.envs import GymEnvNode

    with pytest.raises(RLNodeError, match="could not build env"):
        GymEnvNode(env_id="NoSuchEnv-v0")


async def test_train_export_policy_node_chain(tmp_path: Path) -> None:
    from stargraph.ml.export import export_sb3_policy
    from stargraph.ml.registry import ModelRegistry
    from stargraph.rl.policy import PolicyNode
    from stargraph.rl.trainer import train_ppo

    # 1. train a deliberately tiny PPO through the tool
    meta: dict[str, Any] = await train_ppo(
        env_id="CartPole-v1",
        out_dir=str(tmp_path / "model"),
        total_timesteps=256,
        seed=0,
        n_steps=64,
        batch_size=32,
    )
    model_path = Path(meta["model_path"])
    assert _exists(model_path)
    assert _exists(tmp_path / "model" / "policy_meta.json")

    # 2. publish it as ONNX via the W4 export path into a temp registry
    registry = ModelRegistry(tmp_path / "registry.sqlite")
    await registry.bootstrap()
    entry = await export_sb3_policy(model_path, "cartpole-tiny", registry, version="1")

    # 3. PolicyNode inference: observation -> (action, value, log_prob)
    class _PolicyState(BaseModel):
        observation: list[float]
        action: int = 0
        value: list[float] = []
        log_prob: float = 0.0

    node = PolicyNode(model_id="cartpole-tiny", version="1", file_uri=entry.file_uri)
    out = await node.execute(_PolicyState(observation=[0.01, -0.02, 0.03, 0.04]), _Ctx())
    assert out["action"] in (0, 1)  # deterministic Discrete(2) actor
    assert len(out["value"]) == 1  # critic value row per sample
    assert isinstance(out["log_prob"], float)
