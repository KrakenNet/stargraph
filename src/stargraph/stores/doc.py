# SPDX-License-Identifier: Apache-2.0
"""DocStore Protocol + Document (FR-4, design §3.3).

Defines the structural contract every document-store provider implements:
``bootstrap / health / migrate`` lifecycle (shared with all
``stargraph.stores`` Protocols) plus per-store CRUD (``put``, ``get``,
``query``). Concrete providers (``SQLiteDocStore`` lands later in this
spec) implement :class:`DocStore` structurally; no inheritance required.

:class:`Document` is the row returned by ``get`` / ``query`` -- ``id``,
``content`` (text or bytes), free-form ``metadata`` dict, and
``created_at`` timestamp. Metadata round-trips through orjson JSONB so
nested structures are preserved (design §3.3).
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from stargraph.stores._common import MigrationPlan, StoreHealth  # noqa: TC001

__all__ = [
    "DocStore",
    "Document",
]


class Document(BaseModel):
    """Document row returned by :meth:`DocStore.get` / :meth:`DocStore.query`.

    ``content`` may be text or bytes; providers persist both via the
    shared JSONB codec. ``metadata`` stays typed as ``dict[str, Any]``
    (rather than the JSON-scalar union used by
    :data:`stargraph.stores.vector.MetadataValue`): DocStore is the
    catch-all unstructured-payload tier, metadata round-trips through
    orjson JSONB so nested dicts / lists are preserved, and the
    columnar restrictions that justify scalar-only metadata for
    :class:`stargraph.stores.vector.Row` do not apply (NFR-3).
    """

    id: str
    content: str | bytes
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


@runtime_checkable
class DocStore(Protocol):
    """Structural contract for document-store providers (design §3.3).

    Implementations: :class:`stargraph.stores.sqlite_doc.SQLiteDocStore`
    (arrives later in this spec). Lifecycle (``bootstrap`` / ``health``
    / ``migrate``) shared with every ``stargraph.stores`` Protocol; per-store
    CRUD (``put`` / ``get`` / ``query``) is doc-specific.
    """

    async def bootstrap(self) -> None:
        """Idempotent schema/metadata bootstrap (FR-8 inherited)."""
        ...

    async def health(self) -> StoreHealth:
        """Return a :class:`StoreHealth` snapshot (FR-9 fs-type / lock-state)."""
        ...

    async def migrate(self, plan: MigrationPlan) -> None:
        """Apply a :class:`MigrationPlan`; v1 supports ``add_column`` only."""
        ...

    async def put(
        self,
        doc_id: str,
        content: str | bytes,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Insert-or-replace document ``doc_id`` with ``content`` and ``metadata``."""
        ...

    async def get(self, doc_id: str) -> Document | None:
        """Return the :class:`Document` for ``doc_id`` or ``None`` if absent."""
        ...

    async def query(
        self,
        filter: str | None = None,  # noqa: A002
        *,
        limit: int = 100,
    ) -> list[Document]:
        """Return up to ``limit`` documents matching the SQL ``filter`` clause."""
        ...
