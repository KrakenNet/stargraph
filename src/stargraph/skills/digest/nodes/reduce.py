# SPDX-License-Identifier: Apache-2.0
"""Reduce — fold the per-chunk partials into a single final summary.

The reduce half of the ``digest`` workflow. Shares the injectable ``summarizer``
seam with :class:`~stargraph.skills.digest.nodes.map.MapSummarize`. A single
partial is already the whole summary; multiple partials are concatenated and
summarized once more. ``_default_summarizer`` is the production DSPy path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    # text -> summary of that text.
    Summarizer = Callable[[str], str]


class Reduce(NodeBase):
    def __init__(self, summarizer: Summarizer | None = None) -> None:
        self._summarizer = summarizer or _default_summarizer

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # no per-run context needed for a pure transform
        partials = list(getattr(state, "partials", []) or [])
        if len(partials) == 1:
            return {"summary": partials[0]}
        summary = self._summarizer("\n\n".join(partials))
        return {"summary": summary}


def _default_summarizer(text: str) -> str:
    """Production summarizer — one DSPy call returning a summary of ``text``.

    Imported lazily so the skill (and its tests, which inject a stub) never pull
    in DSPy unless a real summarization runs.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    predictor = dspy.Predict("text -> summary")  # pyright: ignore[reportUnknownMemberType]
    result = predictor(text=text)  # pyright: ignore[reportUnknownVariableType]
    return str(getattr(result, "summary", ""))
