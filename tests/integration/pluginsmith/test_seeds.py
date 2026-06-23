# SPDX-License-Identifier: Apache-2.0
"""Every seed plugin must pass the real gate — the cold-start trainset stays valid."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.pluginsmith import gate
from stargraph.skills.pluginsmith.seeds import SEEDS

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("seed", SEEDS, ids=[str(s["id"]) for s in SEEDS])
def test_seed_passes_gate(seed: dict[str, Any]) -> None:
    ok, results = gate.verify_sources(
        namespace=seed["namespace"],
        tool_name=seed["tool_name"],
        tool_attr=seed["tool_attr"],
        plugin_source=seed["plugin_source"],
        test_source=seed["test_source"],
        fixture=seed["fixture"],
    )
    assert ok, [r.findings for r in results if not r.passed]
