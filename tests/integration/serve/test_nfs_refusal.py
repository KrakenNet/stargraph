# SPDX-License-Identifier: Apache-2.0
"""Integration: NFS refusal at bootstrap (NFR-15, design §16.9).

Both ``SQLiteCheckpointer`` and ``FilesystemArtifactStore`` MUST refuse to
bootstrap on a network filesystem (NFS / SMB / AFP / CIFS). The artifact
store's bootstrap path is partly covered by ``tests/unit/artifacts/test_fs.py``
(NFR-15 unit case). This integration file pins the **dual-module contract**:
both refuse consistently when the same network-FS detector returns ``True``.

Mocks the public seam ``stargraph.checkpoint.sqlite.is_network_fs`` and
``stargraph.artifacts.fs.is_network_fs`` (both imported at module top from
``stargraph.checkpoint.migrations._network_fs``) to force-detect a network
filesystem regardless of the actual ``tmp_path`` location.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from stargraph.artifacts.fs import FilesystemArtifactStore
from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.errors import ArtifactStoreError, CheckpointError

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.serve


def _force_network_fs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch both module-level seams to return ``True`` for any path."""

    def _always_true(_path: Path) -> bool:
        return True

    monkeypatch.setattr("stargraph.checkpoint.sqlite.is_network_fs", _always_true)
    monkeypatch.setattr("stargraph.artifacts.fs.is_network_fs", _always_true)


def test_checkpointer_refuses_nfs_at_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``SQLiteCheckpointer.bootstrap()`` raises ``CheckpointError`` with ``reason='network-fs'``.

    Construction is cheap + synchronous; refusal lands in ``bootstrap``
    per the documented lifecycle (design §3.2.3, §3.2.5).
    """
    _force_network_fs(monkeypatch)
    cp = SQLiteCheckpointer(tmp_path / "nfs-checkpoints.db")

    with pytest.raises(CheckpointError) as excinfo:
        asyncio.run(cp.bootstrap())

    assert excinfo.value.context.get("reason") == "network-fs"
    assert "network FS" in str(excinfo.value) or "network-fs" in str(excinfo.value)


def test_artifact_store_refuses_nfs_at_bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``FilesystemArtifactStore.bootstrap()`` raises ``ArtifactStoreError`` (NFR-15).

    Constructor still succeeds (plain attach); refusal happens in
    ``bootstrap()`` after the idempotent ``mkdir(parents=True, exist_ok=True)``.
    """
    _force_network_fs(monkeypatch)
    store = FilesystemArtifactStore(tmp_path / "nfs-artifacts")

    with pytest.raises(ArtifactStoreError) as excinfo:
        asyncio.run(store.bootstrap())

    assert excinfo.value.context.get("reason") == "network-fs"
    assert excinfo.value.context.get("backend") == "filesystem"


def test_both_modules_refuse_consistently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Single test exercising both bootstrap paths under the same patched seam.

    The integration angle: when an operator deploys Stargraph on a network-FS
    mount by mistake, BOTH the durable-state surface (checkpointer) and the
    durable-output surface (artifact store) must refuse before any side
    effect lands. This test asserts both refusals fire from a single
    monkeypatch session — i.e. the dual-module contract is uniform.
    """
    _force_network_fs(monkeypatch)

    cp = SQLiteCheckpointer(tmp_path / "nfs.db")
    store = FilesystemArtifactStore(tmp_path / "nfs-store")

    with pytest.raises(CheckpointError) as cp_exc:
        asyncio.run(cp.bootstrap())
    assert cp_exc.value.context.get("reason") == "network-fs"

    with pytest.raises(ArtifactStoreError) as store_exc:
        asyncio.run(store.bootstrap())
    assert store_exc.value.context.get("reason") == "network-fs"


@pytest.mark.parametrize(
    "fs_kind",
    ["nfs4", "smb", "cifs", "afp"],
    ids=["nfs4", "smb", "cifs", "afp"],
)
def test_all_documented_network_fs_kinds_refused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fs_kind: str,
) -> None:
    """Bonus: every documented network-FS kind triggers the same refusal.

    The actual ``is_network_fs`` helper matches by path-prefix regex
    (``^/mnt/``, ``^//``, etc.) rather than fstype-name introspection;
    this test parametrizes over the *conceptual* kinds the design names
    (NFS / SMB / CIFS / AFP) and asserts uniform behavior at the
    bootstrap seam regardless of which kind the detector flagged. Patches
    the public seam to return ``True`` regardless of input.
    """
    del fs_kind  # parametrized for ID-tagging; behavior is uniform

    def _always_true(_path: Path) -> bool:
        return True

    monkeypatch.setattr("stargraph.checkpoint.sqlite.is_network_fs", _always_true)
    monkeypatch.setattr("stargraph.artifacts.fs.is_network_fs", _always_true)

    cp = SQLiteCheckpointer(tmp_path / "x.db")
    store = FilesystemArtifactStore(tmp_path / "x-store")

    with pytest.raises(CheckpointError):
        asyncio.run(cp.bootstrap())
    with pytest.raises(ArtifactStoreError):
        asyncio.run(store.bootstrap())
