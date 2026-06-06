# SPDX-License-Identifier: Apache-2.0
"""``RyuGraphStore.expand`` hops-bounds validation (AC-12.3, design §3.2).

Variable-length path traversal in Kuzu cannot be parameterised, so
the hop bound is interpolated as a Cypher literal. To prevent
unbounded traversal blowups, the implementation requires
``0 < hops <= 10`` and raises :class:`ValueError` otherwise. These
unit tests pin the boundary contract:

- ``hops=0`` rejects;
- ``hops=11`` rejects;
- ``hops in 1..10`` accepts (bounds-validation does not raise -- the
  query may still legitimately error on an unopened DB, but the
  ``ValueError`` from the bounds check must not fire).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from stargraph.stores.graph import NodeRef
from stargraph.stores.ryugraph import RyuGraphStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


async def test_expand_hops_zero_raises(tmp_path: Path) -> None:
    """``hops=0`` rejects with :class:`ValueError`."""
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()

    with pytest.raises(ValueError, match="hops"):
        await store.expand(NodeRef(id="alice", kind="Person"), hops=0)


async def test_expand_hops_eleven_raises(tmp_path: Path) -> None:
    """``hops=11`` rejects with :class:`ValueError`."""
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()

    with pytest.raises(ValueError, match="hops"):
        await store.expand(NodeRef(id="alice", kind="Person"), hops=11)


async def test_expand_hops_one_to_ten_accepted(tmp_path: Path) -> None:
    """``hops`` in 1..10 does not trip the bounds-validation ``ValueError``."""
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()

    node = NodeRef(id="alice", kind="Person")
    for hops in range(1, 11):
        # No rows seeded -- expand returns []; the test only pins that
        # the bounds check does NOT raise for any hop in [1, 10].
        result = await store.expand(node, hops=hops)
        assert result == []
