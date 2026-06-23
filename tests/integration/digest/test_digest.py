# SPDX-License-Identifier: Apache-2.0
"""Digest skill — the deterministic value-add, with the LLM seam stubbed.

Journey: long text is chunked into ``chunk_size``-bounded pieces, each chunk is
summarized into a partial (map), and the partials are folded into one final
summary (reduce). The summarizer is injected, so no live LM is involved.
"""

from __future__ import annotations

import pytest

from stargraph.skills.digest import DIGEST, DigestState
from stargraph.skills.digest.nodes.chunk import Chunk
from stargraph.skills.digest.nodes.map import MapSummarize
from stargraph.skills.digest.nodes.reduce import Reduce

pytestmark = pytest.mark.integration


class _Ctx:
    run_id = "digest-test"


def _stub_summarizer(text: str) -> str:
    return f"S[{len(text)}]"


async def test_chunking_bounds_size_and_covers_all_words() -> None:
    words = [f"word{i}" for i in range(200)]
    text = " ".join(words)
    chunk_size = 40
    out = await Chunk().execute(DigestState(text=text, chunk_size=chunk_size), _Ctx())
    chunks = out["chunks"]
    assert len(chunks) > 1  # a long string yields multiple chunks
    assert all(len(c) <= chunk_size for c in chunks)
    reassembled = " ".join(chunks).split()
    assert reassembled == words  # every word survives, in order


async def test_chunking_hard_splits_overlong_word() -> None:
    long_word = "x" * 25
    out = await Chunk().execute(DigestState(text=long_word, chunk_size=10), _Ctx())
    chunks = out["chunks"]
    assert all(len(c) <= 10 for c in chunks)
    assert "".join(chunks) == long_word


async def test_map_yields_one_partial_per_chunk() -> None:
    node = MapSummarize(summarizer=_stub_summarizer)
    state = DigestState(chunks=["alpha", "beta gamma", "delta"])
    out = await node.execute(state, _Ctx())
    partials = out["partials"]
    assert len(partials) == len(state.chunks)
    assert partials == ["S[5]", "S[10]", "S[5]"]  # order preserved


async def test_reduce_over_multiple_partials_summarizes_concatenation() -> None:
    node = Reduce(summarizer=_stub_summarizer)
    partials = ["first", "second", "third"]
    out = await node.execute(DigestState(partials=partials), _Ctx())
    expected_len = len("\n\n".join(partials))
    assert out["summary"] == f"S[{expected_len}]"
    assert out["summary"]  # non-empty, derived from the partials


async def test_reduce_single_partial_is_passthrough() -> None:
    node = Reduce(summarizer=_stub_summarizer)
    out = await node.execute(DigestState(partials=["only one"]), _Ctx())
    assert out["summary"] == "only one"  # the one partial, untouched


async def test_blank_text_raises() -> None:
    with pytest.raises(ValueError, match="text is required"):
        await Chunk().execute(DigestState(text="   ", chunk_size=10), _Ctx())


async def test_nonpositive_chunk_size_raises() -> None:
    with pytest.raises(ValueError, match="chunk_size must be a positive integer"):
        await Chunk().execute(DigestState(text="hello world", chunk_size=0), _Ctx())


def test_skill_declares_only_state_channels() -> None:
    assert DIGEST.kind.value == "workflow"
    assert DIGEST.site_id == "digest@0.1.0"
    assert DIGEST.declared_output_keys == frozenset(
        {"text", "chunk_size", "chunks", "partials", "summary"}
    )
