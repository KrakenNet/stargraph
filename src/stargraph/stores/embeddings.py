# SPDX-License-Identifier: Apache-2.0
"""Embedding Protocol + MiniLMEmbedder POC (FR-14, FR-15, design §3.1).

Defines the structural contract every embedder implements -- the four
identity properties (``model_id`` / ``revision`` / ``content_hash`` /
``ndims``) that compose the 5-tuple drift gate (FR-8) plus an async
:meth:`Embedding.embed` that accepts ``kind: Literal["query","document"]``
for forward-compat with asymmetric models (bge / e5 v5+). MiniLM ignores
``kind`` because all-MiniLM-L6-v2 is symmetric, but the Protocol requires
the parameter day 1.

:class:`MiniLMEmbedder` is the POC reference implementation. Three load
modes per design §3.1:

* **Mode 1** -- explicit ``model_path`` (local directory). Used by tests
  and for fully-offline operators who pre-stage the directory.
* **Mode 2** -- ``HF_HUB_OFFLINE=1`` env var: cache-only load through
  ``huggingface_hub.snapshot_download(local_files_only=True)``. No
  network. Raises if the cache is empty.
* **Mode 3** -- explicit ``allow_download`` constructor kwarg.
  ``allow_download=False`` matches mode 2 semantics; ``True`` (default)
  permits a network fetch into the HF cache on first use.

After the model directory is resolved, the safetensors weights file is
hashed and compared against :data:`MINILM_SHA256`; any drift raises
:class:`stargraph.errors.EmbeddingModelHashMismatch` (FR-15). Token clip:
:meth:`embed` truncates inputs to ``MINILM_MAX_TOKENS`` (256) with a
structlog warning so a long document does not silently degrade
retrieval quality past the model's positional window.

Sync ``SentenceTransformer.encode`` is wrapped via
:func:`asyncio.to_thread` so callers stay non-blocking.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from stargraph.errors import EmbeddingModelHashMismatch
from stargraph.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "MINILM_MAX_TOKENS",
    "MINILM_MODEL_ID",
    "MINILM_NDIMS",
    "MINILM_SHA256",
    "Embedding",
    "FakeEmbedder",
    "MiniLMEmbedder",
]


MINILM_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
"""HuggingFace model id pinned for the POC embedder (design §3.1)."""

MINILM_NDIMS = 384
"""MiniLM embedding dimensionality -- hard-coded per model card."""

MINILM_SHA256 = "53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db"
"""Pinned safetensors sha256 for all-MiniLM-L6-v2 (FR-15 verification)."""

MINILM_MAX_TOKENS = 256
"""MiniLM positional window cap; inputs longer than this are clipped."""

_SAFETENSORS_FILENAME = "model.safetensors"
_HF_OFFLINE_ENV = "HF_HUB_OFFLINE"
_log = get_logger(__name__)


@runtime_checkable
class Embedding(Protocol):
    """Structural contract for embedders (design §3.1).

    The four identity properties feed the 5-tuple drift gate
    ``(model_id, revision, content_hash, ndims, schema_v)`` written into
    LanceDB schema metadata at :meth:`VectorStore.bootstrap` time
    (FR-8). Mismatch on re-entry raises
    :class:`stargraph.errors.IncompatibleEmbeddingHashError`.
    """

    @property
    def model_id(self) -> str:
        """HuggingFace model id (e.g. ``sentence-transformers/all-MiniLM-L6-v2``)."""
        ...

    @property
    def revision(self) -> str:
        """Model revision / commit-sha pin (HF revision hash)."""
        ...

    @property
    def content_hash(self) -> str:
        """Safetensors sha256 of the model weights (FR-15 verification)."""
        ...

    @property
    def ndims(self) -> int:
        """Embedding output dimensionality."""
        ...

    async def embed(
        self,
        texts: list[str],
        *,
        kind: Literal["query", "document"],
    ) -> list[list[float]]:
        """Encode ``texts`` into embedding vectors.

        ``kind`` is required by the Protocol for forward-compat with
        asymmetric models (bge / e5 v5+); symmetric models (MiniLM) are
        free to ignore it.
        """
        ...


def _hash_safetensors(path: Path) -> str:
    """Return the SHA-256 hex digest of ``path``, streaming to bound memory."""
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _resolve_model_dir(
    *,
    model_id: str,
    revision: str,
    allow_download: bool,
) -> Path:
    """Resolve the on-disk model directory for ``model_id``/``revision``.

    Honours ``HF_HUB_OFFLINE=1`` (mode 2) and the explicit
    ``allow_download`` flag (mode 3): when either forbids the network,
    we pass ``local_files_only=True`` to ``snapshot_download``, which
    raises if the cache is empty. Otherwise the call may fetch.
    """
    from huggingface_hub import snapshot_download  # pyright: ignore[reportUnknownVariableType]

    offline = os.environ.get(_HF_OFFLINE_ENV) == "1"
    local_only = offline or not allow_download
    local_path: str = snapshot_download(  # pyright: ignore[reportUnknownVariableType]
        repo_id=model_id,
        revision=revision,
        local_files_only=local_only,
    )
    return Path(local_path)


class MiniLMEmbedder:
    """POC ``Embedding`` for ``sentence-transformers/all-MiniLM-L6-v2``.

    Constructor accepts either an explicit ``model_path`` (mode 1) or a
    cache/network resolve through HuggingFace Hub (modes 2/3). After the
    weights are on disk the safetensors file's sha256 is compared
    against :data:`MINILM_SHA256` and any drift raises
    :class:`stargraph.errors.EmbeddingModelHashMismatch`.
    """

    def __init__(
        self,
        *,
        model_path: Path | None = None,
        model_id: str = MINILM_MODEL_ID,
        revision: str = "main",
        allow_download: bool = True,
        expected_sha256: str = MINILM_SHA256,
    ) -> None:
        if model_path is None:
            model_dir = _resolve_model_dir(
                model_id=model_id,
                revision=revision,
                allow_download=allow_download,
            )
        else:
            model_dir = model_path

        weights_path = model_dir / _SAFETENSORS_FILENAME
        actual_sha = _hash_safetensors(weights_path)
        if actual_sha != expected_sha256:
            raise EmbeddingModelHashMismatch(
                "MiniLM safetensors sha256 mismatch",
                model_id=model_id,
                expected_sha256=expected_sha256,
                actual_sha256=actual_sha,
                model_path=str(weights_path),
            )

        # Lazy import so stargraph doesn't pull sentence-transformers on
        # every `import stargraph.stores`.
        from sentence_transformers import SentenceTransformer

        self._model: SentenceTransformer = SentenceTransformer(str(model_dir))
        self._model_path = model_dir
        self._model_id = model_id
        self._revision = revision
        self._content_hash = actual_sha

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
        return MINILM_NDIMS

    async def embed(
        self,
        texts: list[str],
        *,
        kind: Literal["query", "document"],
    ) -> list[list[float]]:
        """Encode ``texts`` via :meth:`SentenceTransformer.encode` off-thread.

        ``kind`` is accepted but ignored -- MiniLM is symmetric. Inputs
        longer than :data:`MINILM_MAX_TOKENS` are clipped (with a
        structlog warning) before encoding to stay inside the model's
        positional window.
        """
        clipped = self._clip_inputs(texts)
        return await asyncio.to_thread(self._encode_sync, clipped)

    def _clip_inputs(self, texts: Iterable[str]) -> list[str]:
        """Token-clip each input to :data:`MINILM_MAX_TOKENS` and warn on truncation."""
        tokenizer = self._model.tokenizer  # pyright: ignore[reportUnknownMemberType]
        out: list[str] = []
        for text in texts:
            ids = tokenizer.encode(text, add_special_tokens=False)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            if len(ids) > MINILM_MAX_TOKENS:  # pyright: ignore[reportUnknownArgumentType]
                _log.warning(
                    "minilm.input_clipped",
                    original_tokens=len(ids),  # pyright: ignore[reportUnknownArgumentType]
                    max_tokens=MINILM_MAX_TOKENS,
                )
                truncated_ids = ids[:MINILM_MAX_TOKENS]  # pyright: ignore[reportUnknownVariableType]
                out.append(
                    tokenizer.decode(truncated_ids, skip_special_tokens=True)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                )
            else:
                out.append(text)
        return out

    def _encode_sync(self, texts: list[str]) -> list[list[float]]:
        """Sync ``encode`` shim -- isolates ``sentence-transformers`` typing.

        ``SentenceTransformer.encode`` has eight overloads; pyright
        cannot pick one through :func:`asyncio.to_thread`. Boxing the
        call here lets us localise the ``cast`` and keep the public
        :meth:`embed` signature clean.
        """
        from typing import cast

        vectors = cast(
            "list[list[float]]",
            self._model.encode(  # pyright: ignore[reportUnknownMemberType]
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
            ).tolist(),
        )
        return vectors

    @classmethod
    def fake(cls, ndims: int = MINILM_NDIMS) -> FakeEmbedder:
        """Deterministic POC fake -- hashes input strings into fixed vectors.

        Returns a :class:`FakeEmbedder` (still satisfies :class:`Embedding`
        structurally) that never loads ``sentence-transformers``. Used by
        Phase-1 POC smoke tests where the real MiniLM weights are
        unavailable. ``content_hash`` is a stable sentinel so the FR-8
        drift gate still round-trips through ``bootstrap``.
        """
        return FakeEmbedder(ndims=ndims)


class FakeEmbedder:
    """Deterministic, dependency-free :class:`Embedding` for POC tests.

    Hashes each input string with sha256, seeds :class:`numpy.random` with
    the digest, and emits an L2-normalised vector of length ``ndims``.
    Matches MiniLM's symmetric ignore-``kind`` semantics. Not for
    production; smoke tests only.
    """

    _CONTENT_HASH = "fake-" + "0" * 59  # 64-char sentinel mirroring sha256 width.

    def __init__(self, *, ndims: int = MINILM_NDIMS) -> None:
        self._ndims = ndims

    @property
    def model_id(self) -> str:
        return "stargraph-fake-embedder"

    @property
    def revision(self) -> str:
        return "v1"

    @property
    def content_hash(self) -> str:
        return self._CONTENT_HASH

    @property
    def ndims(self) -> int:
        return self._ndims

    async def embed(
        self,
        texts: list[str],
        *,
        kind: Literal["query", "document"],
    ) -> list[list[float]]:
        import hashlib

        import numpy as np

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
