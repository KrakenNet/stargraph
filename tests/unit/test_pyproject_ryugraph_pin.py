# SPDX-License-Identifier: Apache-2.0
"""Pin RyuGraph to ``>=25.9.2,<26`` + carry the swap-path comment (FR-11, AC-12.1).

RyuGraph is the community fork of Kuzu (predictable-labs/ryugraph) after
Kuzu's GitHub repository was archived 2025-10-10 following Apple's
acquisition of Kuzu Inc. Stargraph abstracts the property-graph backend
behind the :class:`stargraph.stores.graph.GraphStore` Protocol so the swap
itself was a one-module rename. We carry a calver-bounded pin
(``>=25.9.2,<26``) and document the swap rationale inline; this test
guards both the pin and the rationale comment so a future bump cannot
silently drop either.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_ryugraph_version_pinned() -> None:
    """``ryugraph>=25.9.2,<26`` calver pin lives under
    ``[project.optional-dependencies].stores``."""
    data = tomllib.loads(_PYPROJECT.read_text())
    stores = data["project"]["optional-dependencies"]["stores"]
    pins = [dep for dep in stores if dep.startswith("ryugraph")]
    assert pins == ["ryugraph>=25.9.2,<26"], (
        f"expected calver pin 'ryugraph>=25.9.2,<26' in "
        f"[project.optional-dependencies].stores, got {pins!r}"
    )
    # Defence-in-depth: the legacy ``kuzu`` pin must not have crept back in.
    legacy = [dep for dep in stores if dep.startswith("kuzu")]
    assert legacy == [], f"legacy kuzu pin must not coexist with ryugraph, got {legacy!r}"


def test_ryugraph_pin_has_swap_path_comment() -> None:
    """Pyproject carries the RyuGraph swap-path rationale next to the pin."""
    text = _PYPROJECT.read_text()
    lowered = text.lower()
    assert "ryugraph" in lowered, (
        "pyproject must explain the RyuGraph swap path next to the ryugraph pin (FR-11, AC-12.1)"
    )
    # The comment must mention the historical Kuzu archival rationale so
    # future maintainers see WHY we are on the fork.
    assert "kuzu" in lowered, (
        "swap-path comment must mention Kuzu (the upstream that was archived) so future "
        "maintainers see why we are on the RyuGraph fork (FR-11, AC-12.1)"
    )
    assert "archived" in lowered or "apple" in lowered, (
        "swap-path comment must capture WHY we forked (Kuzu repo archived after Apple acquisition)"
    )
