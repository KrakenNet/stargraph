# SPDX-License-Identifier: Apache-2.0
"""Common store types and helpers.

Shared types declared by every ``stargraph.stores`` Protocol per design
§3.1: :class:`StoreHealth` returned from ``health()``, :class:`MigrationPlan`
consumed by ``migrate()``, and a process-local registry of
:class:`asyncio.Lock` instances enforcing single-writer-per-path
semantics (FR-9). :func:`_detect_fs_type` lets ``health()`` warn on
networked filesystems where SQLite/LanceDB locking is unsafe.
:func:`_write_embed_metadata` / :func:`_verify_embed_metadata` implement
the FR-8 5-tuple drift gate shared across stores (design §3.1).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from stargraph.errors import IncompatibleEmbeddingHashError, MigrationNotSupported

if TYPE_CHECKING:
    from stargraph.stores.embeddings import Embedding


class StoreHealth(BaseModel):
    """Health snapshot returned by ``Store.health()`` (design §3.1)."""

    ok: bool
    version: int
    fragment_count: int | None = None
    node_count: int | None = None
    embedding_hash: str | None = None
    fs_type: str
    lock_state: Literal["free", "held"]
    warnings: list[str] = Field(default_factory=list)


class MigrationPlan(BaseModel):
    """Migration plan accepted by ``Store.migrate()`` (design §3.1).

    v1 only supports ``add_column`` operations; the schema permits
    arbitrary dict shapes so future ops (rename, drop) are non-breaking.
    """

    target_version: int
    operations: list[dict[str, object]]


# FR-17: ``migrate()`` v1 supports add-nullable-column only. Type narrows,
# renames, and drops cannot be applied forward-safely on Lance fragments
# without a rewrite, so the helper rejects them up-front so every store
# (including ``NotImplementedError`` POC stubs) surfaces the same loud
# ``MigrationNotSupported`` for unsupported ops.
_SUPPORTED_MIGRATION_OPS = frozenset({"add_column"})


def _validate_migration_plan(plan: MigrationPlan, *, store: str) -> None:  # pyright: ignore[reportUnusedFunction]
    """Reject any operation outside the FR-17 add-nullable-column subset.

    Raises :class:`MigrationNotSupported` if ``plan`` contains a
    type narrow, column rename, drop, or any other op-name the v1
    schema does not enumerate. ``store`` populates the error context
    for downstream telemetry.
    """
    for op in plan.operations:
        op_name = op.get("op")
        if op_name not in _SUPPORTED_MIGRATION_OPS:
            raise MigrationNotSupported(
                f"migration op {op_name!r} is not supported in v1 (add_column only)",
                store=store,
                operation=op_name,
                reason="v1-add-column-only",
            )
        if op_name == "add_column" and not op.get("nullable", False):
            raise MigrationNotSupported(
                "add_column requires nullable=True (non-nullable adds need a backfill)",
                store=store,
                operation=op_name,
                reason="non-nullable-add",
            )


# Process-local single-writer locks keyed by resolved store path.
# Lazy-created on first access via :func:`_lock_for`. Concurrent
# upsert/delete/migrate calls against the same on-disk store serialize
# through this registry (FR-9). Single-process only — multi-process
# write is a v1.x concern (design §3.1, §4.3).
_LOCKS: dict[Path, asyncio.Lock] = {}


def _lock_for(path: Path) -> asyncio.Lock:  # pyright: ignore[reportUnusedFunction]
    """Return the :class:`asyncio.Lock` guarding ``path``, creating one if needed."""
    key = path.resolve() if path.exists() else path
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


_NFS_FSTYPES = frozenset({"nfs", "nfs4", "smb", "smbfs", "cifs"})


def _nfs_warning(fs_type: str) -> str | None:  # pyright: ignore[reportUnusedFunction]
    """Return an FR-9 NFS warning string when ``fs_type`` is networked, else ``None``.

    Single-writer locks are process-local (:func:`_lock_for`); networked
    filesystems (NFS / SMB / CIFS) cannot enforce them across hosts, so
    ``health()`` surfaces this as a warning per FR-9 / AC-2.5.
    """
    if fs_type.lower() in _NFS_FSTYPES:
        return (
            f"networked filesystem detected ({fs_type}); "
            "single-writer locks are unsafe across hosts (FR-9)"
        )
    return None


def _detect_fs_type(path: Path) -> str:  # pyright: ignore[reportUnusedFunction]
    """Best-effort filesystem-type probe for ``path``.

    Returns canonical names like ``"ext4"``, ``"tmpfs"``, ``"nfs"``,
    ``"smbfs"``, ``"cifs"`` so callers can warn on networked
    filesystems. Returns ``"unknown"`` when detection is unsupported
    (non-POSIX) or fails.
    """
    if not sys.platform.startswith("linux") and not sys.platform.startswith("darwin"):
        return "unknown"

    try:
        statvfs = os.statvfs(path)
    except OSError:
        return "unknown"

    # macOS / BSD expose f_basetype on the statvfs result; Linux does not.
    basetype = getattr(statvfs, "f_basetype", None)
    if isinstance(basetype, str) and basetype:
        return basetype

    # Linux: walk /proc/mounts and pick the longest mountpoint prefix
    # of ``path`` (the mount actually backing it).
    try:
        target = path.resolve() if path.exists() else path.absolute()
    except OSError:
        return "unknown"

    try:
        with open("/proc/mounts", encoding="utf-8") as fh:
            mounts = fh.readlines()
    except OSError:
        return "unknown"

    best_mount = ""
    best_fstype = "unknown"
    target_str = str(target)
    for line in mounts:
        parts = line.split()
        if len(parts) < 3:
            continue
        mountpoint = parts[1]
        fstype = parts[2]
        if (
            target_str == mountpoint
            or target_str.startswith(mountpoint.rstrip("/") + "/")
            or mountpoint == "/"
        ) and len(mountpoint) >= len(best_mount):
            best_mount = mountpoint
            best_fstype = fstype
    return best_fstype


# FR-8 5-tuple drift gate keys, stable order for sidecar serialisation.
_EMBED_META_TABLE = "_stargraph_meta"
_EMBED_META_KEYS = ("model_id", "revision", "content_hash", "ndims", "schema_v")


def _expected_embed_meta(embedder: Embedding, schema_v: int) -> dict[str, str]:
    """Build the expected 5-tuple meta dict for ``embedder`` at ``schema_v``."""
    return {
        "model_id": embedder.model_id,
        "revision": embedder.revision,
        "content_hash": embedder.content_hash,
        "ndims": str(embedder.ndims),
        "schema_v": str(schema_v),
    }


async def _write_embed_metadata(  # pyright: ignore[reportUnusedFunction]
    db: Any,
    embedder: Embedding,
    schema_v: int,
) -> None:
    """Create the ``_stargraph_meta`` sidecar table with the FR-8 5-tuple.

    Caller must have verified the table does not already exist; LanceDB
    ``create_table`` raises on duplicate.
    """
    # pyarrow is a stores-extra dependency (LanceDB / RyuGraph rely on it);
    # import lazily so engine-only test jobs that don't install the stores
    # extra can still load the stargraph.stores._common module for shared types
    # like ``StoreHealth`` / ``MigrationPlan``.
    import pyarrow as pa

    meta = _expected_embed_meta(embedder, schema_v)
    schema = pa.schema(
        [pa.field("key", pa.string()), pa.field("value", pa.string())],
    )
    keys = [k for k in _EMBED_META_KEYS if k in meta]
    values = [meta[k] for k in keys]
    data = pa.table(
        {"key": pa.array(keys, type=pa.string()), "value": pa.array(values, type=pa.string())},
        schema=schema,
    )
    meta_tbl = await db.create_table(_EMBED_META_TABLE, schema=schema)
    await meta_tbl.add(data)


async def _verify_embed_metadata(  # pyright: ignore[reportUnusedFunction]
    db: Any,
    embedder: Embedding,
    schema_v: int,
    *,
    store: str,
    path: Path,
    table: str,
) -> None:
    """Read the ``_stargraph_meta`` sidecar and raise on FR-8 5-tuple drift.

    ``store`` / ``path`` / ``table`` populate
    :class:`IncompatibleEmbeddingHashError` for telemetry.
    """
    expected = _expected_embed_meta(embedder, schema_v)
    meta_tbl = await db.open_table(_EMBED_META_TABLE)
    rows = (await meta_tbl.query().to_arrow()).to_pylist()
    actual: dict[str, str] = {}
    for row in rows:
        key = row.get("key")
        value = row.get("value")
        if isinstance(key, str) and isinstance(value, str):
            actual[key] = value
    if actual != expected:
        raise IncompatibleEmbeddingHashError(
            "embedding identity drift detected on re-entry",
            store=store,
            path=str(path),
            table=table,
            expected=expected,
            actual=actual,
        )
