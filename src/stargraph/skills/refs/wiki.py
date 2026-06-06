# SPDX-License-Identifier: Apache-2.0
"""WikiSkill POC reference -- topic → markdown wiki workflow (FR-34, AC-7.3).

Phase-1 POC scaffold for the in-tree ``wiki`` reference skill (design
§3.12). Composes :class:`~stargraph.skills.refs.autoresearch.AutoresearchSkill`
(kind=agent) with a deterministic markdown formatter to produce a
publishable wiki entry from a topic string.

POC scope:

* No LLM call -- :meth:`WikiSkill._format_markdown` is a fixed template
  that serializes :class:`WikiEntry` claims with bracketed citation
  markers (``[1]``, ``[2]``...) and a trailing ``## Sources`` block.
  Phase 2 promotes this to a real ``llm.generate`` invocation so the
  declared capability has a call site.
* Provenance preservation is the AC-7.3 invariant: every
  :attr:`Claim.source_id` from the upstream :class:`AutoresearchSkill`
  output MUST appear in the rendered markdown (as a citation marker
  whose number indexes into the ``## Sources`` block). The formatter
  loud-fails if any claim has no resolvable source.
* Capabilities declared on the manifest: ``db.docs:write`` (the wiki
  entry is intended to land in a doc store) and ``llm.generate`` (Phase
  2 LLM-driven prose). The capabilities gate (FR-7) enforces these at
  run admission.
* Subgraph IR is tracked separately under
  ``tests/fixtures/skills/wiki/example.yaml``; Phase 2 loads it via
  :attr:`Skill.subgraph` and routes execution through
  :class:`~stargraph.nodes.SubGraphNode`.

The :meth:`WikiSkill.run` coroutine drives autoresearch → format on a
:class:`WikiState` and returns the updated state with ``wiki_entry`` and
``markdown`` populated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from stargraph.skills.base import Skill, SkillKind
from stargraph.skills.refs.autoresearch import (
    AutoresearchSkill,
    AutoresearchState,
    WikiEntry,
)

if TYPE_CHECKING:
    from stargraph.nodes.base import ExecutionContext

__all__ = [
    "WikiSkill",
    "WikiState",
]


_WIKI_REQUIRES: tuple[str, ...] = (
    "db.docs:write",
    "llm.generate",
)


class WikiState(BaseModel):
    """Wiki run state (design §3.12, FR-34).

    Field names compose the FR-23 declared-output channels for the
    engine ``SubGraphNode`` boundary translator. ``topic`` is the
    input; ``wiki_entry`` is the upstream :class:`AutoresearchSkill`
    output; ``markdown`` is the final formatted artifact with
    provenance preserved as inline citation markers.
    """

    topic: str = ""
    wiki_entry: WikiEntry | None = None
    markdown: str = ""


class WikiSkill(Skill):
    """Wiki reference skill (FR-34, AC-7.3).

    POC workflow: drive :class:`AutoresearchSkill` to assemble a
    :class:`WikiEntry` for ``state.topic``, then serialize via
    :meth:`_format_markdown` -- a fixed-template formatter that
    threads each :attr:`Claim.source_id` into a numbered citation
    marker (``[1]``, ``[2]``...) backed by a ``## Sources`` block.

    Parent-state writes are restricted to :class:`WikiState` field
    names per FR-23 (engine enforces at boundary translation).
    """

    kind: SkillKind = SkillKind.workflow
    state_schema: type[BaseModel] = WikiState
    requires: list[str] = Field(default_factory=lambda: list(_WIKI_REQUIRES))

    async def run(
        self,
        state: WikiState,
        ctx: ExecutionContext | None = None,
    ) -> WikiState:
        """Drive autoresearch → format on ``state`` (POC).

        Composes :class:`AutoresearchSkill` (kind=agent) with the
        deterministic markdown formatter. The smoke path runs without
        any store_resolver -- the upstream skill stays on its
        web-stub branch.
        """
        autoresearch = AutoresearchSkill(
            name="autoresearch",
            version="0.1.0",
            description="POC autoresearch (composed by WikiSkill)",
            state_schema=AutoresearchState,
        )
        ar_state = AutoresearchState(topic=state.topic)
        ar_out = await autoresearch.run(ar_state, ctx)

        if ar_out.wiki_entry is None:
            msg = (
                "AutoresearchSkill returned no wiki_entry for topic "
                f"{state.topic!r}; cannot format markdown"
            )
            raise ValueError(msg)

        markdown = self._format_markdown(ar_out)

        return state.model_copy(
            update={
                "wiki_entry": ar_out.wiki_entry,
                "markdown": markdown,
            }
        )

    @staticmethod
    def _format_markdown(ar_state: AutoresearchState) -> str:
        """POC markdown formatter -- fixed template, no LLM call.

        Renders the upstream :class:`WikiEntry` as a markdown document
        with numbered inline citations (``[N]``) keyed off
        :attr:`Claim.source_id` and a trailing ``## Sources`` block
        listing each source URI. Loud-fails if any claim's source_id
        is not present in ``ar_state.sources`` (AC-7.3 invariant --
        provenance must round-trip through composition).

        Phase 2 swaps this for a real ``llm.generate`` call against the
        engine model registry (design §3.12).
        """
        entry = ar_state.wiki_entry
        if entry is None:  # pragma: no cover -- caller guards
            msg = "WikiEntry is None; nothing to format"
            raise ValueError(msg)

        # Build source_id → citation index ([1], [2], ...) in the order
        # claims first reference them. Stable numbering keeps the body
        # text deterministic across runs.
        citation_index: dict[str, int] = {}
        for claim in ar_state.claims:
            if claim.source_id not in ar_state.sources:
                msg = (
                    f"Provenance break: claim {claim.id!r} references "
                    f"source_id {claim.source_id!r} not in sources dict"
                )
                raise ValueError(msg)
            if claim.source_id not in citation_index:
                citation_index[claim.source_id] = len(citation_index) + 1

        lines: list[str] = [f"# {entry.topic}", "", entry.summary, ""]
        lines.append("## Claims")
        lines.append("")
        for claim in ar_state.claims:
            marker = citation_index[claim.source_id]
            lines.append(f"- {claim.text} [{marker}]")
        lines.append("")
        lines.append("## Sources")
        lines.append("")
        for source_id, marker in citation_index.items():
            source = ar_state.sources[source_id]
            lines.append(f"{marker}. `{source.id}` ({source.kind}) — {source.uri}")

        return "\n".join(lines)
