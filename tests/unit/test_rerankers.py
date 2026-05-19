# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``CrossEncoderReranker`` -- T05 named-test pins.

Pins the contract that :class:`CrossEncoderReranker.fuse` raises
:class:`HarborRuntimeError` when ``query`` is unset (force-loud per FR-6;
the cross-encoder requires ``(query, doc)`` pairs to score). Full
end-to-end coverage lives in ``test_cross_encoder_reranker.py``; this
file holds the PRD-named pin only.
"""

from __future__ import annotations

import pytest

from harbor.errors import HarborRuntimeError
from harbor.stores.rerankers import CrossEncoderReranker
from harbor.stores.vector import Hit

pytestmark = [pytest.mark.unit, pytest.mark.knowledge]


@pytest.mark.unit
async def test_cross_encoder_fuse_raises_when_query_missing() -> None:
    """``fuse(query=None)`` is a wiring bug -- force-loud per FR-6 (T05)."""
    rr = CrossEncoderReranker()
    hits = [[Hit(id="a", score=1.0, metadata={"text": "doc"})]]
    with pytest.raises(HarborRuntimeError):
        await rr.fuse(hits, k=1, query=None)
