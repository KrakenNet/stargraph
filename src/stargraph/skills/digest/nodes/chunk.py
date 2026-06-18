# SPDX-License-Identifier: Apache-2.0
"""Chunk — split long text into ``chunk_size``-bounded pieces.

The first node of the ``digest`` workflow. Pure and deterministic: no LLM seam.
Splits on whitespace boundaries where possible so a chunk never cuts mid-word
unless a single word is itself longer than ``chunk_size``. Validation failures
loud-fail with :class:`ValueError`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from pydantic import BaseModel


class Chunk(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # no per-run context needed for a pure transform
        text = str(getattr(state, "text", "") or "")
        chunk_size = int(getattr(state, "chunk_size", 0) or 0)
        if not text.strip():
            raise ValueError("text is required: nothing to chunk")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be a positive integer")

        return {"chunks": _split(text, chunk_size)}


def _split(text: str, chunk_size: int) -> list[str]:
    """Greedily pack whitespace-delimited words into ``chunk_size`` chunks.

    A word longer than ``chunk_size`` is hard-split across chunks so the size
    bound always holds. Every word survives, in order, so the chunks reassemble
    to cover all of the input's words.
    """
    chunks: list[str] = []
    current = ""
    for word in text.split():
        while len(word) > chunk_size:
            # Word alone exceeds the bound: flush, then emit hard slices.
            if current:
                chunks.append(current)
                current = ""
            chunks.append(word[:chunk_size])
            word = word[chunk_size:]
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= chunk_size:
            current = f"{current} {word}"
        else:
            chunks.append(current)
            current = word
    if current:
        chunks.append(current)
    return chunks
