# SPDX-License-Identifier: Apache-2.0
"""RagSkill POC reference -- retrieval + LLM-stub answer assembly (FR-32, AC-7.1).

Phase-1 POC scaffold for the in-tree ``rag`` reference skill (design §3.10).
Composes :class:`~harbor.nodes.retrieval.RetrievalNode` (vector + doc only)
with a stub LLM call and an answer-assembly step that always emits a
``sources`` list pointing back to retrieved hit ids.

POC scope:

* No real LLM call -- ``_llm_stub`` formats top-``k`` hits into a string
  ``"Based on N sources: ..."`` so the smoke path runs without any model
  dependency. Phase 2 promotes this to the engine model registry per
  design §3.9.
* Capability requirements declared on the manifest:
  ``db.vectors:read``, ``db.docs:read``, ``llm.generate``. The capabilities
  gate (FR-7) enforces these at run admission; the POC declares them so
  the gate has the data to check against once it lands.
* Subgraph IR is tracked separately under
  ``tests/fixtures/skills/rag/example.yaml``; Phase 2 loads it via
  :attr:`Skill.subgraph` and routes execution through ``SubGraphNode``.

The :class:`RagSkill.run` coroutine drives retrieve → llm-stub → assemble
on a :class:`RagState` instance and returns the updated state with
``answer`` and ``sources`` populated. Engine wiring (LangGraph IR +
checkpointer) lands in Phase 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import dspy  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from harbor.adapters.dspy import _install_filter  # pyright: ignore[reportPrivateUsage]
from harbor.logging import get_logger
from harbor.nodes.retrieval import RetrievalNode
from harbor.skills.base import Skill, SkillKind
from harbor.stores.vector import Hit  # noqa: TC001 -- used as Pydantic field type

if TYPE_CHECKING:
    from collections.abc import Callable

    from harbor.ir._models import StoreRef
    from harbor.nodes.base import ExecutionContext
    from harbor.stores.doc import DocStore
    from harbor.stores.vector import VectorStore

__all__ = ["RagSkill", "RagState"]

_LOGGER = get_logger(__name__)


_RAG_REQUIRES: tuple[str, ...] = (
    "db.vectors:read",
    "db.docs:read",
    "llm.generate",
)


class RagState(BaseModel):
    """RAG run state (design §3.10, FR-32).

    Field names compose the FR-23 declared-output channels for the engine
    ``SubGraphNode`` boundary translator. ``query`` is the input;
    ``retrieved`` carries the fused :class:`~harbor.stores.vector.Hit`
    list from :class:`RetrievalNode`; ``context_window`` is the
    formatted prompt-shaped context; ``answer`` + ``sources`` are the
    final output assembled by :meth:`RagSkill._assemble`.
    """

    query: str = ""
    retrieved: list[Hit] = Field(default_factory=list["Hit"])
    context_window: str = ""
    answer: str = ""
    sources: list[str] = Field(default_factory=list[str])


class RagSkill(Skill):
    """RAG reference skill (FR-32, AC-7.1).

    Composes :class:`RetrievalNode` (vector + doc fan-out) with a POC LLM
    stub and an answer-assembly step. Capability requirements declared on
    the manifest: ``db.vectors:read``, ``db.docs:read``, ``llm.generate``.

    Parent-state writes are restricted to :class:`RagState` field names
    per FR-23 (engine enforces at boundary translation).
    """

    kind: SkillKind = SkillKind.agent
    state_schema: type[BaseModel] = RagState
    requires: list[str] = Field(default_factory=lambda: list(_RAG_REQUIRES))

    async def run(
        self,
        state: RagState,
        ctx: ExecutionContext,
        *,
        stores: list[StoreRef],
        store_resolver: Callable[[str], VectorStore | DocStore],
        k: int = 5,
    ) -> RagState:
        """Drive retrieve → llm-stub → assemble on ``state`` (POC).

        ``store_resolver`` maps each :class:`StoreRef` to the concrete
        provider instance (POC: vector + doc only). ``k`` caps both the
        per-store fan-out and the assembled ``sources`` list.
        """
        retrieve_node = RetrievalNode(
            stores,
            rerank=None,
            k=k,
            store_resolver=store_resolver,
        )
        retrieved_out = await retrieve_node.execute(state, ctx)
        # ``RetrievalNode`` always returns ``list[Hit]`` under "retrieved";
        # the cast keeps pyright happy across the dict[str, Any] boundary.
        hits = cast("list[Hit]", retrieved_out.get("retrieved", []))

        context_window = self._format_context(hits)
        answer = self._call_llm(state.query, hits)
        sources = [h.id for h in hits]

        return state.model_copy(
            update={
                "retrieved": hits,
                "context_window": context_window,
                "answer": answer,
                "sources": sources,
            }
        )

    @staticmethod
    def _format_context(hits: list[Hit]) -> str:
        """Build a flat prompt-shaped context block from retrieved hits.

        POC formatting: one ``[<id>] <metadata?>`` line per hit. Phase 2
        replaces this with a token-budgeted packer driven by the model
        registry's context window.
        """
        if not hits:
            return ""
        lines: list[str] = []
        for h in hits:
            lines.append(f"[{h.id}]")
        return "\n".join(lines)

    def _call_llm(self, query: str, hits: list[Hit]) -> str:
        """Route answer synthesis through the DSPy seam (FR-32, T09).

        Builds ``context`` from retrieved hits, invokes
        ``dspy.Predict(_RagAnswerSignature)`` with force-loud adapter
        installed via :func:`harbor.adapters.dspy._install_filter`, and
        returns the LM-produced ``answer`` field.

        Surfaces :class:`~harbor.errors.AdapterFallbackError` rather than
        silently degrading when DSPy hits its JSONAdapter fallback path.
        """
        _install_filter()
        context = self._format_context(hits)
        predictor = dspy.Predict(_RagAnswerSignature)
        _LOGGER.debug("rag._call_llm", query=query, n_hits=len(hits))
        result = predictor(query=query, context=context)
        return str(result.answer)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


class _RagAnswerSignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """RAG answer synthesis (T09).

    Inputs: ``query`` (the user question), ``context`` (retrieved hit
    text block). Output: ``answer`` (model-produced string).
    """

    query: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
    context: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
    answer: str = dspy.OutputField()  # pyright: ignore[reportUnknownMemberType]
