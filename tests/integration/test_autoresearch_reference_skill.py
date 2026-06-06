# SPDX-License-Identifier: Apache-2.0
"""End-to-end test for ``AutoresearchSkill`` -- topic → claims → wiki entry.

Task 3.42 / FR-33 / AC-7.2 / NFR-4. Drives the POC web-stub branch
(no network) and asserts the generated :class:`WikiEntry` carries
claim ids that match the accumulated :class:`Claim` records, and
that every claim's ``source_id`` resolves into the run's
``sources`` provenance dict.
"""

from __future__ import annotations

import pytest

from harbor.skills.refs.autoresearch import (
    AutoresearchSkill,
    AutoresearchState,
)

pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


async def test_autoresearch_topic_to_wiki_entry() -> None:
    """topic → gather (web-stub) → assemble produces a populated WikiEntry."""
    skill = AutoresearchSkill(
        name="autoresearch",
        version="0.1.0",
        description="E2E autoresearch reference test",
    )

    state = AutoresearchState(topic="LangGraph")
    out = await skill.run(state)

    # FR-33: wiki_entry populated with topic + claim ids.
    assert out.wiki_entry is not None
    assert out.wiki_entry.topic == "LangGraph"
    assert out.wiki_entry.claim_ids, "wiki entry has no claim ids"
    assert len(out.wiki_entry.claim_ids) == len(out.claims)

    # Claim ids in entry exactly match accumulated claims (order preserved).
    assert out.wiki_entry.claim_ids == [c.id for c in out.claims]

    # AC-7.2: every claim resolves into sources dict.
    assert out.sources, "no sources recorded"
    for claim in out.claims:
        assert claim.source_id in out.sources
        rec = out.sources[claim.source_id]
        assert rec.kind == "web"
        assert rec.uri == claim.source_id

    # Stub web fetch contributes ≥ 1 claim per topic.
    assert len(out.claims) >= 1
    assert out.wiki_entry.summary == "STANDIN_SUMMARY"  # canned StandinLM payload (T10 dspy seam)
