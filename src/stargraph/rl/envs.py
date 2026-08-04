# SPDX-License-Identifier: Apache-2.0
"""stargraph.rl.envs -- :class:`GymEnvNode`: a registered gymnasium env as a graph node.

Environments are addressed by their **gymnasium registry id** -- the same
indirection the model registry gives models: graphs name an env, never a
class. Two registration routes resolve an id:

1. ids already in the gymnasium registry (built-ins, or anything a
   distribution registered via ``gymnasium.register``), e.g. ``CartPole-v1``;
2. the entry-point-analogous ``"module.path:EnvId"`` form -- the module is
   imported first (its import side effect calls ``gymnasium.register``), then
   ``EnvId`` is resolved from the registry. This is how a package ships a
   custom env without touching Stargraph.

gymnasium is imported lazily inside the constructor per the optional-dep seam
(``ml/export.py`` pattern); install the extra::

    pip install 'stargraph[rl]'
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING, Any

from stargraph.errors import RLNodeError
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel

__all__ = ["GymEnvNode"]

_EXTRA_HINT = "install the RL extra: pip install 'stargraph[rl]'"


def _require_gymnasium() -> Any:
    """Import gymnasium lazily; raise a typed, actionable error when absent."""
    try:
        return importlib.import_module("gymnasium")
    except ImportError as exc:
        raise RLNodeError(
            "gymnasium is required for GymEnvNode but is not installed",
            hint=_EXTRA_HINT,
        ) from exc


def make_env(env_id: str, **env_kwargs: Any) -> Any:
    """Resolve ``env_id`` through the gymnasium registry (``module:EnvId`` supported).

    Shared by :class:`GymEnvNode` and :func:`stargraph.rl.trainer.train_ppo` so
    both surfaces resolve envs identically.
    """
    gym = _require_gymnasium()
    module_path, sep, bare_id = env_id.partition(":")
    if sep:
        try:
            importlib.import_module(module_path)
        except ImportError as exc:
            raise RLNodeError(
                f"cannot import env module {module_path!r} for env id {env_id!r}: {exc}",
                env_id=env_id,
            ) from exc
    else:
        bare_id = env_id
    try:
        return gym.make(bare_id, **env_kwargs)
    except Exception as exc:
        raise RLNodeError(
            f"gymnasium could not build env {bare_id!r}: {exc}",
            hint="register the env (gymnasium.register) or use 'module.path:EnvId'",
            env_id=env_id,
        ) from exc


class GymEnvNode(NodeBase):
    """Graph node stepping a registered gymnasium env; typed state carries the loop.

    Construction is eager (house style, like :class:`~stargraph.nodes.ml.MLNode`):
    the env is built immediately, so a missing extra or an unregistered id
    fails at definition time, not mid-run.

    State contract per :meth:`execute`:

    * first call (and every call after a terminal step) **resets** the
      episode -- the action field is ignored;
    * otherwise the env **steps** on ``state.<action_field>``
      (``int`` for ``Discrete`` action spaces, list/array otherwise);
    * writes ``{observation_field: obs, reward_field: float,
      terminated_field: bool, truncated_field: bool}`` -- numpy values are
      converted to plain Python so the field-merge writes JSON-serializable
      state.

    :param env_id: gymnasium registry id, optionally ``"module.path:EnvId"``.
    :param seed: Seed passed to the first ``reset`` of every episode
        (deterministic replays need a pinned seed).
    :param env_kwargs: Forwarded to ``gymnasium.make``.
    """

    def __init__(
        self,
        *,
        env_id: str,
        seed: int | None = None,
        env_kwargs: dict[str, Any] | None = None,
        action_field: str = "action",
        observation_field: str = "observation",
        reward_field: str = "reward",
        terminated_field: str = "terminated",
        truncated_field: str = "truncated",
    ) -> None:
        self.env_id = env_id
        self.seed = seed
        self.action_field = action_field
        self.observation_field = observation_field
        self.reward_field = reward_field
        self.terminated_field = terminated_field
        self.truncated_field = truncated_field
        self._env: Any = make_env(env_id, **(env_kwargs or {}))
        self._needs_reset = True

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        """Reset or step the wrapped env (offloaded to a worker thread)."""
        del ctx
        if self._needs_reset:
            return await asyncio.to_thread(self._reset)
        action: Any = getattr(state, self.action_field)
        return await asyncio.to_thread(self._step, action)

    # -- sync bodies (worker thread) ---------------------------------

    def _reset(self) -> dict[str, Any]:
        obs, _info = self._env.reset(seed=self.seed)
        self._needs_reset = False
        return {
            self.observation_field: _to_plain(obs),
            self.reward_field: 0.0,
            self.terminated_field: False,
            self.truncated_field: False,
        }

    def _step(self, action: Any) -> dict[str, Any]:
        obs, reward, terminated, truncated, _info = self._env.step(self._coerce(action))
        if terminated or truncated:
            self._needs_reset = True
        return {
            self.observation_field: _to_plain(obs),
            self.reward_field: float(reward),
            self.terminated_field: bool(terminated),
            self.truncated_field: bool(truncated),
        }

    def _coerce(self, action: Any) -> Any:
        """``int`` for Discrete spaces, float32 array otherwise."""
        gym = _require_gymnasium()
        if isinstance(self._env.action_space, gym.spaces.Discrete):
            return int(action)
        import numpy as np

        return np.asarray(action, dtype=np.float32)


def _to_plain(value: Any) -> Any:
    """numpy scalars/arrays -> plain Python (JSON-serializable state values)."""
    tolist = getattr(value, "tolist", None)
    return tolist() if callable(tolist) else value
