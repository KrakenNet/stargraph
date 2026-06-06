# SPDX-License-Identifier: Apache-2.0
"""Embed-hash drift gate -- NFR-4 loud-fail (FR-8, AC-2.4).

Phase 3 testing checkpoint for the FR-8 5-tuple drift gate
``(model_id, revision, content_hash, ndims, schema_v)`` written to the
LanceDB sidecar ``_stargraph_meta`` table at ``bootstrap()`` time.

Two integration tests:

1. :func:`test_bootstrap_writes_5tuple_metadata` -- bootstrapping a fresh
   :class:`~stargraph.stores.lancedb.LanceDBVectorStore` writes all five
   keys (``model_id``, ``revision``, ``content_hash``, ``ndims``,
   ``schema_v``) into the sidecar table; absence of any key is a
   silent-corruption regression.
2. :func:`test_reentry_with_mismatch_raises_IncompatibleEmbeddingHashError`
   -- re-opening the same on-disk store with an embedder whose
   ``content_hash`` differs raises
   :class:`~stargraph.errors.IncompatibleEmbeddingHashError` (NFR-4 loud-fail
   mandatory; silent acceptance would corrupt retrieval).

Tests use a lightweight in-test fake embedder
(:class:`_HashableFakeEmbedder`) that mirrors
:class:`~stargraph.stores.embeddings.FakeEmbedder`'s embed semantics but
exposes ``content_hash`` as a constructor kwarg so the second test can
flip the hash without touching production code.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

import lancedb  # pyright: ignore[reportMissingTypeStubs]
import numpy as np
import pytest

from stargraph.errors import IncompatibleEmbeddingHashError
from stargraph.stores._common import _EMBED_META_TABLE  # pyright: ignore[reportPrivateUsage]
from stargraph.stores.lancedb import LanceDBVectorStore

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.knowledge, pytest.mark.integration]


_EXPECTED_META_KEYS = frozenset(
    {"model_id", "revision", "content_hash", "ndims", "schema_v"},
)


class _HashableFakeEmbedder:
    """Test-only :class:`~stargraph.stores.embeddings.Embedding` with overridable identity.

    Mirrors :class:`~stargraph.stores.embeddings.FakeEmbedder`'s deterministic
    sha256-seeded embed semantics, but exposes ``content_hash`` (and
    ``model_id`` / ``revision``) as constructor kwargs so the drift-gate
    test can swap the hash on re-entry.
    """

    def __init__(
        self,
        *,
        content_hash: str,
        ndims: int = 4,
        model_id: str = "stargraph-fake-embedder",
        revision: str = "v1",
    ) -> None:
        self._content_hash = content_hash
        self._ndims = ndims
        self._model_id = model_id
        self._revision = revision

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def revision(self) -> str:
        return self._revision

    @property
    def content_hash(self) -> str:
        return self._content_hash

    @property
    def ndims(self) -> int:
        return self._ndims

    async def embed(
        self,
        texts: list[str],
        *,
        kind: Literal["query", "document"],
    ) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            seed = int.from_bytes(digest[:8], "big", signed=False)
            rng = np.random.default_rng(seed)
            vec = rng.standard_normal(self._ndims).astype(np.float32)
            norm = float(np.linalg.norm(vec))
            if norm > 0.0:
                vec = vec / norm
            out.append(vec.tolist())
        return out


async def test_bootstrap_writes_5tuple_metadata(tmp_path: Path) -> None:
    """Bootstrap writes all 5 FR-8 drift-gate keys into ``_stargraph_meta``."""
    embedder = _HashableFakeEmbedder(content_hash="aaa", ndims=4)
    store = LanceDBVectorStore(tmp_path / "vectors", embedder)
    await store.bootstrap()

    db = await lancedb.connect_async(tmp_path / "vectors")
    meta_tbl = await db.open_table(_EMBED_META_TABLE)
    rows = (await meta_tbl.query().to_arrow()).to_pylist()
    actual: dict[str, str] = {}
    for row in rows:
        key = row.get("key")
        value = row.get("value")
        if isinstance(key, str) and isinstance(value, str):
            actual[key] = value

    assert set(actual.keys()) == _EXPECTED_META_KEYS, f"missing 5-tuple keys; got {sorted(actual)}"
    assert actual["model_id"] == "stargraph-fake-embedder"
    assert actual["revision"] == "v1"
    assert actual["content_hash"] == "aaa"
    assert actual["ndims"] == "4"
    assert actual["schema_v"] == "1"


async def test_reentry_with_mismatch_raises_IncompatibleEmbeddingHashError(  # noqa: N802
    tmp_path: Path,
) -> None:
    """Re-opening with a different ``content_hash`` raises NFR-4 loud-fail."""
    path = tmp_path / "vectors"

    bootstrap_embedder = _HashableFakeEmbedder(content_hash="aaa", ndims=4)
    bootstrap_store = LanceDBVectorStore(path, bootstrap_embedder)
    await bootstrap_store.bootstrap()

    # Re-open the same on-disk path with a drifted content_hash.
    drifted_embedder = _HashableFakeEmbedder(content_hash="bbb", ndims=4)
    drifted_store = LanceDBVectorStore(path, drifted_embedder)

    with pytest.raises(IncompatibleEmbeddingHashError) as exc_info:
        await drifted_store.bootstrap()

    err = exc_info.value
    assert err.context["store"] == "lancedb"
    assert err.context["path"] == str(path)
    assert err.context["expected"]["content_hash"] == "bbb"
    assert err.context["actual"]["content_hash"] == "aaa"
