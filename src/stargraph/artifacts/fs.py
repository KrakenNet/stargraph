# SPDX-License-Identifier: Apache-2.0
"""Filesystem-backed :class:`ArtifactStore` provider (FR-91, design §10.2).

POSIX-local-only, BLAKE3 content-addressable artifact store. The
default :mod:`stargraph.artifacts` provider for v1 -- writes content +
sidecar metadata under ``<root>/<run_id>/<artifact_id>`` with the
standard temp-file + ``os.fsync`` + ``os.rename`` atomic-write idiom
(NFR-20). Identical content always yields identical ``artifact_id``
(NFR-21); ``fips_mode=True`` swaps BLAKE3 for SHA-256 so deployments
under FIPS 140-2 can use the same store.

Bootstrap-time NFS / SMB / AFP refusal (NFR-15) reuses the network-FS
prefix detector at :mod:`stargraph.checkpoint.migrations._network_fs` --
the checkpoint module's helper covers the same prefix set so a single
detector rules both subsystems out of "remote-FS-unsafe" deployments.

Phase 2 / consolidation: a future task should hoist the network-FS
detector + ``_detect_fs_type`` into a single ``stargraph.fs`` module so
``stargraph.checkpoint.sqlite`` and ``stargraph.artifacts.fs`` import from
one place; for now the artifact store reuses ``is_network_fs`` (the
checkpoint helper) directly.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from blake3 import blake3

from stargraph.artifacts.base import ArtifactRef
from stargraph.checkpoint.migrations._network_fs import is_network_fs
from stargraph.errors import ArtifactNotFound, ArtifactStoreError
from stargraph.stores._common import (
    StoreHealth,
    _detect_fs_type,  # pyright: ignore[reportPrivateUsage]
    _nfs_warning,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["FilesystemArtifactStore"]


_SIDE_SUFFIX = ".metadata.json"
"""Sidecar-metadata file suffix (design §10.2)."""


def _check_posix_local(path: Path) -> None:
    """Refuse NFS / SMB / AFP paths at bootstrap (NFR-15).

    Reuses :func:`stargraph.checkpoint.migrations._network_fs.is_network_fs`
    -- artifacts and checkpoints share the same "remote-FS-unsafe" set
    (the WAL safety guarantee for the checkpoint store and the atomic-
    rename guarantee for the artifact store both rest on POSIX-local
    filesystem semantics).

    Raises :class:`ArtifactStoreError` with ``backend="filesystem"`` /
    ``path=str(path)`` / ``reason="network-fs"`` on detection so callers
    can surface the deployment-misconfiguration cause.
    """
    if is_network_fs(path):
        raise ArtifactStoreError(
            f"refusing to bootstrap FilesystemArtifactStore on network filesystem: {path}",
            backend="filesystem",
            path=str(path),
            reason="network-fs",
        )


def _digest(content: bytes, *, fips_mode: bool) -> str:
    """Return the full hex digest of ``content`` (BLAKE3 or SHA-256 in FIPS).

    BLAKE3 is the default per design §10.2 / NFR-21 (faster than SHA-256,
    Merkle-tree friendly); ``fips_mode=True`` swaps in ``hashlib.sha256``
    so cleared deployments under FIPS 140-2 can still use the same
    store. Both algorithms produce a 64-char hex digest.
    """
    if fips_mode:
        return hashlib.sha256(content).hexdigest()
    return blake3(content).hexdigest()


class FilesystemArtifactStore:
    """POSIX-local, BLAKE3 content-addressable artifact store (design §10.2).

    Layout::

        <root>/
            <run_id>/
                <artifact_id>                 -- raw bytes
                <artifact_id>.metadata.json   -- sidecar JSON

    Sidecar fields: ``name``, ``content_type``, ``run_id``, ``step``,
    ``created_at`` (ISO-8601 UTC), ``content_hash``, plus the caller's
    raw ``metadata`` dict for round-trip preservation.

    ``artifact_id`` is the first 32 chars of the BLAKE3 (or SHA-256
    under FIPS) hex digest -- enough collision resistance for the v1
    artifact namespace while keeping path lengths short. ``content_hash``
    on the returned :class:`ArtifactRef` carries the full 64-char digest
    for integrity checks.
    """

    def __init__(self, root: Path, *, fips_mode: bool = False) -> None:
        self._root = root
        self._fips_mode = fips_mode

    async def bootstrap(self) -> None:
        """Create the root directory and refuse network filesystems (NFR-15).

        Idempotent: ``mkdir(parents=True, exist_ok=True)`` then
        :func:`_check_posix_local`. The NFS/SMB/AFP refusal happens
        *after* mkdir so the error message can name the resolved path
        even when the parent didn't exist.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        _check_posix_local(self._root)

    async def put(
        self,
        *,
        name: str,
        content: bytes,
        metadata: dict[str, Any],
        run_id: str,
        step: int,
    ) -> ArtifactRef:
        """Persist ``content`` atomically; return a content-addressable ref.

        Computes the digest, then writes content + sidecar atomically
        (temp file → ``os.fsync`` → ``os.rename``). Identical content
        under the same ``run_id`` always produces the same
        ``artifact_id`` -- the rename is overwrite-safe on POSIX.
        """
        full_hash = _digest(content, fips_mode=self._fips_mode)
        artifact_id = full_hash[:32]
        run_dir = self._root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / artifact_id
        sidecar = run_dir / f"{artifact_id}{_SIDE_SUFFIX}"
        created_at = datetime.now(UTC)
        content_type = str(metadata.get("content_type", "application/octet-stream"))
        sidecar_payload = {
            "name": name,
            "content_type": content_type,
            "run_id": run_id,
            "step": step,
            "created_at": created_at.isoformat(),
            "content_hash": full_hash,
            "metadata": metadata,
        }
        try:
            _atomic_write_bytes(target, content)
            _atomic_write_bytes(
                sidecar,
                json.dumps(sidecar_payload, sort_keys=True).encode("utf-8"),
            )
        except OSError as exc:
            raise ArtifactStoreError(
                f"atomic write failed for artifact {artifact_id} under {run_dir}: {exc}",
                backend="filesystem",
                path=str(target),
                run_id=run_id,
                artifact_id=artifact_id,
                reason="atomic-write",
            ) from exc
        return ArtifactRef(
            artifact_id=artifact_id,
            content_hash=full_hash,
            name=name,
            content_type=content_type,
            run_id=run_id,
            step=step,
            created_at=created_at,
        )

    async def get(self, artifact_id: str) -> bytes:
        """Return the raw bytes stored for ``artifact_id``.

        Walks ``<root>/*/<artifact_id>`` -- the run subdirectory is
        not part of the ``get`` API per design §10.1 (artifacts are
        addressed by content hash, not by run). Raises
        :class:`ArtifactNotFound` when no file matches.
        """
        for content_path in self._root.glob(f"*/{artifact_id}"):
            if content_path.is_file() and not content_path.name.endswith(_SIDE_SUFFIX):
                return content_path.read_bytes()
        raise ArtifactNotFound(
            f"artifact {artifact_id} not found under {self._root}",
            backend="filesystem",
            artifact_id=artifact_id,
            path=str(self._root),
        )

    async def list(self, run_id: str) -> list[ArtifactRef]:
        """Return every :class:`ArtifactRef` produced by ``run_id``.

        Enumerates ``<root>/<run_id>/*`` (excluding ``.metadata.json``
        sidecars), parses each sidecar to reconstruct the
        :class:`ArtifactRef`. Returns ``[]`` if the run directory does
        not exist (a run that wrote no artifacts is not an error).
        """
        run_dir = self._root / run_id
        if not run_dir.is_dir():
            return []
        refs: list[ArtifactRef] = []
        for content_path in sorted(run_dir.iterdir()):
            if content_path.name.endswith(_SIDE_SUFFIX) or not content_path.is_file():
                continue
            sidecar = run_dir / f"{content_path.name}{_SIDE_SUFFIX}"
            if not sidecar.is_file():
                # Orphan content (sidecar lost) — skip with no error so
                # ``list`` stays best-effort; ``get`` will still serve it.
                continue
            try:
                payload = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            try:
                refs.append(
                    ArtifactRef(
                        artifact_id=content_path.name,
                        content_hash=str(payload["content_hash"]),
                        name=str(payload["name"]),
                        content_type=str(payload["content_type"]),
                        run_id=str(payload["run_id"]),
                        step=int(payload["step"]),
                        created_at=datetime.fromisoformat(str(payload["created_at"])),
                    )
                )
            except (KeyError, ValueError):
                continue
        return refs

    async def delete(self, artifact_id: str) -> None:
        """Remove the content file and its sidecar for ``artifact_id``.

        Idempotent on the sidecar (a missing sidecar is not an error
        when content is present); raises :class:`ArtifactNotFound` if
        no content file exists for ``artifact_id`` under any run.
        """
        found = False
        for content_path in self._root.glob(f"*/{artifact_id}"):
            if not content_path.is_file() or content_path.name.endswith(_SIDE_SUFFIX):
                continue
            found = True
            sidecar = content_path.with_name(f"{content_path.name}{_SIDE_SUFFIX}")
            content_path.unlink(missing_ok=True)
            sidecar.unlink(missing_ok=True)
        if not found:
            raise ArtifactNotFound(
                f"artifact {artifact_id} not found under {self._root}",
                backend="filesystem",
                artifact_id=artifact_id,
                path=str(self._root),
            )

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (fs-type + writability).

        Mirrors the :mod:`stargraph.stores` health() pattern: detects the
        backing filesystem type (so callers see ``"nfs"`` / ``"cifs"``
        as a warning) and probes writability with a temp file. Lock
        state is reported as ``"free"`` -- the artifact store has no
        global lock; concurrent writes to distinct ``artifact_id`` are
        safe (content addressing) and concurrent writes to the same
        ``artifact_id`` are no-ops (rename of identical content).
        """
        fs_type = _detect_fs_type(self._root)
        warnings: list[str] = []
        nfs_warning = _nfs_warning(fs_type)
        if nfs_warning is not None:
            warnings.append(nfs_warning)
        writable = os.access(self._root, os.W_OK)
        if not writable:
            warnings.append(f"root {self._root} is not writable")
        return StoreHealth(
            ok=writable,
            version=1,
            fs_type=fs_type,
            lock_state="free",
            warnings=warnings,
        )

    async def migrate(self) -> None:
        """No-op migration (FR-91, design §10.2 -- BLAKE3 layout is identity-stable).

        BLAKE3 content addressing means the on-disk layout has no
        schema to evolve forward in v1: identical content always lands
        at the same path regardless of when it was written. Future
        changes (e.g. metadata-schema bump) will populate this hook.
        """
        return None


def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    """Write ``payload`` to ``target`` atomically (NFR-20).

    Standard POSIX idiom: write to ``<target>.tmp``, ``os.fsync(fd)``
    on the open file descriptor, then ``os.rename`` to the final
    name. ``os.rename`` is atomic within a single filesystem on POSIX,
    so partial writes never leave orphan files visible at ``target``.
    """
    tmp = target.with_name(f"{target.name}.tmp")
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.rename(tmp, target)
