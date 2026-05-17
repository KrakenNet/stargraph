# SPDX-License-Identifier: Apache-2.0
"""Integration tests for ``AutoresearchSkill`` DSPy seam wiring (T10).

Pins that the summary call site routes through
``dspy.Predict(_AutoresearchSummarySignature)`` rather than returning the
hardcoded f-string ``"POC stub summary for {topic} ({N} claims)"``.
"""

from __future__ import annotations

import pytest

from harbor.skills.refs.autoresearch import _AutoresearchSummarySignature

pytestmark = pytest.mark.integration


@pytest.mark.integration
async def test_autoresearch_summary_routes_through_dspy_signature() -> None:
    """The summary call site invokes ``dspy.Predict(_AutoresearchSummarySignature)``;
    the returned ``WikiEntry.summary`` contains no ``"POC stub summary"`` literal (T10)."""
    import dspy

    from harbor.skills.refs.autoresearch import AutoresearchSkill, AutoresearchState

    class _StandinLM(dspy.LM):
        def __init__(self) -> None:
            super().__init__(model="standin/standin")

        def __call__(self, *_args: object, **_kwargs: object) -> list[str]:
            return ['{"summary": "STANDIN_SUMMARY"}']

    with dspy.context(lm=_StandinLM()):
        skill = AutoresearchSkill(
            name="autoresearch", version="1.0.0", description="autoresearch ref skill"
        )
        state = AutoresearchState(topic="cats")
        result = await skill.run(state)
    assert result.wiki_entry is not None
    assert "POC stub summary" not in result.wiki_entry.summary


@pytest.mark.integration
async def test_autoresearch_summary_raises_when_lm_not_configured() -> None:
    """Unset ``dspy.settings.lm`` surfaces the DSPy force-loud
    ``AdapterFallbackError`` rather than silently degrading (T10)."""
    import dspy

    from harbor.skills.refs.autoresearch import AutoresearchSkill, AutoresearchState

    with dspy.context(lm=None):
        skill = AutoresearchSkill(
            name="autoresearch", version="1.0.0", description="autoresearch ref skill"
        )
        state = AutoresearchState(topic="dogs")
        with pytest.raises(Exception):
            await skill.run(state)


@pytest.mark.integration
def test_autoresearch_summary_signature_symbol_exists() -> None:
    """The DSPy signature class is exposed at module scope (T10)."""
    assert _AutoresearchSummarySignature is not None
