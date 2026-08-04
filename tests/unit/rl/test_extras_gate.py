# SPDX-License-Identifier: Apache-2.0
"""The rl-extra gate: absent optional deps fail with a typed, actionable error.

``stargraph.rl`` must import cleanly WITHOUT the ``rl`` extra (the
``ml/export.py`` importlib-funnel seam); only *using* a gym/SB3 surface may
fail, and then with :class:`~stargraph.errors.RLNodeError` naming the extra.
These tests run in the extra-less repo venv and self-skip wherever the dep
happens to be installed (e.g. the acceptance venv).
"""

from __future__ import annotations

import importlib.util

import pytest

from stargraph.errors import RLNodeError

pytestmark = pytest.mark.unit

_HAS_GYM = importlib.util.find_spec("gymnasium") is not None
_HAS_SB3 = importlib.util.find_spec("stable_baselines3") is not None


def test_rl_package_imports_without_the_extra() -> None:
    import stargraph.rl
    import stargraph.rl.envs
    import stargraph.rl.gauntlet
    import stargraph.rl.planners
    import stargraph.rl.trainer

    assert stargraph.rl.GymEnvNode is stargraph.rl.envs.GymEnvNode
    assert stargraph.rl.gauntlet.eval_graph_path().exists()
    assert stargraph.rl.planners.ENTRY_POINT_GROUP == "stargraph.planners"
    assert callable(stargraph.rl.trainer.train_ppo)


@pytest.mark.skipif(_HAS_GYM, reason="gymnasium installed; the absent-extra gate can't fire")
def test_gym_env_node_without_gymnasium_names_the_extra() -> None:
    from stargraph.rl.envs import GymEnvNode

    with pytest.raises(RLNodeError, match="gymnasium is required") as err:
        GymEnvNode(env_id="CartPole-v1")
    assert "stargraph[rl]" in str(err.value)  # hint carries the install fix


@pytest.mark.skipif(_HAS_SB3, reason="stable-baselines3 installed; gate can't fire")
@pytest.mark.asyncio
async def test_train_ppo_without_sb3_names_the_extra(tmp_path: object) -> None:
    from stargraph.rl.trainer import train_ppo

    with pytest.raises(RLNodeError, match="stable-baselines3 is required") as err:
        await train_ppo(env_id="CartPole-v1", out_dir=str(tmp_path))
    assert "stargraph[rl]" in str(err.value)
