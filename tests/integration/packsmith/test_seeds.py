# SPDX-License-Identifier: Apache-2.0
"""Every seed pack must pass the real gate — the cold-start trainset stays valid."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.packsmith import gate
from stargraph.skills.packsmith.seeds import SEEDS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("seed", SEEDS, ids=[str(s["id"]) for s in SEEDS])
def test_seed_passes_gate(seed: dict[str, Any]) -> None:
    ok, results = gate.verify_sources(
        pack_name=seed["pack_name"],
        flavor=seed["flavor"],
        input_template=seed["input_template"],
        output_template=seed["output_template"],
        rules_clp=seed["rules_clp"],
        test_source=seed["test_source"],
        fixture=seed["fixture"],
    )
    assert ok, [r.findings for r in results if not r.passed]
