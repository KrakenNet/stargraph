# SPDX-License-Identifier: Apache-2.0
"""``Store.health()`` warns on networked filesystems (FR-9, AC-2.5).

Each default provider exposes ``health() -> StoreHealth``; the returned
``warnings`` list must include an NFS / network-FS warning when the
on-disk path lives on ``nfs`` / ``smb`` / ``cifs``. Single-writer locks
(:func:`harbor.stores._common._lock_for`) are process-local, so they
cannot serialise concurrent writers across hosts -- the warning is the
operator-visible signal mandated by FR-9.

Each test monkey-patches :func:`harbor.stores._common._detect_fs_type`
in the **provider module's** namespace (the import binds it locally) to
return a networked fstype, then asserts ``StoreHealth.warnings`` carries
a string mentioning the filesystem type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from harbor.stores.embeddings import FakeEmbedder
from harbor.stores.lancedb import LanceDBVectorStore
from harbor.stores.ryugraph import RyuGraphStore
from harbor.stores.sqlite_doc import SQLiteDocStore
from harbor.stores.sqlite_fact import SQLiteFactStore
from harbor.stores.sqlite_memory import SQLiteMemoryStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_NDIMS = 4


def _has_nfs_warning(warnings: list[str], fs_type: str) -> bool:
    """Return ``True`` when ``warnings`` mentions ``fs_type`` (FR-9 phrasing)."""
    return any(fs_type in w and "filesystem" in w.lower() for w in warnings)


async def test_lancedb_health_warns_on_nfs(tmp_path: Path) -> None:
    """LanceDB ``health()`` flags ``nfs`` paths via the FR-9 warning."""
    store = LanceDBVectorStore(tmp_path / "vectors", FakeEmbedder(ndims=_NDIMS))
    await store.bootstrap()
    with patch("harbor.stores.lancedb._detect_fs_type", return_value="nfs"):
        health = await store.health()
    assert health.fs_type == "nfs"
    assert _has_nfs_warning(health.warnings, "nfs")


async def test_kuzu_health_warns_on_nfs(tmp_path: Path) -> None:
    """Kuzu ``health()`` flags ``smb`` paths via the FR-9 warning."""
    store = RyuGraphStore(tmp_path / "graph")
    await store.bootstrap()
    with patch("harbor.stores.ryugraph._detect_fs_type", return_value="smb"):
        health = await store.health()
    assert health.fs_type == "smb"
    assert _has_nfs_warning(health.warnings, "smb")


async def test_sqlite_doc_health_warns_on_nfs(tmp_path: Path) -> None:
    """SQLite doc-store ``health()`` flags ``cifs`` paths via the FR-9 warning."""
    store = SQLiteDocStore(tmp_path / "docs.db")
    await store.bootstrap()
    with patch("harbor.stores.sqlite_doc._detect_fs_type", return_value="cifs"):
        health = await store.health()
    assert health.fs_type == "cifs"
    assert _has_nfs_warning(health.warnings, "cifs")


async def test_sqlite_memory_health_warns_on_nfs(tmp_path: Path) -> None:
    """SQLite memory-store ``health()`` flags ``nfs4`` paths via the FR-9 warning."""
    store = SQLiteMemoryStore(tmp_path / "memory.db")
    await store.bootstrap()
    with patch("harbor.stores.sqlite_memory._detect_fs_type", return_value="nfs4"):
        health = await store.health()
    assert health.fs_type == "nfs4"
    assert _has_nfs_warning(health.warnings, "nfs4")


async def test_sqlite_fact_health_warns_on_nfs(tmp_path: Path) -> None:
    """SQLite fact-store ``health()`` flags ``smbfs`` paths via the FR-9 warning."""
    store = SQLiteFactStore(tmp_path / "facts.db")
    await store.bootstrap()
    with patch("harbor.stores.sqlite_fact._detect_fs_type", return_value="smbfs"):
        health = await store.health()
    assert health.fs_type == "smbfs"
    assert _has_nfs_warning(health.warnings, "smbfs")
