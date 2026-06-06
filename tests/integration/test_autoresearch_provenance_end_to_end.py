# SPDX-License-Identifier: Apache-2.0
"""End-to-end provenance check for ``AutoresearchSkill`` (AC-7.2 invariant).

Task 3.42 / FR-33 / AC-7.2 / NFR-4. Stronger sibling of
``test_autoresearch_reference_skill`` -- exercises both the
happy-path (every claim's ``source_id`` resolves to a
:class:`SourceRecord` whose ``uri`` is recoverable) and the
loud-fail path (orphan provenance raises ``ValueError`` per
design §3.11 invariant). NFR-4: hidden fallbacks unacceptable.
"""

from __future__ import annotations

import pytest

from stargraph.skills.refs.autoresearch import (
    AutoresearchSkill,
    AutoresearchState,
    Claim,
    SourceRecord,
)

pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


async def test_every_claim_resolves_to_source() -> None:
    """AC-7.2: every emitted claim resolves into the sources dict."""
    skill = AutoresearchSkill(
        name="autoresearch",
        version="0.1.0",
        description="E2E provenance test",
    )

    out = await skill.run(AutoresearchState(topic="provenance"))

    assert out.claims, "no claims emitted"
    assert out.sources, "no sources recorded"

    # Every claim points to a source whose URI is non-empty (web kind).
    for claim in out.claims:
        rec = out.sources.get(claim.source_id)
        assert rec is not None, f"orphan source_id {claim.source_id!r}"
        assert rec.uri, f"source {rec.id!r} has no uri"
        # Stub web sources echo the source_id as URI.
        assert rec.uri == claim.source_id
        # Source record's text matches claim text (web-stub round-trip).
        assert rec.text == claim.text

    # No orphans: every source is referenced by at least one claim.
    referenced = {c.source_id for c in out.claims}
    assert referenced == set(out.sources.keys())


async def test_orphan_provenance_loud_fails() -> None:
    """AC-7.2 / NFR-4: an orphan claim.source_id raises ValueError loudly."""
    skill = AutoresearchSkill(
        name="autoresearch",
        version="0.1.0",
        description="E2E orphan-provenance loud-fail test",
    )

    # Pre-seed a claim whose source_id will NOT be added by the web-stub.
    # The stub only emits ``web:{topic}:{idx}`` -- this id never overlaps.
    seed = AutoresearchState(
        topic="orphan-topic",
        claims=[Claim(id="planted", text="orphan", source_id="missing-source")],
        sources={
            # Only seed an unrelated source so the orphan check actually fires.
            "unrelated": SourceRecord(id="unrelated", kind="web", uri="x", text=""),
        },
    )

    with pytest.raises(ValueError, match=r"Orphan provenance"):
        await skill.run(seed)
