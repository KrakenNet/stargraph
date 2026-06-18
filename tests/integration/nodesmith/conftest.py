# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for nodesmith integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Point NODESMITH_HOME at a throwaway dir so the ledger never touches the repo."""
    monkeypatch.setenv("NODESMITH_HOME", str(tmp_path / "nm"))
