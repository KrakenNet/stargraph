# SPDX-License-Identifier: Apache-2.0
"""SQLite-backed tiny model registry (FR-31, design §3.9.3).

Persists ``(model_id, version, runtime, file_uri, content_hash, framework,
metadata, created_at)`` rows in a local SQLite database, with a sibling
``model_aliases`` table providing MLflow-style ``production`` / ``staging``
pointer indirection. ``content_hash`` (sha256 of the model file bytes) is
verified at every load -- mismatch raises
:class:`stargraph.errors.IncompatibleModelHashError` and prevents a silent
model-file swap from going undetected (the version-skew gate).

v1 limits:

* ``file_uri`` MUST be a ``file://`` URI; any other scheme is rejected at
  ``load`` / ``load_alias`` time.
* No signing keys (deferred to plugin manifest signing in v1.x).
* Single-process: no inter-process locking. The expected use case is a
  developer or an in-process tool registering models that the same process
  later loads.
"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import aiosqlite
import orjson

from stargraph.errors import IncompatibleModelHashError, StargraphRuntimeError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["ModelEntry", "ModelRegistry"]


_RUNTIMES: frozenset[str] = frozenset({"sklearn", "xgboost", "onnx"})


# --------------------------------------------------------------------------- #
# Row shape                                                                   #
# --------------------------------------------------------------------------- #


class ModelEntry:
    """In-memory view of a ``models`` row (design §3.9.3, 8 columns).

    Attribute order mirrors the SQL column order. ``metadata`` is a decoded
    JSON dict; ``created_at`` is an ISO-8601 UTC string.
    """

    __slots__ = (
        "content_hash",
        "created_at",
        "file_uri",
        "framework",
        "metadata",
        "model_id",
        "runtime",
        "version",
    )

    def __init__(
        self,
        *,
        model_id: str,
        version: str,
        runtime: str,
        file_uri: str,
        content_hash: str,
        framework: str,
        metadata: dict[str, Any],
        created_at: str,
    ) -> None:
        self.model_id: str = model_id
        self.version: str = version
        self.runtime: str = runtime
        self.file_uri: str = file_uri
        self.content_hash: str = content_hash
        self.framework: str = framework
        self.metadata: dict[str, Any] = metadata
        self.created_at: str = created_at

    def __repr__(self) -> str:  # pragma: no cover -- debug aid
        return (
            f"ModelEntry(model_id={self.model_id!r}, version={self.version!r}, "
            f"runtime={self.runtime!r}, framework={self.framework!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelEntry):
            return NotImplemented
        return (
            self.model_id == other.model_id
            and self.version == other.version
            and self.runtime == other.runtime
            and self.file_uri == other.file_uri
            and self.content_hash == other.content_hash
            and self.framework == other.framework
            and self.metadata == other.metadata
            and self.created_at == other.created_at
        )

    def __hash__(self) -> int:
        return hash((self.model_id, self.version, self.content_hash))


# --------------------------------------------------------------------------- #
# Registry                                                                    #
# --------------------------------------------------------------------------- #


class ModelRegistry:
    """SQLite-backed registry for ``(model_id, version)`` model artifacts.

    Construction is cheap and synchronous; all I/O happens in the async
    methods. The first :py:meth:`bootstrap` call opens the aiosqlite
    connection, creates the ``models`` and ``model_aliases`` tables (and
    the ``idx_models_runtime`` index), and is idempotent on re-entry.
    """

    def __init__(self, path: Path | str) -> None:
        self._path: Path = Path(path)
        self._db: aiosqlite.Connection | None = None
        self._bootstrapped: bool = False

    # ----- Lifecycle ------------------------------------------------------ #

    async def bootstrap(self) -> None:
        """Open the SQLite connection and create the schema (idempotent)."""
        if self._bootstrapped:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        db = await aiosqlite.connect(self._path)
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS models (
                model_id      TEXT NOT NULL,
                version       TEXT NOT NULL,
                runtime       TEXT NOT NULL CHECK (runtime IN ('sklearn','xgboost','onnx')),
                file_uri      TEXT NOT NULL,
                content_hash  TEXT NOT NULL,
                framework     TEXT NOT NULL,
                metadata      TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                PRIMARY KEY (model_id, version)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS model_aliases (
                model_id  TEXT NOT NULL,
                alias     TEXT NOT NULL,
                version   TEXT NOT NULL,
                PRIMARY KEY (model_id, alias),
                FOREIGN KEY (model_id, version) REFERENCES models(model_id, version)
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_models_runtime ON models(runtime)")
        await db.commit()
        self._db = db
        self._bootstrapped = True

    async def close(self) -> None:
        """Close the underlying aiosqlite connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
        self._bootstrapped = False

    # ----- Mutators ------------------------------------------------------- #

    async def register(
        self,
        *,
        model_id: str,
        version: str,
        runtime: str,
        file_uri: str,
        content_hash: str,
        framework: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Insert (or replace) a ``(model_id, version)`` row.

        ``framework`` defaults to ``runtime`` (the design notes the duplicate
        column allows future ``sklearn-v2``-style framework variants under a
        single runtime). ``metadata`` defaults to an empty dict.
        ``runtime`` must be one of ``sklearn`` / ``xgboost`` / ``onnx``;
        the SQL CHECK constraint enforces this at write time, but we also
        validate up-front for a clearer error.
        """
        if runtime not in _RUNTIMES:
            raise ValueError(f"unknown runtime {runtime!r}; expected one of {sorted(_RUNTIMES)}")
        db = self._require_db()
        framework_value = runtime if framework is None else framework
        metadata_blob = orjson.dumps(dict(metadata) if metadata is not None else {}).decode("utf-8")
        created_at = datetime.now(UTC).isoformat()
        try:
            await db.execute(
                """
                INSERT OR REPLACE INTO models (
                    model_id, version, runtime, file_uri,
                    content_hash, framework, metadata, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id,
                    version,
                    runtime,
                    file_uri,
                    content_hash,
                    framework_value,
                    metadata_blob,
                    created_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"register failed for ({model_id!r}, {version!r}): {exc}") from exc
        await db.commit()

    async def alias(self, *, model_id: str, alias: str, version: str) -> None:
        """Point ``alias`` at ``(model_id, version)``.

        Raises :class:`KeyError` if the target ``(model_id, version)`` row
        does not exist (the ``FOREIGN KEY`` constraint catches this and is
        re-surfaced as a typed lookup miss for callers).
        """
        db = self._require_db()
        # Pre-check so we can surface KeyError rather than IntegrityError.
        async with db.execute(
            "SELECT 1 FROM models WHERE model_id = ? AND version = ?",
            (model_id, version),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise KeyError(f"alias target ({model_id!r}, {version!r}) is not registered")
        await db.execute(
            """
            INSERT OR REPLACE INTO model_aliases (model_id, alias, version)
            VALUES (?, ?, ?)
            """,
            (model_id, alias, version),
        )
        await db.commit()

    # ----- Lookups -------------------------------------------------------- #

    async def load(self, model_id: str, version: str) -> ModelEntry:
        """Fetch + verify a model entry by ``(model_id, version)``.

        Reads the registered file's bytes, recomputes sha256, and raises
        :class:`stargraph.errors.IncompatibleModelHashError` if the hash does
        not match the registry. Raises :class:`KeyError` when the row is
        absent and :class:`ValueError` when ``file_uri`` is not a
        ``file://`` URI.
        """
        db = self._require_db()
        async with db.execute(
            """
            SELECT model_id, version, runtime, file_uri,
                   content_hash, framework, metadata, created_at
              FROM models WHERE model_id = ? AND version = ?
            """,
            (model_id, version),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise KeyError(f"no model registered as ({model_id!r}, {version!r})")
        entry = _row_to_entry(row)
        _verify_content_hash(entry)
        return entry

    async def load_alias(self, model_id: str, alias: str) -> ModelEntry:
        """Resolve ``alias`` to a version then delegate to :meth:`load`."""
        db = self._require_db()
        async with db.execute(
            "SELECT version FROM model_aliases WHERE model_id = ? AND alias = ?",
            (model_id, alias),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise KeyError(f"no alias {alias!r} registered for model_id={model_id!r}")
        version = cast("str", row[0])
        return await self.load(model_id, version)

    # ----- Helpers -------------------------------------------------------- #

    def _require_db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise StargraphRuntimeError(
                "ModelRegistry not bootstrapped; call await registry.bootstrap() first",
                path=str(self._path),
            )
        return self._db


# --------------------------------------------------------------------------- #
# Module-private helpers                                                      #
# --------------------------------------------------------------------------- #


def _row_to_entry(row: tuple[Any, ...] | aiosqlite.Row) -> ModelEntry:
    """Hydrate a ``models`` row into a :class:`ModelEntry`."""
    (
        model_id,
        version,
        runtime,
        file_uri,
        content_hash,
        framework,
        metadata_blob,
        created_at,
    ) = tuple(row)
    metadata_obj = orjson.loads(metadata_blob)
    if not isinstance(metadata_obj, dict):
        raise ValueError(f"metadata for ({model_id!r}, {version!r}) is not a JSON object")
    return ModelEntry(
        model_id=cast("str", model_id),
        version=cast("str", version),
        runtime=cast("str", runtime),
        file_uri=cast("str", file_uri),
        content_hash=cast("str", content_hash),
        framework=cast("str", framework),
        metadata=cast("dict[str, Any]", metadata_obj),
        created_at=cast("str", created_at),
    )


def _verify_content_hash(entry: ModelEntry) -> None:
    """Recompute sha256 of the registered file and compare to the registry.

    Raises :class:`ValueError` if ``file_uri`` is not a ``file://`` URI (v1
    limitation), :class:`FileNotFoundError` if the file is gone, and
    :class:`stargraph.errors.IncompatibleModelHashError` on hash mismatch.
    """
    parsed = urlparse(entry.file_uri)
    if parsed.scheme != "file":
        raise ValueError(f"v1 model registry accepts only file:// URIs, got {entry.file_uri!r}")
    # urlparse on file:///abs/path yields netloc='' + path='/abs/path'.
    model_path = Path(parsed.path)
    digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
    if digest != entry.content_hash:
        raise IncompatibleModelHashError(
            "model file content hash does not match registry",
            model_id=entry.model_id,
            expected_sha256=entry.content_hash,
            actual_sha256=digest,
            model_path=str(model_path),
        )
