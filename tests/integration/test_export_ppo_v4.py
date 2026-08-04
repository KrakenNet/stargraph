# SPDX-License-Identifier: Apache-2.0
"""W4 acceptance: real SB3 PPO candidate -> ONNX -> registry -> MLNode inference.

Exercises :func:`stargraph.ml.export.export_sb3_policy` against a real trained
PPO candidate (an SB3 ``.zip`` living OUTSIDE this repo -- no binary
artifacts are committed). Self-skips unless torch, stable-baselines3 and
onnxruntime are importable and the model zip exists; point
``STARGRAPH_PPO_V4_DIR`` at a directory containing the ``.zip`` to run it.

Covers the full W4 acceptance chain:

1. export the policy to ONNX + register it in a temp ModelRegistry,
2. run one inference through the existing MLNode onnx-session path on a
   plausible observation (sampled from the policy's observation space),
3. cross-check the ONNX deterministic action against the torch policy,
4. tamper with the registered bytes and confirm the content-hash gate
   rejects the load with ``IncompatibleModelHashError``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
import pytest

from stargraph.errors import IncompatibleModelHashError
from stargraph.ml.export import export_sb3_policy
from stargraph.ml.registry import ModelRegistry
from stargraph.nodes.ml import MLNode

torch = pytest.importorskip("torch")
pytest.importorskip("stable_baselines3")
pytest.importorskip("onnxruntime")

# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

_PPO_DIR_ENV = os.environ.get("STARGRAPH_PPO_V4_DIR")
_PPO_DIR = Path(_PPO_DIR_ENV).expanduser() if _PPO_DIR_ENV else None
_PPO_ZIP = next(iter(_PPO_DIR.glob("*.zip")), None) if _PPO_DIR else None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _PPO_ZIP is None,
        reason="no SB3 .zip found (set STARGRAPH_PPO_V4_DIR to a directory containing one)",
    ),
]


class _Ctx:
    run_id = "test-export-ppo-v4"


def _tamper(path: Path) -> None:
    """Sync filesystem helper (kept sync so the async test doesn't trip ASYNC240)."""
    path.write_bytes(path.read_bytes() + b"tampered")


async def test_ppo_v4_export_register_infer_tamper_gate(tmp_path: Path) -> None:
    """Real-model acceptance: export -> register -> MLNode inference -> tamper."""
    assert _PPO_ZIP is not None
    registry = ModelRegistry(tmp_path / "models.db")
    await registry.bootstrap()
    try:
        # 1. Export + register (export re-loads through the registry, so the
        # content-hash gate has already verified the written bytes here).
        entry = await export_sb3_policy(
            _PPO_ZIP,
            "ppo-ca",
            registry,
            version="4.0.0",
            output_path=tmp_path / "ppo-ca.onnx",
            metadata={"source": _PPO_ZIP.name},
        )
        assert entry.runtime == "onnx"
        assert entry.metadata == {"source": _PPO_ZIP.name}

        # 2. Plausible observation: sample the policy's own observation space.
        from stargraph.ml.export import _resolve_sb3_policy  # pyright: ignore[reportPrivateUsage]

        policy = _resolve_sb3_policy(_PPO_ZIP, device="cpu")
        obs_space = policy.observation_space
        obs_space.seed(0)
        obs = np.asarray(obs_space.sample(), dtype=np.float32)

        node = MLNode(
            model_id="ppo-ca",
            version="4.0.0",
            runtime="onnx",
            file_uri=entry.file_uri,
        )

        class _State:
            x: Any = obs.tolist()  # rank-1; MLNode adds the batch axis

        out = await node.execute(_State(), _Ctx())  # type: ignore[arg-type]
        onnx_action = np.asarray(out["y"], dtype=np.float64)

        # 3. The ONNX graph's deterministic action must match torch's.
        with torch.no_grad():
            torch_action, values, log_prob = policy(
                torch.as_tensor(obs[np.newaxis, :]), deterministic=True
            )
        expected = np.asarray(torch_action.cpu().numpy()[0], dtype=np.float64)
        np.testing.assert_allclose(onnx_action, expected, atol=1e-5)
        print(
            f"\nppo-v4 export: obs shape={obs.shape} -> "
            f"action={onnx_action!r} (shape={onnx_action.shape}), "
            f"values shape={tuple(values.shape)}, log_prob shape={tuple(log_prob.shape)}"
        )

        # 4. Tamper with the registered bytes -> hash gate must refuse.
        _tamper(Path(urlparse(entry.file_uri).path))
        with pytest.raises(IncompatibleModelHashError):
            await registry.load("ppo-ca", "4.0.0")
    finally:
        await registry.close()
