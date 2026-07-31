# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.rag -- ``kind: rag``, retrieve-then-answer (P3b, T6).

The node owns its store bindings (the config surface P3a deferred):
each :class:`RagStoreBinding` names a provider + path, built at graph
load so misconfiguration fails at build time, not mid-run.

Retrieval is two-branch per binding kind:

* ``sqlite_doc`` -- the store has no native ranking, so the node scans
  up to ``scan_limit`` documents and ranks them with a deterministic
  lexical scorer (per-term saturating tf, :func:`lexical_score`).
  Content for the context block comes straight from the ranked docs.
* ``lancedb`` -- native ranked ``search(text=..., k=...)`` (FTS mode;
  the table must have been ingested with ``text`` rows). Content
  resolves from hit ``metadata["text"]`` when present, else from any
  bound doc store via ``get(id)`` -- the vector-index + doc-store pair
  is the intended production shape.

Branches fuse via :class:`~stargraph.stores.rerankers.RRFReranker`
(same fusion as :class:`~stargraph.nodes.retrieval.RetrievalNode`).
Answer synthesis follows the P3a preset pattern: a synthesized
``kind: dspy`` ChainOfThought over ``question, context -> answer``.
Zero hits short-circuits to ``answer=""`` -- the LM is never asked to
answer from nothing. Outputs: ``answer`` + ``citations`` (ranked
retrieved ids); Fathom rules route on the mirrored facts.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel as _PydanticBaseModel
from pydantic import ConfigDict, Field
from pydantic import ValidationError as _PydanticValidationError

from stargraph.errors import IRValidationError, StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.stores.rerankers import RRFReranker
from stargraph.stores.vector import Hit

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.ir._models import NodeSpec
    from stargraph.stores.doc import DocStore
    from stargraph.stores.vector import VectorStore

__all__ = ["RagNode", "RagNodeConfig", "RagStoreBinding", "lexical_score", "rag_node_from_config"]

_RAG_INSTRUCTIONS = (
    "Answer the question using ONLY the provided context. Cite the "
    "document ids you relied on in square brackets, e.g. [doc-3]. If "
    "the context is insufficient, say so instead of guessing."
)

#: Per-document excerpt cap for the context block.
_SNIPPET_CHARS = 1500

_TOKEN_RE = re.compile(r"\w+")


def lexical_score(query: str, text: str) -> float:
    """Deterministic lexical relevance: per-query-term saturating tf.

    ``score = Σ_{t in unique(query)} tf_t / (tf_t + 1)`` where ``tf_t``
    is the term's count in ``text`` (case-folded ``\\w+`` tokens).
    Saturation rewards matching more *distinct* query terms over
    repeating one -- a BM25-shaped curve without corpus statistics, so
    the score depends only on the (query, text) pair and replays
    identically.
    """
    doc_counts = Counter(_TOKEN_RE.findall(text.casefold()))
    score = 0.0
    for term in set(_TOKEN_RE.findall(query.casefold())):
        tf = doc_counts[term]
        if tf:
            score += tf / (tf + 1)
    return score


class RagStoreBinding(_PydanticBaseModel):
    """One store binding: provider + path (extra keys rejected)."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["sqlite_doc", "lancedb"]
    path: str
    table_name: str = "vectors"  # lancedb only


class RagNodeConfig(_PydanticBaseModel):
    """``NodeSpec.config`` schema for ``kind: rag`` (extra keys rejected)."""

    model_config = ConfigDict(extra="forbid")

    stores: list[RagStoreBinding] = Field(min_length=1)
    query: str = "query"
    k: int = Field(default=5, ge=1, le=50)
    scan_limit: int = Field(default=256, ge=1, le=10_000)
    instructions: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None


class _PromptState(_PydanticBaseModel):
    """Ephemeral state carrying the synthesized answer-signature inputs."""

    question: str
    context: str


class RagNode(NodeBase):
    """Retrieve from bound stores, fuse, answer with citations."""

    def __init__(
        self,
        *,
        inner: NodeBase,
        config: RagNodeConfig,
        doc_stores: list[DocStore],
        vector_stores: list[VectorStore],
    ) -> None:
        self._inner = inner
        self.config = config
        self._doc_stores = doc_stores
        self._vector_stores = vector_stores
        self._bootstrapped = False

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        if not hasattr(state, self.config.query):
            raise StargraphRuntimeError(
                f"rag node reads state field {self.config.query!r} "
                "which does not exist on the run state",
            )
        question = str(getattr(state, self.config.query))

        if not self._bootstrapped:
            for doc_store in self._doc_stores:
                await doc_store.bootstrap()
            self._bootstrapped = True

        branches: list[list[Hit]] = []
        content_by_id: dict[str, str] = {}
        for doc_store in self._doc_stores:
            branches.append(await self._lexical_branch(doc_store, question, content_by_id))
        for vector_store in self._vector_stores:
            branches.append(await vector_store.search(text=question, k=self.config.k))

        fused = await RRFReranker().fuse(branches, k=self.config.k, query=question)
        if not fused:
            # Nothing retrieved: never ask the LM to answer from nothing.
            return {"answer": "", "citations": []}

        context = await self._build_context(fused, content_by_id)
        outputs = await self._inner.execute(_PromptState(question=question, context=context), ctx)
        return {
            "answer": str(outputs.get("answer", "")),
            "citations": [hit.id for hit in fused],
        }

    async def _lexical_branch(
        self,
        doc_store: DocStore,
        question: str,
        content_by_id: dict[str, str],
    ) -> list[Hit]:
        """Scan-and-rank a doc store (no native ranking) into a Hit list."""
        docs = await doc_store.query(filter=None, limit=self.config.scan_limit)
        scored: list[tuple[float, str]] = []
        for doc in docs:
            text = doc.content if isinstance(doc.content, str) else ""
            content_by_id.setdefault(doc.id, text)
            score = lexical_score(question, text)
            if score > 0.0:
                scored.append((score, doc.id))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        return [
            Hit(id=doc_id, score=score, metadata={}) for score, doc_id in scored[: self.config.k]
        ]

    async def _build_context(
        self,
        hits: list[Hit],
        content_by_id: dict[str, str],
    ) -> str:
        """``[id] excerpt`` block; resolve content doc-scan > doc get > metadata."""
        lines: list[str] = []
        for hit in hits:
            text = content_by_id.get(hit.id, "")
            if not text:
                for doc_store in self._doc_stores:
                    doc = await doc_store.get(hit.id)
                    if doc is not None and isinstance(doc.content, str):
                        text = doc.content
                        break
            if not text:
                raw = hit.metadata.get("text", "")
                text = raw if isinstance(raw, str) else ""
            lines.append(f"[{hit.id}] {text[:_SNIPPET_CHARS]}".rstrip())
        return "\n".join(lines)


def _build_stores(
    spec_id: str, bindings: list[RagStoreBinding]
) -> tuple[list[DocStore], list[VectorStore]]:
    """Instantiate store bindings at graph load (fail-fast on misconfig)."""
    from pathlib import Path

    doc_stores: list[DocStore] = []
    vector_stores: list[VectorStore] = []
    for binding in bindings:
        if binding.provider == "sqlite_doc":
            from stargraph.stores.sqlite_doc import SQLiteDocStore

            doc_stores.append(SQLiteDocStore(Path(binding.path)))
        else:  # lancedb -- behind the ``stores`` extra
            try:
                from stargraph.stores.lancedb import LanceDBVectorStore
            except ImportError as e:
                raise StargraphRuntimeError(
                    f"rag node {spec_id!r}: store provider 'lancedb' needs the stores extra",
                    hint="pip install stargraph[stores]",
                ) from e
            from stargraph.stores.embeddings import FakeEmbedder

            # v1 searches FTS (text) mode only, which never touches the
            # embedder -- the placeholder satisfies the constructor
            # without pulling model weights.
            vector_stores.append(
                LanceDBVectorStore(
                    Path(binding.path),
                    FakeEmbedder(),
                    table_name=binding.table_name,
                )
            )
    return doc_stores, vector_stores


def rag_node_from_config(spec: NodeSpec) -> RagNode:
    """Build a :class:`RagNode` from ``NodeSpec.config`` (``kind: rag``)."""
    try:
        cfg = RagNodeConfig.model_validate(spec.config)
    except _PydanticValidationError as e:
        raise IRValidationError(f"rag node {spec.id!r}: invalid config: {e}") from e
    if not cfg.query.isidentifier():
        raise IRValidationError(
            f"rag node {spec.id!r}: query {cfg.query!r} must be a valid "
            "identifier (it names the run-state input field)"
        )

    dspy_config: dict[str, Any] = {
        "signature": "question, context -> answer",
        "module": "cot",
        "instructions": cfg.instructions or _RAG_INSTRUCTIONS,
    }
    if cfg.model is not None:
        dspy_config["model"] = cfg.model
    if cfg.api_base is not None:
        dspy_config["api_base"] = cfg.api_base
    if cfg.api_key_env is not None:
        dspy_config["api_key_env"] = cfg.api_key_env

    from stargraph.nodes.dspy import dspy_node_from_config

    inner = dspy_node_from_config(spec.model_copy(update={"config": dspy_config}))
    doc_stores, vector_stores = _build_stores(spec.id, cfg.stores)
    return RagNode(inner=inner, config=cfg, doc_stores=doc_stores, vector_stores=vector_stores)
