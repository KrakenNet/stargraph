# SPDX-License-Identifier: Apache-2.0
"""stargraph.ml.export -- publish PyTorch modules / SB3 policies to the registry as ONNX.

PyTorch is **not** an :class:`~stargraph.nodes.ml.MLNode` runtime -- the
supported runtimes stay ``sklearn`` / ``xgboost`` / ``onnx``. Torch models
enter Stargraph through this module instead: ``torch.onnx.export`` writes
the graph to a file, the bytes are sha256-hashed, and the artifact is
registered in a :class:`stargraph.ml.registry.ModelRegistry` under
``runtime="onnx"`` so the existing content-hash gate covers the published
model end to end (tampered bytes fail ``registry.load`` with
:class:`stargraph.errors.IncompatibleModelHashError`).

``torch`` / ``stable_baselines3`` are imported lazily (via
:func:`importlib.import_module`, the optional-dep seam) inside the
functions; neither is a core dependency. Install the export extra::

    pip install 'stargraph[onnx-export]'
"""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.errors import MLNodeError
from stargraph.ml.loaders import _sha256_of  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    from collections.abc import Sequence

    from stargraph.ml.registry import ModelEntry, ModelRegistry

__all__ = ["export_sb3_policy", "export_torch_module"]

_EXTRA_HINT = "install the export extra: pip install 'stargraph[onnx-export]'"


def _require_torch() -> Any:
    """Import torch lazily; raise a typed, actionable error when absent."""
    try:
        return importlib.import_module("torch")
    except ImportError as exc:
        raise MLNodeError(
            "torch is required for ONNX export but is not installed",
            hint=_EXTRA_HINT,
            runtime="onnx",
        ) from exc


async def export_torch_module(
    module: Any,
    sample_input: Any,
    name: str,
    registry: ModelRegistry,
    *,
    version: str,
    output_path: Path | str | None = None,
    opset_version: int = 17,
    input_names: Sequence[str] = ("input",),
    output_names: Sequence[str] | None = None,
    dynamo: bool | None = None,
    **register_kwargs: Any,
) -> ModelEntry:
    """Export a ``torch.nn.Module`` to ONNX and register it under ``runtime="onnx"``.

    Wraps ``torch.onnx.export``: traces ``module`` (switched to eval mode)
    with ``sample_input``, writes the ONNX graph to ``output_path`` (a fresh
    temp directory when ``None`` -- note the registry verifies the file
    bytes at every ``load``, so pass a durable ``output_path`` for anything
    beyond the current process), then registers ``(name, version)`` with the
    file's sha256 as ``content_hash``. Extra keyword arguments
    (``framework=``, ``metadata=``) are forwarded to
    :meth:`~stargraph.ml.registry.ModelRegistry.register`.

    ``dynamo=None`` (default) uses torch's default exporter; pass ``False``
    to force the TorchScript tracer for modules the dynamo exporter cannot
    trace (e.g. ``torch.distributions`` heads -- requires torch>=2.5, where
    ``torch.onnx.export`` grew the ``dynamo`` keyword).

    Returns the registered :class:`~stargraph.ml.registry.ModelEntry`, loaded
    back through the registry so the content-hash gate has verified the
    written bytes before this function returns.

    :raises MLNodeError: when torch is not installed (``hint`` names the
        ``onnx-export`` extra).
    """
    torch = _require_torch()

    if output_path is None:
        out_dir = Path(tempfile.mkdtemp(prefix="stargraph-onnx-export-"))
        output_path = out_dir / f"{name}-{version}.onnx"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    module.eval()
    export_kwargs: dict[str, Any] = {} if dynamo is None else {"dynamo": dynamo}
    torch.onnx.export(
        module,
        (sample_input,),
        str(output_path),
        opset_version=opset_version,
        input_names=list(input_names),
        output_names=None if output_names is None else list(output_names),
        **export_kwargs,
    )

    await registry.register(
        model_id=name,
        version=version,
        runtime="onnx",
        file_uri=output_path.as_uri(),
        content_hash=_sha256_of(output_path),
        **register_kwargs,
    )
    # Load back through the registry: re-reads the file and verifies the
    # content hash, so a registered entry is proven loadable on return.
    return await registry.load(name, version)


async def export_sb3_policy(
    model_or_path: Any,
    name: str,
    registry: ModelRegistry,
    *,
    version: str,
    device: str = "cpu",
    output_path: Path | str | None = None,
    opset_version: int = 17,
    **register_kwargs: Any,
) -> ModelEntry:
    """Export a stable-baselines3 actor-critic policy to ONNX and register it.

    ``model_or_path`` may be a loaded SB3 algorithm (anything with a
    ``.policy``), a bare policy, or a path to an SB3 ``.zip``. The policy's
    torch module is wrapped so the ONNX graph maps a batched observation to
    the standard SB3 triple ``(actions, values, log_prob)`` with the actor
    evaluated **deterministically** (the standard SB3 export recipe), then
    published via :func:`export_torch_module`.

    Supports on-policy actor-critic policies (PPO / A2C ``ActorCriticPolicy``
    lineage); the ``.zip`` path rebuilds the saved ``policy_class`` from the
    archive's spaces + kwargs and loads its state dict -- the full algorithm
    class is never needed.

    .. warning:: SB3 ``.zip`` archives contain pickled objects
       (deserialized via ``torch.load``) -- only export archives you
       trust, exactly like the sklearn ``allow_unsafe_pickle`` stance.
       The published **ONNX** artifact is pickle-free; this caveat is
       about the *input* archive.

    :raises MLNodeError: when torch or stable-baselines3 is missing
        (``hint`` names the ``onnx-export`` extra).
    """
    torch = _require_torch()
    policy = _resolve_sb3_policy(model_or_path, device=device)

    class _DeterministicPolicy(torch.nn.Module):  # pyright: ignore[reportUntypedBaseClass]
        """ONNX-traceable wrapper: observation -> (actions, values, log_prob)."""

        def __init__(self, policy: Any) -> None:
            super().__init__()  # pyright: ignore[reportUnknownMemberType]
            self.policy = policy

        def forward(self, observation: Any) -> Any:
            return self.policy(observation, deterministic=True)

    obs_shape = tuple(policy.observation_space.shape)
    sample_input = torch.zeros((1, *obs_shape), dtype=torch.float32, device=device)

    return await export_torch_module(
        _DeterministicPolicy(policy),
        sample_input,
        name,
        registry,
        version=version,
        output_path=output_path,
        opset_version=opset_version,
        input_names=("observation",),
        output_names=("actions", "values", "log_prob"),
        # SB3 policies sample through torch.distributions, which the dynamo
        # exporter cannot trace (data-dependent guards in Categorical); the
        # TorchScript tracer handles deterministic=True fine and is the
        # standard SB3 export recipe.
        dynamo=False,
        **register_kwargs,
    )


def _lr_schedule(_: float) -> float:
    """Dummy schedule for policy reconstruction (never trained again)."""
    return 0.0


def _resolve_sb3_policy(model_or_path: Any, *, device: str) -> Any:
    """Extract the torch policy module from an SB3 model, policy, or ``.zip``."""
    try:
        sb3_policies: Any = importlib.import_module("stable_baselines3.common.policies")
        sb3_save_util: Any = importlib.import_module("stable_baselines3.common.save_util")
    except ImportError as exc:
        raise MLNodeError(
            "stable-baselines3 is required to export an SB3 policy but is not installed",
            hint=_EXTRA_HINT,
            runtime="onnx",
        ) from exc

    policy: Any
    if isinstance(model_or_path, sb3_policies.BasePolicy):
        policy = model_or_path
    elif hasattr(model_or_path, "policy"):  # loaded BaseAlgorithm
        policy = model_or_path.policy
    else:
        data, params, _ = sb3_save_util.load_from_zip_file(model_or_path, device=device)
        policy = data["policy_class"](
            observation_space=data["observation_space"],
            action_space=data["action_space"],
            lr_schedule=_lr_schedule,
            **(data.get("policy_kwargs") or {}),
        )
        policy.load_state_dict(params["policy"])

    policy = policy.to(device)
    policy.set_training_mode(False)
    return policy
