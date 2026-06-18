# SPDX-License-Identifier: Apache-2.0
"""MapSummarize — summarize each chunk into a partial summary.

The map half of the ``digest`` workflow. The summarization call sits behind the
injectable ``summarizer`` seam (the nodesmith ``Build._program`` pattern), so
the node's value-add — fanning the summarizer across every chunk, in order — is
exercised in tests with no live model. ``_default_summarizer`` is the
production DSPy path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    # text -> summary of that text.
    Summarizer = Callable[[str], str]


class MapSummarize(NodeBase):
    def __init__(self, summarizer: Summarizer | None = None) -> None:
        self._summarizer = summarizer or _default_summarizer

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # no per-run context needed for a pure transform
        chunks = list(getattr(state, "chunks", []) or [])
        partials = [self._summarizer(chunk) for chunk in chunks]
        return {"partials": partials}


def _default_summarizer(text: str) -> str:
    """Production summarizer — one DSPy call returning a summary of ``text``.

    Imported lazily so the skill (and its tests, which inject a stub) never pull
    in DSPy unless a real summarization runs.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    predictor = dspy.Predict("text -> summary")  # pyright: ignore[reportUnknownMemberType]
    result = predictor(text=text)  # pyright: ignore[reportUnknownVariableType]
    return str(getattr(result, "summary", ""))
