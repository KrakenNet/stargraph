# SPDX-License-Identifier: Apache-2.0
"""End-to-end composition test: ``WikiSkill`` consumes ``AutoresearchSkill``.

Task 3.42 / FR-34 / AC-7.3 / NFR-4. Confirms that provenance is
preserved through skill composition: the same claim ids and source
URIs that ``AutoresearchSkill`` emits for a topic appear in the
``WikiSkill`` markdown output (every claim's ``source_id`` is
referenced as a numbered citation, and every cited source is listed
in the trailing ``## Sources`` block).
"""

from __future__ import annotations

import pytest

from harbor.skills.refs.autoresearch import AutoresearchSkill, AutoresearchState
from harbor.skills.refs.wiki import WikiSkill, WikiState

pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


async def test_wiki_consumes_autoresearch_provenance_preserved() -> None:
    """Chained run: WikiSkill drives AutoresearchSkill; provenance round-trips."""
    topic = "compositional-provenance"

    # First, run AutoresearchSkill standalone to capture the expected
    # provenance baseline (claim ids + source URIs).
    ar_skill = AutoresearchSkill(
        name="autoresearch",
        version="0.1.0",
        description="E2E composition baseline",
    )
    ar_out = await ar_skill.run(AutoresearchState(topic=topic))
    assert ar_out.wiki_entry is not None
    expected_claim_ids = [c.id for c in ar_out.claims]
    expected_source_uris = {s.uri for s in ar_out.sources.values()}

    # Now drive WikiSkill, which composes AutoresearchSkill internally.
    wiki_skill = WikiSkill(
        name="wiki",
        version="0.1.0",
        description="E2E composition test",
    )
    wiki_out = await wiki_skill.run(WikiState(topic=topic))

    # Composed run reproduces the same wiki entry topic and claim id list.
    assert wiki_out.wiki_entry is not None
    assert wiki_out.wiki_entry.topic == topic
    assert wiki_out.wiki_entry.claim_ids == expected_claim_ids

    # AC-7.3 invariant: every source URI from autoresearch survives in
    # the markdown -- composition cannot drop provenance.
    md = wiki_out.markdown
    assert md, "WikiSkill produced no markdown"
    for uri in expected_source_uris:
        assert uri in md, f"source uri {uri!r} not preserved through composition"

    # Every claim text appears in the markdown body (citation block).
    for claim in ar_out.claims:
        assert claim.text in md, f"claim {claim.id!r} text missing from markdown"
