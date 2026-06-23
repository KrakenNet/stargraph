# SPDX-License-Identifier: Apache-2.0
"""Every seed adapter must pass the real gate — the cold-start trainset stays valid."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.adaptersmith import gate
from stargraph.skills.adaptersmith.seeds import SEEDS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("seed", SEEDS, ids=[str(s["adapter_name"]) for s in SEEDS])
def test_seed_passes_gate(seed: dict[str, Any]) -> None:
    ok, results = gate.verify_sources(
        seed["adapter_source"], seed["test_source"], fixture=seed["fixture"]
    )
    assert ok, [r.findings for r in results if not r.passed]
