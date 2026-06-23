# SPDX-License-Identifier: Apache-2.0
"""Shared fixtures for storesmith integration tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.skills._smith import web

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Point STORESMITH_HOME at a throwaway dir so the ledger never touches the repo."""
    monkeypatch.setenv("STORESMITH_HOME", str(tmp_path / "ss"))


@pytest.fixture(autouse=True)
def _offline_web(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """Make model-decided web research a deterministic no-op (no LM, no network)."""

    def _decline(_brief: str) -> tuple[bool, list[str]]:
        return False, []

    monkeypatch.setattr(web, "_decide", _decline)
