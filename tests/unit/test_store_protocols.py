# SPDX-License-Identifier: Apache-2.0
"""Structural / signature tests for the five Store Protocols (design §3, FR-1)."""

from __future__ import annotations

import inspect

import pytest

from stargraph.stores import (
    DocStore,
    FactStore,
    GraphStore,
    MemoryStore,
    VectorStore,
)

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]

ALL_PROTOCOLS = (VectorStore, GraphStore, DocStore, MemoryStore, FactStore)
LIFECYCLE_METHODS = ("bootstrap", "health", "migrate")
PER_STORE_METHODS = {
    VectorStore: ("upsert", "search", "delete"),
    GraphStore: ("add_triple", "query", "expand"),
    DocStore: ("put", "get", "query"),
    MemoryStore: ("put", "recent", "consolidate"),
    FactStore: ("pin", "unpin", "query"),
}


def test_lifecycle_signatures() -> None:
    """All five Protocols declare the shared lifecycle surface (FR-8/FR-9)."""
    for proto in ALL_PROTOCOLS:
        for name in LIFECYCLE_METHODS:
            attr = getattr(proto, name, None)
            assert attr is not None, f"{proto.__name__} missing {name}"
            assert inspect.iscoroutinefunction(attr), f"{proto.__name__}.{name} must be async"

        # bootstrap takes only self; health takes only self; migrate takes plan.
        bootstrap_sig = inspect.signature(proto.bootstrap)
        assert list(bootstrap_sig.parameters) == ["self"], (
            f"{proto.__name__}.bootstrap must be (self) -> None"
        )
        health_sig = inspect.signature(proto.health)
        assert list(health_sig.parameters) == ["self"], (
            f"{proto.__name__}.health must be (self) -> StoreHealth"
        )
        migrate_sig = inspect.signature(proto.migrate)
        assert list(migrate_sig.parameters) == ["self", "plan"], (
            f"{proto.__name__}.migrate must be (self, plan)"
        )


def test_per_store_crud_surfaces() -> None:
    """Each Protocol declares its store-specific CRUD methods (FR-1/AC-1.1)."""
    for proto, methods in PER_STORE_METHODS.items():
        for name in methods:
            attr = getattr(proto, name, None)
            assert attr is not None, f"{proto.__name__} missing {name}"
            assert inspect.iscoroutinefunction(attr), f"{proto.__name__}.{name} must be async"


def test_protocols_are_runtime_checkable() -> None:
    """All five Protocols are decorated with @runtime_checkable (AC-1.2)."""
    for proto in ALL_PROTOCOLS:
        assert getattr(proto, "_is_runtime_protocol", False), (
            f"{proto.__name__} must be @runtime_checkable"
        )
