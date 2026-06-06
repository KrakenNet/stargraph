# SPDX-License-Identifier: Apache-2.0
"""FR-17: ``migrate(plan)`` rejects type narrows / column renames / drops.

The v1 contract from stargraph-knowledge design §4.5 is that ``migrate()``
supports add-nullable-column **only**. Forward-incompatible operations
(narrowing a column type, renaming a column, dropping a column) are
unsafe in Lance fragments and SQLite alike, so every store must surface
them as :class:`MigrationNotSupported` before any DDL runs.

This unit test exercises the validation guard against every store
implementation so a future stub does not silently accept a narrow.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from stargraph.errors import MigrationNotSupported
from stargraph.stores._common import MigrationPlan
from stargraph.stores.sqlite_doc import SQLiteDocStore
from stargraph.stores.sqlite_fact import SQLiteFactStore
from stargraph.stores.sqlite_memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def _bootstrap_doc(tmp_path: Path) -> Any:
    store = SQLiteDocStore(tmp_path / "doc.sqlite")
    asyncio.run(store.bootstrap())
    return store


def _bootstrap_fact(tmp_path: Path) -> Any:
    store = SQLiteFactStore(tmp_path / "fact.sqlite")
    asyncio.run(store.bootstrap())
    return store


def _bootstrap_memory(tmp_path: Path) -> Any:
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite")
    asyncio.run(store.bootstrap())
    return store


_BOOTSTRAPS: list[tuple[Callable[[Path], Any], str]] = [
    (_bootstrap_doc, "sqlite_doc"),
    (_bootstrap_fact, "sqlite_fact"),
    (_bootstrap_memory, "sqlite_memory"),
]


def _run_migrate(store: Any, plan: MigrationPlan) -> None:
    coro = store.migrate(plan)
    asyncio.run(coro)


@pytest.mark.parametrize(("bootstrap_fn", "store_name"), _BOOTSTRAPS)
def test_migrate_rejects_type_narrow(
    tmp_path: Path,
    bootstrap_fn: Callable[[Path], Any],
    store_name: str,
) -> None:
    """``narrow_type`` op raises :class:`MigrationNotSupported` (FR-17)."""
    store = bootstrap_fn(tmp_path)
    plan = MigrationPlan(
        target_version=2,
        operations=[
            {"op": "narrow_type", "table": "documents", "column": "metadata", "type": "TEXT"}
        ],
    )
    with pytest.raises(MigrationNotSupported) as excinfo:
        _run_migrate(store, plan)
    assert excinfo.value.context["store"] == store_name
    assert excinfo.value.context["operation"] == "narrow_type"


@pytest.mark.parametrize(("bootstrap_fn", "store_name"), _BOOTSTRAPS)
def test_migrate_rejects_column_rename(
    tmp_path: Path,
    bootstrap_fn: Callable[[Path], Any],
    store_name: str,
) -> None:
    """``rename_column`` op raises :class:`MigrationNotSupported` (FR-17)."""
    store = bootstrap_fn(tmp_path)
    plan = MigrationPlan(
        target_version=2,
        operations=[
            {
                "op": "rename_column",
                "table": "documents",
                "old_name": "doc_id",
                "new_name": "document_id",
            }
        ],
    )
    with pytest.raises(MigrationNotSupported) as excinfo:
        _run_migrate(store, plan)
    assert excinfo.value.context["store"] == store_name
    assert excinfo.value.context["operation"] == "rename_column"


@pytest.mark.parametrize(("bootstrap_fn", "store_name"), _BOOTSTRAPS)
def test_migrate_rejects_column_drop(
    tmp_path: Path,
    bootstrap_fn: Callable[[Path], Any],
    store_name: str,
) -> None:
    """``drop_column`` op raises :class:`MigrationNotSupported` (FR-17)."""
    store = bootstrap_fn(tmp_path)
    plan = MigrationPlan(
        target_version=2,
        operations=[{"op": "drop_column", "table": "documents", "column": "metadata"}],
    )
    with pytest.raises(MigrationNotSupported) as excinfo:
        _run_migrate(store, plan)
    assert excinfo.value.context["store"] == store_name
    assert excinfo.value.context["operation"] == "drop_column"


def test_migrate_rejects_non_nullable_add(tmp_path: Path) -> None:
    """``add_column`` without ``nullable=True`` is rejected (no backfill path)."""
    store = _bootstrap_doc(tmp_path)
    plan = MigrationPlan(
        target_version=2,
        operations=[
            {
                "op": "add_column",
                "table": "documents",
                "column": "summary",
                "type": "TEXT",
                # nullable=False -- needs backfill, not v1
            }
        ],
    )
    with pytest.raises(MigrationNotSupported) as excinfo:
        _run_migrate(store, plan)
    assert excinfo.value.context["reason"] == "non-nullable-add"
