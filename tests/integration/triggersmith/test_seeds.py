# SPDX-License-Identifier: Apache-2.0
"""Every seed trigger must pass the real gate — the cold-start trainset stays valid."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.triggersmith import gate
from stargraph.skills.triggersmith.seeds import SEEDS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("seed", SEEDS, ids=[str(s["class_name"]) for s in SEEDS])
def test_seed_passes_gate(seed: dict[str, Any]) -> None:
    ok, results = gate.verify_sources(
        seed["trigger_source"], seed["test_source"], fixture=seed["fixture"]
    )
    assert ok, [r.findings for r in results if not r.passed]
