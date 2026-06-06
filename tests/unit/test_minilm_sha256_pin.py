# SPDX-License-Identifier: Apache-2.0
"""TDD-GREEN: MiniLM safetensors sha256 pin (FR-15, AC-11).

Asserts :class:`stargraph.stores.embeddings.MiniLMEmbedder` enforces the
pinned :data:`stargraph.stores.embeddings.MINILM_SHA256` against the
on-disk safetensors weights:

1. :func:`test_sha256_match_no_error` -- when the hashed bytes equal
   the pin, construction succeeds.
2. :func:`test_sha256_drift_raises_mismatch` -- when the hash differs,
   :class:`stargraph.errors.EmbeddingModelHashMismatch` is raised with the
   expected/actual digests in ``context``.

Tests heavily mock ``huggingface_hub.snapshot_download``,
``sentence_transformers.SentenceTransformer``, and the streaming
``_hash_safetensors`` helper -- we never touch the 90 MB model weights
or the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from stargraph.errors import EmbeddingModelHashMismatch
from stargraph.stores import embeddings
from stargraph.stores.embeddings import MINILM_SHA256, MiniLMEmbedder

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def _patch_st(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch ``sentence_transformers.SentenceTransformer`` to a stub."""
    fake_module = MagicMock()
    fake_st = MagicMock()
    fake_module.SentenceTransformer = fake_st
    monkeypatch.setitem(__import__("sys").modules, "sentence_transformers", fake_module)
    return fake_st


def test_sha256_match_no_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash equals the pin -> embedder constructs cleanly."""
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"fake-weights")

    fake_st = _patch_st(monkeypatch)

    with (
        patch.object(embeddings, "_resolve_model_dir", return_value=tmp_path),
        patch.object(embeddings, "_hash_safetensors", return_value=MINILM_SHA256),
    ):
        emb = MiniLMEmbedder(allow_download=False)

    assert emb.content_hash == MINILM_SHA256
    fake_st.assert_called_once_with(str(tmp_path))


def test_sha256_drift_raises_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash drifts from the pin -> ``EmbeddingModelHashMismatch`` raised."""
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"tampered-weights")

    _patch_st(monkeypatch)

    drifted = "deadbeef" * 8  # 64-char hex sentinel != MINILM_SHA256

    with (
        patch.object(embeddings, "_resolve_model_dir", return_value=tmp_path),
        patch.object(embeddings, "_hash_safetensors", return_value=drifted),
        pytest.raises(EmbeddingModelHashMismatch) as exc_info,
    ):
        MiniLMEmbedder(allow_download=False)

    ctx: dict[str, Any] = exc_info.value.context
    assert ctx["expected_sha256"] == MINILM_SHA256
    assert ctx["actual_sha256"] == drifted
    assert ctx["model_id"] == "sentence-transformers/all-MiniLM-L6-v2"
