# SPDX-License-Identifier: Apache-2.0
"""Cypher write-keyword scan integration test (FR-20, AC-8.3, Task 3.17).

The Cypher portable-subset linter (:mod:`stargraph.stores.cypher`) exposes
:meth:`Linter.requires_write` -- a keyword scan used by FR-20 capability
gating to decide whether a graph branch needs ``db.<name>:write`` instead
of the default ``db.<name>:read``.

These tests assert two surfaces:

1. The bare keyword scan: ``MATCH ... RETURN`` is read-only,
   ``MERGE ... SET ...`` flags as write.
2. :class:`~stargraph.nodes.retrieval.RetrievalNode` derives ``db.X:write``
   capabilities at construction time when its ``cypher_by_store`` mapping
   contains a write-keyword query for that store name.

NOTE: Per Task 2.10's design, the capability gate runs **offline** at
construction time -- the resulting :attr:`RetrievalNode.requires` list is
fixed. Runtime ``CapabilityError`` enforcement happens at a separate
authorization seam (policy check against the granted capability set,
not inside :meth:`RetrievalNode.execute`). This test therefore verifies
the offline derivation, which is the load-bearing FR-20 contract for
this node.
"""

from __future__ import annotations

import pytest

from stargraph.ir._models import StoreRef
from stargraph.nodes.retrieval import RetrievalNode
from stargraph.stores.cypher import Linter

pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


def test_match_return_no_write() -> None:
    """A bare ``MATCH ... RETURN`` is a read-only query (FR-20)."""
    assert Linter().requires_write("MATCH (n) RETURN n") is False


def test_merge_set_requires_write() -> None:
    """``MERGE`` and ``SET`` both flag a query as write (FR-20)."""
    assert Linter().requires_write("MERGE (n {id: 'x'}) SET n.kind='y'") is True


def test_capability_elevated_for_write_query() -> None:
    """RetrievalNode escalates ``db.<name>:read`` to ``:write`` for write-Cypher (FR-20).

    Verifies the offline capability derivation in
    :meth:`RetrievalNode._derive_requires`. A write-Cypher branch yields
    ``db.<name>:write``; a read-only branch (or no Cypher) stays at
    ``db.<name>:read``. Runtime ``CapabilityError`` enforcement is a
    separate concern handled by the deployment's authorization seam,
    not by :meth:`RetrievalNode.execute`.
    """
    stores = [
        StoreRef(name="graph_w", provider="ryugraph"),
        StoreRef(name="graph_r", provider="ryugraph"),
        StoreRef(name="vec", provider="lancedb"),
    ]

    def _resolver(_name: str) -> object:  # not exercised in offline derivation
        raise AssertionError("resolver should not run during construction")

    node = RetrievalNode(
        stores=stores,
        store_resolver=_resolver,  # type: ignore[arg-type]
        cypher_by_store={
            "graph_w": "MERGE (n {id: 'a'}) SET n.kind='b'",
            "graph_r": "MATCH (n) RETURN n",
        },
    )

    assert "db.graph_w:write" in node.requires
    assert "db.graph_r:read" in node.requires
    assert "db.vec:read" in node.requires
    # The write branch must NOT also appear as :read, and vice versa.
    assert "db.graph_w:read" not in node.requires
    assert "db.graph_r:write" not in node.requires
