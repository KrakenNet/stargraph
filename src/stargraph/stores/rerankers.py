# SPDX-License-Identifier: Apache-2.0
"""Reranker Protocol + RRFReranker default + CrossEncoderReranker (FR-16, design §3.8).

Defines the structural contract used by ``RetrievalNode`` to fuse per-store
hit lists into a single ranked list. Two implementations ship in tree:

* :class:`RRFReranker` -- always-available Reciprocal Rank Fusion; no
  query needed; pure-Python; the documented default.
* :class:`CrossEncoderReranker` -- opt-in (``skills-rag`` extra); loads
  a sentence-transformers ``CrossEncoder`` model and scores
  ``(query, doc_text)`` pairs at fuse time. Registered under the
  ``stargraph.rerankers`` entry-point group as ``cross-encoder`` so it
  resolves through :func:`stargraph.stores._rerank_loader.load_reranker`.

Both honor a single :class:`Reranker` Protocol whose :meth:`fuse`
takes an optional ``query`` kwarg -- model-free fusers ignore it,
cross-encoder + LLM-based rerankers use it.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from stargraph.errors import StargraphRuntimeError
from stargraph.stores.vector import Hit

__all__ = [
    "CrossEncoderReranker",
    "RRFReranker",
    "Reranker",
]


#: Default cross-encoder model. The MS-MARCO MiniLM variant is the
#: canonical "small + fast + good enough" baseline cited in the
#: sentence-transformers cross-encoder docs; ~22M params, CPU-friendly,
#: Apache-2.0 license. Override via :class:`CrossEncoderReranker`'s
#: ``model`` kwarg or the ``STARGRAPH_CROSS_ENCODER_MODEL`` env var.
_DEFAULT_CE_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

#: Conventional metadata keys searched for the document text in fuse
#: input. First non-empty string wins. Hits with no resolvable text
#: fall through to the hit ``id`` so the cross-encoder still gets a
#: signal (better than dropping the hit silently).
_TEXT_METADATA_KEYS = ("text", "content", "body", "passage", "chunk")


@runtime_checkable
class Reranker(Protocol):
    """Structural contract for rerankers (design §3.8).

    Implementations fuse ``per_store`` (one ranked list per store) into a
    single ranked list of length ``<= k``. ``query`` is the search-time
    string a node passes through from state; model-free fusers
    (RRF, weighted-sum) ignore it, cross-encoder / LLM-judge rerankers
    consume it.
    """

    async def fuse(
        self,
        per_store: list[list[Hit]],
        *,
        k: int,
        query: str | None = None,
    ) -> list[Hit]:
        """Fuse per-store ranked lists into a single top-``k`` list."""
        ...


class RRFReranker:
    """Reciprocal Rank Fusion reranker (design §3.8).

    For each :class:`Hit` appearing in any per-store list, the fused
    score is ``Σ_lists 1/(k_param + rank_in_list)`` where ``rank_in_list``
    is 1-based. Hits sharing an ``id`` across stores are de-duplicated
    and their RRF contributions summed; the first-seen ``metadata`` is
    retained. Output is sorted by fused score desc and truncated to
    ``k``. Query is ignored (model-free fusion).
    """

    def __init__(self, k_param: int = 60) -> None:
        self.k_param = k_param

    async def fuse(
        self,
        per_store: list[list[Hit]],
        *,
        k: int,
        query: str | None = None,
    ) -> list[Hit]:
        del query  # RRF is model-free
        # Collect per-id contributions as a list, then sum in sorted order.
        # Float addition is non-associative, so ordering the addends makes
        # the fused score depend only on the multiset of contributions --
        # required for the permutation-invariance guarantee in design §3.8.
        contributions: dict[str, list[float]] = {}
        first_seen: dict[str, Hit] = {}
        for hits in per_store:
            for rank, hit in enumerate(hits, start=1):
                contributions.setdefault(hit.id, []).append(1.0 / (self.k_param + rank))
                if hit.id not in first_seen:
                    first_seen[hit.id] = hit
        scores = {hit_id: sum(sorted(parts)) for hit_id, parts in contributions.items()}
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [
            Hit(id=hit_id, score=score, metadata=first_seen[hit_id].metadata)
            for hit_id, score in ranked[:k]
        ]


class CrossEncoderReranker:
    """Sentence-transformers ``CrossEncoder``-backed reranker.

    Re-scores hits by feeding ``(query, doc_text)`` pairs through a
    cross-encoder model and returning the top-``k`` by model score
    descending. Document text is read from the first non-empty
    :data:`_TEXT_METADATA_KEYS` key on each hit's ``metadata``;
    hits with no resolvable text fall back to the hit ``id`` so the
    model still gets a signal.

    When ``query`` is ``None`` (caller did not propagate one) the
    reranker raises :class:`StargraphRuntimeError` rather than silently
    falling through to RRF -- the cross-encoder is opt-in and a
    missing query is a wiring bug worth surfacing loudly.

    Heavyweight: requires the ``skills-rag`` extra
    (``sentence-transformers`` + ``torch``). The model is loaded
    lazily on first :meth:`fuse` call so import-time cost stays
    bounded; subsequent fuses share the cached instance.

    Inference runs in :func:`asyncio.to_thread` so the event loop is
    never blocked on a model forward pass.
    """

    def __init__(
        self,
        *,
        model: str = _DEFAULT_CE_MODEL,
        device: str | None = None,
        batch_size: int = 32,
    ) -> None:
        self._model_name = model
        self._device = device
        self._batch_size = batch_size
        self._encoder: Any | None = None  # lazy: sentence_transformers.CrossEncoder

    def _load_encoder(self) -> Any:
        """Import + instantiate the CrossEncoder on first call."""
        if self._encoder is not None:
            return self._encoder
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:  # pragma: no cover - depends on extras
            raise StargraphRuntimeError(
                "CrossEncoderReranker requires the 'skills-rag' extra "
                "(sentence-transformers + torch). Install via "
                "`pip install stargraph[skills-rag]`."
            ) from exc
        self._encoder = CrossEncoder(self._model_name, device=self._device)
        return self._encoder

    async def fuse(
        self,
        per_store: list[list[Hit]],
        *,
        k: int,
        query: str | None = None,
    ) -> list[Hit]:
        if not query:
            raise StargraphRuntimeError(
                "CrossEncoderReranker.fuse() requires a non-empty 'query'; "
                "the cross-encoder needs (query, doc) pairs to score."
            )

        # Deduplicate hits across per-store lists by id. First-seen
        # metadata is retained -- matches RRFReranker's policy so callers
        # can swap rerankers without metadata drift.
        first_seen: dict[str, Hit] = {}
        for hits in per_store:
            for hit in hits:
                if hit.id not in first_seen:
                    first_seen[hit.id] = hit

        if not first_seen:
            return []

        ordered = list(first_seen.values())
        docs = [_extract_doc_text(hit) for hit in ordered]
        pairs = [(query, doc) for doc in docs]

        encoder = self._load_encoder()

        import asyncio

        scores = await asyncio.to_thread(encoder.predict, pairs, batch_size=self._batch_size)

        scored = list(zip(ordered, [float(s) for s in scores], strict=True))
        scored.sort(key=lambda pair: (-pair[1], pair[0].id))
        return [Hit(id=hit.id, score=score, metadata=hit.metadata) for hit, score in scored[:k]]


def _extract_doc_text(hit: Hit) -> str:
    """Pull a document text string out of ``hit.metadata``.

    Searches :data:`_TEXT_METADATA_KEYS` in order; first non-empty
    string wins. Falls back to ``hit.id`` so the cross-encoder always
    has *some* token to score on (better than dropping the hit).
    """
    metadata = hit.metadata or {}
    for key in _TEXT_METADATA_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return hit.id
