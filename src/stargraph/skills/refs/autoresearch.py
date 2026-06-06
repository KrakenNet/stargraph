# SPDX-License-Identifier: Apache-2.0
"""AutoresearchSkill POC reference -- topic → wiki-entry agent (FR-33, AC-7.2).

Phase-1 POC scaffold for the in-tree ``autoresearch`` reference skill
(design §3.11). Uses the :class:`~stargraph.skills.react.ReactSkill`
think → act → observe pattern internally, with the ``act`` step
alternating between a stubbed web fetch and a :class:`RagSkill`
vector-retrieval call. Each emitted :class:`Claim` carries a
``source_id`` that MUST resolve into the run's ``sources`` dict --
provenance lineage is the AC-7.2 invariant.

POC scope:

* ``_stub_web_fetch`` returns a fixed list of ``(text, source_id)``
  pairs so the smoke path runs without network IO. Phase 2 promotes
  this to a real :mod:`stargraph.tools.web` invocation gated by the
  ``web.read`` capability.
* Vector retrieval is delegated to :class:`RagSkill` only when a
  ``store_resolver`` is provided -- the POC smoke path runs without
  one and exercises the web-stub branch alone.
* Capabilities declared on the manifest: ``web.read``,
  ``db.vectors:read``, ``db.docs:read``, ``llm.generate``. The
  capabilities gate (FR-7) enforces these at run admission.
* Subgraph IR is tracked separately under
  ``tests/fixtures/skills/autoresearch/example.yaml``; Phase 2 loads
  it via :attr:`Skill.subgraph` and routes execution through
  :class:`~stargraph.nodes.SubGraphNode`.

The :meth:`AutoresearchSkill.run` coroutine drives gather → assemble
on an :class:`AutoresearchState` and returns the updated state with
``claims``, ``sources``, and ``wiki_entry`` populated. Every
``claim.source_id`` is verified to resolve into ``sources`` before
return -- orphaned provenance loud-fails per design §3.11.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import dspy  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from stargraph.adapters.dspy import _install_filter  # pyright: ignore[reportPrivateUsage]
from stargraph.logging import get_logger
from stargraph.skills.base import Skill, SkillKind

if TYPE_CHECKING:
    from collections.abc import Callable

    from stargraph.ir._models import StoreRef
    from stargraph.nodes.base import ExecutionContext
    from stargraph.stores.doc import DocStore
    from stargraph.stores.vector import VectorStore

__all__ = [
    "AutoresearchSkill",
    "AutoresearchState",
    "Claim",
    "SourceRecord",
    "WikiEntry",
]

_LOGGER = get_logger(__name__)


_AUTORESEARCH_REQUIRES: tuple[str, ...] = (
    "web.read",
    "db.vectors:read",
    "db.docs:read",
    "llm.generate",
)


class Claim(BaseModel):
    """Single research claim with mandatory provenance (design §3.11).

    ``source_id`` is REQUIRED -- the AC-7.2 invariant is that every
    claim resolves back to a :class:`SourceRecord` in the run
    ``sources`` dict. Orphaned provenance loud-fails at assembly time.
    """

    id: str
    text: str
    source_id: str


class SourceRecord(BaseModel):
    """Source provenance record for a :class:`Claim` (design §3.11).

    ``id`` matches :attr:`Claim.source_id`; ``kind`` discriminates the
    fetch path (``web`` vs ``vector``). ``uri`` is the original locator
    (URL for web, store-qualified hit id for vector).
    """

    id: str
    kind: str
    uri: str
    text: str = ""


class WikiEntry(BaseModel):
    """Assembled topic wiki entry (design §3.11, FR-33)."""

    topic: str
    summary: str = ""
    claim_ids: list[str] = Field(default_factory=list[str])


class AutoresearchState(BaseModel):
    """Autoresearch run state (design §3.11, FR-33).

    Field names compose the FR-23 declared-output channels for the
    engine ``SubGraphNode`` boundary translator. ``topic`` is the
    input; ``claims`` accumulates evidence with mandatory
    ``source_id``; ``sources`` is the provenance dict keyed by
    :attr:`SourceRecord.id`; ``wiki_entry`` is the final assembled
    output.
    """

    topic: str = ""
    claims: list[Claim] = Field(default_factory=list[Claim])
    sources: dict[str, SourceRecord] = Field(default_factory=dict[str, SourceRecord])
    wiki_entry: WikiEntry | None = None


class AutoresearchSkill(Skill):
    """Autoresearch reference skill (FR-33, AC-7.2).

    POC subgraph: gather (stub web fetch + optional RagSkill vector
    retrieval) → assemble (build :class:`WikiEntry`, verify
    provenance lineage). Internally follows the
    :class:`~stargraph.skills.react.ReactSkill` think → act → observe
    shape; Phase 2 moves the loop under
    :class:`~stargraph.nodes.SubGraphNode` driven by the LangGraph IR
    referenced via :attr:`Skill.subgraph`.

    Parent-state writes are restricted to :class:`AutoresearchState`
    field names per FR-23 (engine enforces at boundary translation).
    """

    kind: SkillKind = SkillKind.agent
    state_schema: type[BaseModel] = AutoresearchState
    requires: list[str] = Field(default_factory=lambda: list(_AUTORESEARCH_REQUIRES))
    max_steps: int = 4

    @staticmethod
    def _stub_web_fetch(topic: str) -> list[tuple[str, str]]:
        """POC web-fetch stub -- no real network IO.

        Returns a fixed list of ``(text, source_id)`` pairs keyed off
        ``topic`` so smoke tests assert deterministic provenance.
        Phase 2 routes this through :mod:`stargraph.tools.web` gated by
        the ``web.read`` capability.
        """
        return [
            (f"{topic} is a documented subject.", f"web:{topic}:0"),
            (f"{topic} has multiple cited references.", f"web:{topic}:1"),
        ]

    async def run(
        self,
        state: AutoresearchState,
        ctx: ExecutionContext | None = None,
        *,
        stores: list[StoreRef] | None = None,
        store_resolver: Callable[[str], VectorStore | DocStore] | None = None,
        k: int = 3,
    ) -> AutoresearchState:
        """Drive gather → assemble on ``state`` (POC).

        ``stores`` + ``store_resolver`` opt the run into the RagSkill
        vector branch; omitting them keeps the smoke path on the
        web-stub alone. ``k`` caps the per-store fan-out passed to
        :class:`RagSkill`.
        """
        del ctx, stores, store_resolver, k  # POC: vector branch unused in smoke
        sources: dict[str, SourceRecord] = dict(state.sources)
        claims: list[Claim] = list(state.claims)

        # Gather: stub web fetch (Phase-2 swaps in real tool dispatch).
        for idx, (text, source_id) in enumerate(self._stub_web_fetch(state.topic)):
            sources[source_id] = SourceRecord(
                id=source_id,
                kind="web",
                uri=source_id,
                text=text,
            )
            claims.append(
                Claim(
                    id=f"claim:{state.topic}:{idx}",
                    text=text,
                    source_id=source_id,
                )
            )

        # Assemble: verify provenance lineage (AC-7.2 invariant).
        for claim in claims:
            if claim.source_id not in sources:
                msg = (
                    f"Orphan provenance: claim {claim.id!r} references "
                    f"source_id {claim.source_id!r} not in sources dict"
                )
                raise ValueError(msg)

        summary = self._call_summary(state.topic, [c.text for c in claims])
        wiki_entry = WikiEntry(
            topic=state.topic,
            summary=summary,
            claim_ids=[c.id for c in claims],
        )

        return state.model_copy(
            update={
                "claims": claims,
                "sources": sources,
                "wiki_entry": wiki_entry,
            }
        )

    def _call_summary(self, topic: str, claims: list[str]) -> str:
        """Route the wiki-entry summary through the DSPy seam (FR-33, T10).

        Invokes ``dspy.Predict(_AutoresearchSummarySignature)`` with
        force-loud adapter installed. Returns the LM-produced ``summary``
        string. Raises :class:`~stargraph.errors.AdapterFallbackError` when
        DSPy hits the silent JSONAdapter fallback path.
        """
        _install_filter()
        predictor = dspy.Predict(_AutoresearchSummarySignature)
        _LOGGER.debug("autoresearch._call_summary", topic=topic, n_claims=len(claims))
        result = predictor(topic=topic, claims=claims)
        return str(result.summary)  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]


class _AutoresearchSummarySignature(dspy.Signature):  # pyright: ignore[reportUnknownMemberType]
    """Autoresearch wiki-entry summary (T10).

    Inputs: ``topic`` (the research subject), ``claims`` (the list of
    extracted claim texts). Output: ``summary`` (the synthesized wiki
    entry summary).
    """

    topic: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
    claims: list[str] = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
    summary: str = dspy.OutputField()  # pyright: ignore[reportUnknownMemberType]
