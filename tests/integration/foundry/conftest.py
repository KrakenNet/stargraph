# SPDX-License-Identifier: Apache-2.0
"""Foundry test fixtures.

The foundry drives real smiths, which write their own ledgers; isolate every
``*_HOME`` the suite's builds touch into a tmp dir so runs don't pollute the
developer's real ledgers (and stay deterministic). Stage 1 drives graphsmith only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def isolated_homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for env in ("GRAPHSMITH_HOME", "STORESMITH_HOME", "PACKSMITH_HOME"):
        monkeypatch.setenv(env, str(tmp_path / env.lower()))
