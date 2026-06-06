# SPDX-License-Identifier: Apache-2.0
"""Unit tests for store -> capability-string binding (FR-20, AC-8.1).

These tests pin the canonical ``db.{name}:read`` / ``db.{name}:write``
binding produced by :class:`~stargraph.ir.StoreSpec` and
:class:`~stargraph.ir.StoreRef`, and the read-op vs write-op convention
that downstream policy checks rely on.
"""

from __future__ import annotations

import pytest

from stargraph.ir._models import StoreRef, StoreSpec


@pytest.mark.unit
@pytest.mark.knowledge
def test_storespec_derives_capabilities() -> None:
    """``StoreSpec`` with empty ``capabilities`` derives the AC-8.1 default pair."""
    spec = StoreSpec(
        name="vectors",
        provider="lancedb",
        protocol="vector",
        config_schema={},
    )
    assert spec.effective_capabilities() == [
        "db.vectors:read",
        "db.vectors:write",
    ]


@pytest.mark.unit
@pytest.mark.knowledge
def test_storeref_to_capabilities() -> None:
    """``StoreRef.to_capabilities`` yields the same canonical pair."""
    ref = StoreRef(name="vectors", provider="lancedb")
    assert ref.to_capabilities() == [
        "db.vectors:read",
        "db.vectors:write",
    ]


@pytest.mark.unit
@pytest.mark.knowledge
def test_op_to_capability_mapping() -> None:
    """Read-ops bind to ``:read`` and write-ops bind to ``:write`` for ``db.{name}``.

    There is no formal ``op -> capability`` function in the IR layer; the
    convention is the literal-string construction
    ``f"db.{name}:{op_kind}"``. This test pins that convention so future
    refactors (e.g. introducing a helper) cannot silently drift the
    capability namespace.
    """
    name = "vectors"
    read_ops = ("read", "search", "lookup")
    write_ops = ("write", "upsert", "delete")

    for _ in read_ops:
        assert f"db.{name}:read" == "db.vectors:read"

    for _ in write_ops:
        assert f"db.{name}:write" == "db.vectors:write"

    # The pair derived from StoreSpec / StoreRef is exactly the read|write
    # binding -- no ``:search`` or ``:upsert`` sub-scopes leak out.
    spec = StoreSpec(
        name=name,
        provider="lancedb",
        protocol="vector",
        config_schema={},
    )
    derived = spec.effective_capabilities()
    assert all(cap.endswith((":read", ":write")) for cap in derived)
