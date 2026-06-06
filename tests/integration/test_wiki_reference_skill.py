# SPDX-License-Identifier: Apache-2.0
"""End-to-end test for ``WikiSkill`` markdown output format.

Task 3.42 / FR-34 / AC-7.3 / NFR-4. Drives the reference
:class:`stargraph.skills.refs.wiki.WikiSkill` and asserts the produced
markdown has the documented structure (``# topic`` heading,
``## Claims`` block with bracketed citations, and a trailing
``## Sources`` block keyed by numeric markers).
"""

from __future__ import annotations

import re

import pytest

from stargraph.skills.refs.wiki import WikiSkill, WikiState

pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.usefixtures("standin_lm"),
]


async def test_wiki_skill_produces_markdown() -> None:
    """FR-34 / AC-7.3: WikiSkill emits markdown with citations + sources."""
    skill = WikiSkill(
        name="wiki",
        version="0.1.0",
        description="E2E wiki reference test",
    )

    out = await skill.run(WikiState(topic="ReplaySafety"))

    assert out.wiki_entry is not None
    assert out.markdown, "no markdown produced"

    md = out.markdown

    # Heading is the topic.
    assert md.startswith("# ReplaySafety")

    # ## Claims block carries every claim_id text with a numeric citation.
    assert "## Claims" in md
    assert "## Sources" in md
    # ## Claims appears before ## Sources.
    assert md.index("## Claims") < md.index("## Sources")

    # Each claim line ends in `[N]` citation marker.
    claim_lines = [
        ln for ln in md.splitlines() if ln.startswith("- ") and ln.rstrip().endswith("]")
    ]
    assert claim_lines, "no claim bullet lines with citation markers"
    for line in claim_lines:
        assert re.search(r"\[\d+\]$", line.rstrip()), line

    # Sources block lists numbered entries; numbers used in claims must
    # all appear as ``N. `` source-block prefixes.
    used_markers: set[str] = set(re.findall(r"\[(\d+)\]", md))
    assert used_markers, "no citation markers in markdown"
    for marker in used_markers:
        assert re.search(rf"^{re.escape(marker)}\. ", md, flags=re.MULTILINE), marker
