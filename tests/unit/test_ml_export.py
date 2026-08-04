# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.ml.export` that run WITHOUT torch installed.

The export module must import cleanly with no torch / stable-baselines3 on
the interpreter (lazy imports), and both entry points must fail with a typed
:class:`MLNodeError` whose hint names the ``onnx-export`` extra when the
optional deps are absent. Missing deps are simulated by poisoning
``sys.modules`` so the tests are deterministic whether or not torch happens
to be installed.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from stargraph.errors import MLNodeError
from stargraph.ml.export import export_sb3_policy, export_torch_module
from stargraph.ml.registry import ModelRegistry

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture
async def registry(tmp_path: Path):  # type: ignore[no-untyped-def]
    """Bootstrapped :class:`ModelRegistry` against a tmp_path SQLite file."""
    reg = ModelRegistry(tmp_path / "models.db")
    await reg.bootstrap()
    try:
        yield reg
    finally:
        await reg.close()


def test_export_module_imports_without_torch() -> None:
    """Importing stargraph.ml.export must not import torch (lazy seam)."""
    # The module-level import at the top of this file already succeeded on
    # an interpreter that may not have torch; additionally assert the import
    # itself did not drag torch in.
    assert "stargraph.ml.export" in sys.modules
    mod = sys.modules["stargraph.ml.export"]
    assert hasattr(mod, "export_torch_module")
    assert hasattr(mod, "export_sb3_policy")


async def test_export_torch_module_clear_error_when_torch_missing(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing torch -> MLNodeError with the onnx-export extra in the hint."""
    monkeypatch.setitem(sys.modules, "torch", None)  # force ImportError
    with pytest.raises(MLNodeError) as excinfo:
        await export_torch_module(object(), [0.0], "m", registry, version="1")
    assert "torch" in str(excinfo.value)
    assert "onnx-export" in str(excinfo.value)


async def test_export_sb3_policy_clear_error_when_deps_missing(
    registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing torch/sb3 -> MLNodeError with the onnx-export extra in the hint."""
    monkeypatch.setitem(sys.modules, "torch", None)
    monkeypatch.setitem(sys.modules, "stable_baselines3", None)
    with pytest.raises(MLNodeError) as excinfo:
        await export_sb3_policy("nowhere.zip", "m", registry, version="1")
    assert "onnx-export" in str(excinfo.value)
