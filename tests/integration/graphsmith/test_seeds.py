# SPDX-License-Identifier: Apache-2.0
"""Every seed bundle must pass the real gate — the cold-start trainset stays valid."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.graphsmith import gate
from stargraph.skills.graphsmith.seeds import SEEDS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("seed", SEEDS, ids=[str(s["id"]) for s in SEEDS])
def test_seed_passes_gate(seed: dict[str, Any]) -> None:
    ok, results = gate.verify_sources(
        graph_id=seed["graph_id"],
        node_classes=seed["node_classes"],
        state_source=seed["state_source"],
        nodes_source=seed["nodes_source"],
        test_source=seed["test_source"],
        fixture=seed["fixture"],
    )
    assert ok, [r.findings for r in results if not r.passed]
