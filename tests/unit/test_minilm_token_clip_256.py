# SPDX-License-Identifier: Apache-2.0
"""TDD-GREEN: MiniLM input token clip at 256 (FR-15, AC-11).

Asserts :meth:`stargraph.stores.embeddings.MiniLMEmbedder.embed` truncates
inputs longer than :data:`stargraph.stores.embeddings.MINILM_MAX_TOKENS`
(256) before calling ``SentenceTransformer.encode``, and emits a
``minilm.input_clipped`` warning so operators can spot the truncation
in structured logs.

Two tests:

1. :func:`test_short_input_passes_through` -- input under 256 tokens is
   forwarded verbatim, no warning, no clip.
2. :func:`test_long_input_clipped_with_warning` -- input over 256 tokens
   is truncated to 256 token-ids before decoding, and a structlog
   warning is captured.

The :class:`MiniLMEmbedder` is built bypassing ``__init__`` (we do not
need real weights for the clip path); a stub ``_model`` carrying a
fake tokenizer + ``encode`` is attached directly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

from stargraph.stores.embeddings import MINILM_MAX_TOKENS, MINILM_NDIMS, MiniLMEmbedder

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


class _FakeTokenizer:
    """Minimal ``transformers``-style tokenizer for clip-path tests."""

    def __init__(self) -> None:
        self.encode_calls: list[tuple[str, bool]] = []
        self.decode_calls: list[list[int]] = []

    def encode(self, text: str, add_special_tokens: bool = True) -> list[int]:
        self.encode_calls.append((text, add_special_tokens))
        # One token per character is plenty for the size tests.
        return list(range(len(text)))

    def decode(self, ids: list[int], skip_special_tokens: bool = False) -> str:
        _ = skip_special_tokens
        self.decode_calls.append(list(ids))
        return f"<decoded-{len(ids)}-toks>"


def _build_embedder() -> tuple[MiniLMEmbedder, _FakeTokenizer, MagicMock]:
    """Return an embedder with stub model + tokenizer (no real weights)."""
    emb = MiniLMEmbedder.__new__(MiniLMEmbedder)
    tokenizer = _FakeTokenizer()
    fake_encode = MagicMock(
        return_value=np.zeros((1, MINILM_NDIMS), dtype=np.float32),
    )
    fake_model = MagicMock()
    fake_model.tokenizer = tokenizer
    fake_model.encode = fake_encode
    emb._model = fake_model  # pyright: ignore[reportPrivateUsage]
    return emb, tokenizer, fake_encode


@pytest.mark.asyncio
async def test_short_input_passes_through() -> None:
    """Input under the 256-token cap is forwarded unchanged; no warning, no decode."""
    emb, tokenizer, fake_encode = _build_embedder()
    short_text = "x" * 32  # 32 tokens via the fake tokenizer's char-per-token rule

    out = await emb.embed([short_text], kind="document")

    assert len(out) == 1
    # Tokenizer was consulted once for the length check; nothing was decoded.
    assert tokenizer.encode_calls == [(short_text, False)]
    assert tokenizer.decode_calls == []
    # The verbatim text was passed through to encode().
    forwarded: list[str] = fake_encode.call_args.args[0]
    assert forwarded == [short_text]


@pytest.mark.asyncio
async def test_long_input_clipped_with_warning(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Input over 256 tokens is truncated and an `minilm.input_clipped` warning fires."""
    emb, tokenizer, fake_encode = _build_embedder()
    over_cap = "x" * (MINILM_MAX_TOKENS + 10)  # 266 tokens

    await emb.embed([over_cap], kind="query")

    # The clip path called decode with exactly MINILM_MAX_TOKENS ids.
    assert len(tokenizer.decode_calls) == 1
    assert len(tokenizer.decode_calls[0]) == MINILM_MAX_TOKENS

    # Encode received the decoded (clipped) string, not the 266-char original.
    forwarded: list[str] = fake_encode.call_args.args[0]
    assert forwarded[0] != over_cap
    assert forwarded[0] == f"<decoded-{MINILM_MAX_TOKENS}-toks>"

    # Structlog (JSONRenderer) writes the warning to stdout; assert the
    # event name + truncation telemetry are present.
    captured: Any = capsys.readouterr()
    assert "minilm.input_clipped" in captured.out
    assert '"original_tokens": 266' in captured.out
    assert f'"max_tokens": {MINILM_MAX_TOKENS}' in captured.out
