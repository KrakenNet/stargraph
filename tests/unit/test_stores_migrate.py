# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``RyuGraphStore.migrate`` / ``LanceDBVectorStore.migrate``
validate-then-noop semantics (T03 / T04).

Pins the contract that both stores accept a valid ``add_column nullable=True``
``MigrationPlan`` silently (matching the sibling pattern at
``stores/sqlite_fact.py:115-123`` / ``stores/sqlite_memory.py:136-144``) and
raise :class:`MigrationNotSupported` on unsupported ops (the shared
validator at ``stores/_common.py:62-85`` is the source of truth).

NOTE: T03/T04 are gated behind the ``stores`` optional extra
(ryugraph / lancedb / pyarrow). When the extra is absent at collection
time, ``tests/conftest.py:32-75`` skips the import-heavy integration
tests; the unit tests below avoid module-top-level imports of the store
classes so they can collect on the no-extras path -- the import lives
inside each test and is gated by ``importlib.util.find_spec`` so the
tests skip cleanly when the optional providers are absent.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from harbor.errors import MigrationNotSupported
from harbor.stores._common import MigrationPlan

pytestmark = pytest.mark.unit


def _add_column_plan() -> MigrationPlan:
    """Return a valid ``add_column nullable=True`` plan."""
    return MigrationPlan(
        target_version=2,
        operations=[
            {
                "op": "add_column",
                "table": "rows",
                "column": "new_col",
                "type": "TEXT",
                "nullable": True,
            }
        ],
    )


def _drop_column_plan() -> MigrationPlan:
    """Return an unsupported ``drop_column`` plan."""
    return MigrationPlan(
        target_version=2,
        operations=[
            {
                "op": "drop_column",
                "table": "rows",
                "column": "old_col",
            }
        ],
    )


@pytest.mark.skipif(
    importlib.util.find_spec("ryugraph") is None,
    reason="requires the `stores` extra (ryugraph)",
)
async def test_ryugraph_migrate_valid_add_column_succeeds_silently(
    tmp_path: object,
) -> None:
    """``RyuGraphStore.migrate(add_column nullable=True)`` returns ``None``
    without raising (T03)."""
    from harbor.stores.ryugraph import RyuGraphStore

    store = RyuGraphStore(Path(str(tmp_path)))
    result = await store.migrate(_add_column_plan())
    assert result is None


@pytest.mark.skipif(
    importlib.util.find_spec("ryugraph") is None,
    reason="requires the `stores` extra (ryugraph)",
)
async def test_ryugraph_migrate_rejects_drop_column(tmp_path: object) -> None:
    """``RyuGraphStore.migrate(drop_column)`` raises ``MigrationNotSupported`` (T03)."""
    from harbor.stores.ryugraph import RyuGraphStore

    store = RyuGraphStore(Path(str(tmp_path)))
    with pytest.raises(MigrationNotSupported):
        await store.migrate(_drop_column_plan())


@pytest.mark.skipif(
    importlib.util.find_spec("lancedb") is None,
    reason="requires the `stores` extra (lancedb)",
)
async def test_lancedb_migrate_valid_add_column_succeeds_silently(
    tmp_path: object,
) -> None:
    """``LanceDBVectorStore.migrate(add_column nullable=True)`` returns ``None``
    without raising (T04)."""
    from harbor.stores.embeddings import FakeEmbedder
    from harbor.stores.lancedb import LanceDBVectorStore

    store = LanceDBVectorStore(Path(str(tmp_path)), FakeEmbedder())
    result = await store.migrate(_add_column_plan())
    assert result is None


@pytest.mark.skipif(
    importlib.util.find_spec("lancedb") is None,
    reason="requires the `stores` extra (lancedb)",
)
async def test_lancedb_migrate_rejects_drop_column(tmp_path: object) -> None:
    """``LanceDBVectorStore.migrate(drop_column)`` raises ``MigrationNotSupported`` (T04)."""
    from harbor.stores.embeddings import FakeEmbedder
    from harbor.stores.lancedb import LanceDBVectorStore

    store = LanceDBVectorStore(Path(str(tmp_path)), FakeEmbedder())
    with pytest.raises(MigrationNotSupported):
        await store.migrate(_drop_column_plan())
