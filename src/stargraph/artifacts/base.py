# SPDX-License-Identifier: Apache-2.0
"""ArtifactStore Protocol + ArtifactRef Pydantic record (FR-90, design §10.1).

Defines the structural contract every artifact-store provider implements:
``bootstrap / health / migrate`` lifecycle (mirrors the
:mod:`stargraph.stores` Protocol pattern) plus content-addressable
``put / get / list / delete`` CRUD. Concrete providers
(:class:`stargraph.artifacts.fs.FilesystemArtifactStore` arrives in task
1.13) implement :class:`ArtifactStore` structurally; no inheritance
required.

:class:`ArtifactRef` is the row returned by ``put`` and ``list`` --
``artifact_id`` (BLAKE3(content)[:32], or SHA-256 in FIPS), full
``content_hash``, ``name`` / ``content_type`` / ``run_id`` / ``step``
provenance, and ``created_at`` UTC timestamp.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 -- pydantic resolves at runtime
from typing import Any, Protocol, runtime_checkable

from stargraph.ir._models import IRBase
from stargraph.stores._common import StoreHealth  # noqa: TC001 -- pydantic resolves at runtime

__all__ = [
    "ArtifactRef",
    "ArtifactStore",
]


class ArtifactRef(IRBase):
    """Content-addressable artifact handle returned by :meth:`ArtifactStore.put`.

    ``artifact_id`` is BLAKE3(content)[:32] (SHA-256 fallback in FIPS)
    so identical content always yields identical ids; ``content_hash``
    carries the full digest for integrity checks (design §10.1, NFR-21).
    """

    artifact_id: str
    """Content-addressable id: BLAKE3(content)[:32] (SHA-256 in FIPS)."""
    content_hash: str
    """Full BLAKE3 digest (or SHA-256 under ``fips_mode=true``)."""
    name: str
    """Caller-supplied logical name (e.g. ``"remediation.pdf"``)."""
    content_type: str
    """MIME type (e.g. ``"application/pdf"``)."""
    run_id: str
    """Run that produced this artifact."""
    step: int
    """Step index within the run."""
    created_at: datetime
    """UTC timestamp of the ``put`` call."""


@runtime_checkable
class ArtifactStore(Protocol):
    """Structural contract for artifact-store providers (design §10.1).

    Implementations: :class:`stargraph.artifacts.fs.FilesystemArtifactStore`
    (arrives in task 1.13). Lifecycle (``bootstrap`` / ``health`` /
    ``migrate``) mirrors every :mod:`stargraph.stores` Protocol; per-store
    CRUD (``put`` / ``get`` / ``list`` / ``delete``) is artifact-specific
    and content-addressable.
    """

    async def put(
        self,
        *,
        name: str,
        content: bytes,
        metadata: dict[str, Any],
        run_id: str,
        step: int,
    ) -> ArtifactRef:
        """Persist ``content`` under ``name``; return its content-addressable ref."""
        ...

    async def get(self, artifact_id: str) -> bytes:
        """Return the raw bytes for ``artifact_id``."""
        ...

    async def list(self, run_id: str) -> list[ArtifactRef]:
        """Return every :class:`ArtifactRef` produced by ``run_id``."""
        ...

    async def delete(self, artifact_id: str) -> None:
        """Remove the artifact (and sidecar) for ``artifact_id``."""
        ...

    async def bootstrap(self) -> None:
        """Idempotent root/schema bootstrap (NFS/SMB/AFP refusal, NFR-15)."""
        ...

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (fs-type / lock-state)."""
        ...

    async def migrate(self) -> None:
        """Apply forward-safe schema migrations (no-op in v1)."""
        ...
