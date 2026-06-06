# SPDX-License-Identifier: Apache-2.0
"""LanceDB versioning + engine checkpoint integration test (FR-10, AC-2.1).

Pins the reproducibility hook called out in design §3.1: every LanceDB
upsert bumps :py:meth:`AsyncTable.version`, and the engine checkpoint
records that integer so a counterfactual replay can ``checkout(version)``
the exact dataset snapshot that produced the run.

Three behaviours are exercised end-to-end against a real on-disk LanceDB
table and a real :class:`~stargraph.checkpoint.sqlite.SQLiteCheckpointer`:

1. :func:`test_checkpoint_records_lance_version` -- upsert N rows, ask the
   store for :py:meth:`current_version`, write a :class:`Checkpoint`
   carrying the version under ``state['lance_version']``, reload via
   :py:meth:`SQLiteCheckpointer.read_latest`, and assert the recorded
   version round-trips and ``checkout(version)`` returns N rows.
2. :func:`test_counterfactual_checkout_prior_version` -- record version
   ``v1`` after the first upsert, perform a second upsert, then assert
   ``checkout(v1)`` returns only the rows that existed at ``v1``. This
   pins counterfactual replay against the historical snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import lancedb  # pyright: ignore[reportMissingTypeStubs]
import pytest

from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.stores.embeddings import FakeEmbedder
from stargraph.stores.lancedb import LanceDBVectorStore
from stargraph.stores.vector import Row

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_NDIMS = 4
_TABLE = "vectors"


def _rows(prefix: str, n: int) -> list[Row]:
    return [
        Row(id=f"{prefix}{i}", text=f"row {prefix}{i}", metadata={"tag": prefix}) for i in range(n)
    ]


def _make_checkpoint(
    *,
    run_id: str,
    step: int,
    state: dict[str, object],
) -> Checkpoint:
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash="sha256:graph",
        runtime_hash="sha256:runtime",
        state=state,
        clips_facts=[],
        last_node="upsert",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side",
    )


async def _open_table_at_version(path: Path, version: int) -> int:
    """Return the row count of ``path`` checked out at ``version``."""
    db = await lancedb.connect_async(path)
    tbl = await db.open_table(_TABLE)
    await tbl.checkout(version)
    return await tbl.count_rows()


async def test_checkpoint_records_lance_version(tmp_path: Path) -> None:
    """Upsert N rows, write checkpoint with version, reload, checkout matches N."""
    n = 5
    store_path = tmp_path / "vectors"
    store = LanceDBVectorStore(store_path, FakeEmbedder(ndims=_NDIMS))
    await store.bootstrap()
    await store.upsert(_rows("a", n))

    version = await store.current_version()
    assert version >= 1

    cp = SQLiteCheckpointer(tmp_path / "ckpt.sqlite")
    await cp.bootstrap()
    try:
        await cp.write(
            _make_checkpoint(
                run_id="run-1",
                step=0,
                state={"lance_version": version, "store": "vectors"},
            )
        )

        loaded = await cp.read_latest("run-1")
        assert loaded is not None
        assert loaded.state["lance_version"] == version
    finally:
        await cp.close()

    # Reproducibility: checkout the recorded version returns the same N rows.
    count = await _open_table_at_version(store_path, version)
    assert count == n


async def test_counterfactual_checkout_prior_version(tmp_path: Path) -> None:
    """Checkout of the prior version returns the prior row count, not the latest."""
    store_path = tmp_path / "vectors"
    store = LanceDBVectorStore(store_path, FakeEmbedder(ndims=_NDIMS))
    await store.bootstrap()

    # First upsert: 3 rows, record version v1.
    await store.upsert(_rows("a", 3))
    v1 = await store.current_version()

    # Second upsert: +2 rows (5 total), record version v2.
    await store.upsert(_rows("b", 2))
    v2 = await store.current_version()

    assert v2 > v1

    cp = SQLiteCheckpointer(tmp_path / "ckpt.sqlite")
    await cp.bootstrap()
    try:
        await cp.write(
            _make_checkpoint(
                run_id="run-cf",
                step=0,
                state={"lance_version": v1},
            )
        )
        await cp.write(
            _make_checkpoint(
                run_id="run-cf",
                step=1,
                state={"lance_version": v2},
            )
        )

        prior = await cp.read_at_step("run-cf", 0)
        latest = await cp.read_at_step("run-cf", 1)
        assert prior is not None
        assert latest is not None
        assert prior.state["lance_version"] == v1
        assert latest.state["lance_version"] == v2
    finally:
        await cp.close()

    # Counterfactual replay: checkout v1 sees only the first 3 rows; v2 sees 5.
    assert await _open_table_at_version(store_path, v1) == 3
    assert await _open_table_at_version(store_path, v2) == 5
