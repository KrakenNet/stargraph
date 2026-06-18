# SPDX-License-Identifier: Apache-2.0
"""Every seed pair must pass the real gate — the cold-start data is trustworthy."""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.nodesmith.gate import verify_sources
from stargraph.skills.nodesmith.seeds import SEEDS

pytestmark = pytest.mark.integration


def test_seed_ids_are_unique() -> None:
    ids = [s["id"] for s in SEEDS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("seed", SEEDS, ids=[str(s["class_name"]) for s in SEEDS])
def test_seed_passes_gate(seed: dict[str, Any]) -> None:
    ok, results = verify_sources(
        seed["node_source"],
        seed["test_source"],
        reads=seed["reads"],
        writes=seed["writes"],
        fixture=seed["fixture"],
    )
    assert ok, [r for r in results if not r.passed]
