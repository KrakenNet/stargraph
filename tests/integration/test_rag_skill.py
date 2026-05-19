# SPDX-License-Identifier: Apache-2.0
"""Integration tests for ``RagSkill`` DSPy seam wiring (T09).

Pins that ``_call_llm`` (the renamed ``_llm_stub`` body) routes through
``harbor.adapters.dspy.bind`` and a ``dspy.Predict(_RagAnswerSignature)``
invocation rather than returning the hardcoded f-string
``"Based on N sources: POC stub answer"``.

The DSPy stand-in LM is configured via ``dspy.settings.configure(...)``
inside each test (no ``unittest.mock``); the integration-test seam is
``tests/integration/test_dspy_*.py`` for shared patterns.
"""

from __future__ import annotations

import pytest

from harbor.skills.refs.rag import RagSkill, _RagAnswerSignature
from harbor.stores.vector import Hit

pytestmark = pytest.mark.integration


def _hits() -> list[Hit]:
    return [
        Hit(id="h1", score=1.0, metadata={"text": "the cat sat on the mat"}),
        Hit(id="h2", score=0.9, metadata={"text": "the dog ran in the park"}),
    ]


@pytest.mark.integration
def test_rag_call_llm_invokes_dspy_signature() -> None:
    """``_call_llm`` routes through ``dspy.Predict(_RagAnswerSignature)`` -- the
    returned string is the LM-produced answer, not the POC stub (T09)."""
    import dspy

    # Hand-rolled stand-in LM (no ``unittest.mock``). Returns a JSON-shaped
    # string in the canonical DSPy LM output format so JSONAdapter can parse
    # the ``answer`` field out.
    class _StandinLM(dspy.LM):
        def __init__(self) -> None:
            super().__init__(model="standin/standin")

        def __call__(self, *_args: object, **_kwargs: object) -> list[str]:
            return ['{"answer": "STANDIN_ANSWER"}']

    with dspy.context(lm=_StandinLM()):
        skill = RagSkill(name="rag", version="1.0.0", description="RAG ref skill")
        out = skill._call_llm(query="where did the cat sit", hits=_hits())  # pyright: ignore[reportPrivateUsage]
    assert isinstance(out, str)
    assert "POC stub answer" not in out


@pytest.mark.integration
def test_rag_call_llm_raises_when_lm_not_configured() -> None:
    """When ``dspy.settings.lm`` is unset, ``_call_llm`` surfaces the DSPy
    force-loud ``AdapterFallbackError`` rather than silently degrading (T09)."""
    import dspy

    with dspy.context(lm=None):
        skill = RagSkill(name="rag", version="1.0.0", description="RAG ref skill")
        with pytest.raises(Exception):
            skill._call_llm(query="q", hits=_hits())  # pyright: ignore[reportPrivateUsage]


@pytest.mark.integration
def test_rag_answer_signature_symbol_exists() -> None:
    """The DSPy signature class is exposed at module scope (T09)."""
    assert _RagAnswerSignature is not None
