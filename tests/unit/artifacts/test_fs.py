# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.artifacts.fs` (FR-91, NFR-15, NFR-20, NFR-21).

Covers the BLAKE3 content-addressable contract, atomic-write idempotency,
NFS / SMB / AFP refusal at bootstrap, and the SHA-256 fallback under
``STARGRAPH_FIPS_MODE=1``.
"""

from __future__ import annotations

import hashlib
import os
from typing import TYPE_CHECKING

import pytest
from blake3 import blake3

from stargraph.artifacts.fs import FilesystemArtifactStore
from stargraph.errors import ArtifactStoreError

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    """Return a bootstrapped :class:`FilesystemArtifactStore` rooted at ``tmp_path``."""
    return FilesystemArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def fips_store(tmp_path: Path) -> FilesystemArtifactStore:
    """Return a bootstrapped FIPS-mode store (SHA-256 hashing)."""
    return FilesystemArtifactStore(tmp_path / "artifacts-fips", fips_mode=True)


async def _bootstrap(store: FilesystemArtifactStore) -> FilesystemArtifactStore:
    await store.bootstrap()
    return store


async def test_blake3_content_addressing_same_bytes_same_id(
    store: FilesystemArtifactStore,
) -> None:
    """Storing identical bytes twice yields the same ``artifact_id`` (NFR-21).

    Content-addressable: ``artifact_id = BLAKE3(content)[:32]``. Two
    independent ``put`` calls with the same payload must produce
    bit-identical ids; ``content_hash`` must equal the full BLAKE3 hex.
    """
    await _bootstrap(store)
    payload = b"the quick brown fox jumps over the lazy dog"
    ref1 = await store.put(
        name="fox.txt",
        content=payload,
        metadata={"content_type": "text/plain"},
        run_id="run-1",
        step=0,
    )
    ref2 = await store.put(
        name="fox-again.txt",
        content=payload,
        metadata={"content_type": "text/plain"},
        run_id="run-1",
        step=1,
    )
    assert ref1.artifact_id == ref2.artifact_id
    expected_full = blake3(payload).hexdigest()
    assert ref1.content_hash == expected_full
    assert ref1.artifact_id == expected_full[:32]


async def test_different_content_different_artifact_id(
    store: FilesystemArtifactStore,
) -> None:
    """Distinct payloads produce distinct ``artifact_id``s (collision-resistance contract)."""
    await _bootstrap(store)
    ref_a = await store.put(
        name="a.bin",
        content=b"alpha",
        metadata={},
        run_id="run-1",
        step=0,
    )
    ref_b = await store.put(
        name="b.bin",
        content=b"beta",
        metadata={},
        run_id="run-1",
        step=1,
    )
    assert ref_a.artifact_id != ref_b.artifact_id
    assert ref_a.content_hash != ref_b.content_hash


async def test_atomic_write_temp_then_rename(
    store: FilesystemArtifactStore,
    tmp_path: Path,
) -> None:
    """``put`` writes via temp file + rename; final file matches payload exactly (NFR-20).

    Asserts the standard POSIX atomic-write idiom by:

    1. Confirming the final content path exists and contains the exact
       bytes (round-trip via :meth:`get`).
    2. Confirming no ``.tmp`` sibling lingers after a clean ``put`` --
       the rename target consumed it.
    """
    await _bootstrap(store)
    payload = b"atomic-write-payload"
    ref = await store.put(
        name="a.bin",
        content=payload,
        metadata={},
        run_id="run-atomic",
        step=0,
    )
    fetched = await store.get(ref.artifact_id)
    assert fetched == payload
    run_dir = (tmp_path / "artifacts") / "run-atomic"
    # Final content + sidecar are present; no orphan .tmp files.
    leftover_tmps = list(run_dir.glob("*.tmp"))
    assert leftover_tmps == [], f"orphan temp files found: {leftover_tmps}"


async def test_atomic_write_failure_no_partial_artifact(
    store: FilesystemArtifactStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filesystem failure mid-write leaves no half-written artifact at the target.

    Mocks ``os.rename`` (the final atomic step) to raise ``OSError``;
    the store must wrap it in :class:`ArtifactStoreError` and the
    target path must not exist (atomic-rename guarantee: failure
    leaves only the temp file, never a partial target).
    """
    await _bootstrap(store)

    real_rename = os.rename

    def _failing_rename(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        # Fail the *content* rename; allow sidecar renames to pass.
        s = str(src)
        if s.endswith(".tmp") and not s.endswith(".metadata.json.tmp"):
            raise OSError("simulated mid-write failure")
        real_rename(src, dst)

    monkeypatch.setattr("stargraph.artifacts.fs.os.rename", _failing_rename)

    with pytest.raises(ArtifactStoreError) as excinfo:
        await store.put(
            name="bad.bin",
            content=b"will-not-land",
            metadata={},
            run_id="run-fail",
            step=0,
        )
    assert excinfo.value.context.get("reason") == "atomic-write"
    # No final artifact-id file landed at target.
    run_dir = (tmp_path / "artifacts") / "run-fail"
    if run_dir.is_dir():
        non_tmp_files = [p for p in run_dir.iterdir() if not p.name.endswith(".tmp")]
        assert non_tmp_files == [], (
            f"partial artifact landed despite rename failure: {non_tmp_files}"
        )


def test_nfs_refusal_at_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap on a network-FS path raises :class:`ArtifactStoreError` (NFR-15).

    Mocks the public seam :func:`stargraph.artifacts.fs.is_network_fs` to
    return ``True`` so the store believes its root sits on a
    network mount. The constructor itself does not refuse (it is a
    plain dataclass-shaped attach); refusal happens in
    :meth:`bootstrap` per the documented lifecycle.
    """

    def _always_network(_path: Path) -> bool:
        return True

    monkeypatch.setattr("stargraph.artifacts.fs.is_network_fs", _always_network)
    store = FilesystemArtifactStore(tmp_path / "nfs-root")

    with pytest.raises(ArtifactStoreError) as excinfo:
        # ``bootstrap`` is async but the network-fs raise happens after
        # ``mkdir``; the exception bubbles synchronously from ``await``.
        import asyncio

        asyncio.run(store.bootstrap())
    assert excinfo.value.context.get("reason") == "network-fs"
    assert excinfo.value.context.get("backend") == "filesystem"


async def test_sha256_fallback_under_fips_mode(
    fips_store: FilesystemArtifactStore,
) -> None:
    """``fips_mode=True`` swaps BLAKE3 for SHA-256; ``content_hash`` matches SHA-256 hex.

    Same payload through a non-FIPS store and a FIPS store yields
    *different* ``content_hash`` values: the FIPS digest equals
    ``hashlib.sha256(content).hexdigest()`` and the non-FIPS digest
    equals ``blake3(content).hexdigest()``.
    """
    await _bootstrap(fips_store)
    payload = b"fips-payload"
    ref = await fips_store.put(
        name="f.bin",
        content=payload,
        metadata={},
        run_id="run-fips",
        step=0,
    )
    expected_sha = hashlib.sha256(payload).hexdigest()
    expected_blake = blake3(payload).hexdigest()
    assert ref.content_hash == expected_sha
    assert ref.content_hash != expected_blake
    assert ref.artifact_id == expected_sha[:32]


async def test_identical_content_dedup_artifact_id(
    store: FilesystemArtifactStore,
) -> None:
    """Identical content across different runs still yields identical ``artifact_id``.

    Locks the deduplication contract from NFR-21: content-addressing is
    a function of bytes only — ``run_id`` / ``step`` / ``name`` /
    ``metadata`` do not perturb ``artifact_id``. (The sidecar JSON
    differs across runs; the addressable key does not.)
    """
    await _bootstrap(store)
    payload = b"dedup-me"
    ref_run_a = await store.put(
        name="dedup-a.bin",
        content=payload,
        metadata={"caller": "run-a"},
        run_id="run-a",
        step=0,
    )
    ref_run_b = await store.put(
        name="dedup-b.bin",
        content=payload,
        metadata={"caller": "run-b"},
        run_id="run-b",
        step=7,
    )
    assert ref_run_a.artifact_id == ref_run_b.artifact_id
    assert ref_run_a.content_hash == ref_run_b.content_hash
