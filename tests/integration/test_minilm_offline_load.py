# SPDX-License-Identifier: Apache-2.0
"""Integration: MiniLM offline-mode load (FR-15, AC-11).

Phase-3 integration coverage for mode 2 of the
:class:`stargraph.stores.embeddings.MiniLMEmbedder` loader. Sets
``HF_HUB_OFFLINE=1``, then attempts construction without a pre-staged
HF cache and asserts the loader propagates the
``huggingface_hub`` cache-miss error rather than reaching for the
network.

This test is :func:`@pytest.mark.slow`-gated by repository convention --
mode-2 requires either a real HF cache hit (which would download
~90 MB the first time) or the cache-miss negative path. The negative
path is what we exercise here, but we still keep the slow marker so
``pytest -q`` stays snappy and CI can opt in via ``--runslow``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from stargraph.stores.embeddings import MiniLMEmbedder

pytestmark = [
    pytest.mark.knowledge,
    pytest.mark.integration,
    pytest.mark.slow,
]


def test_offline_mode_without_cache_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``HF_HUB_OFFLINE=1`` + empty cache -> ``snapshot_download`` raises.

    Verifies the offline-mode error path: with ``HF_HUB_OFFLINE=1`` set
    and ``HF_HOME`` pointed at an empty ``tmp_path``,
    :class:`MiniLMEmbedder` cannot resolve the model directory and
    surfaces the ``huggingface_hub`` cache-miss exception. We do not
    pin to a specific exception class because the hub library has
    rotated the public name (``LocalEntryNotFoundError`` vs
    ``OfflineModeIsEnabled``) across minor versions.
    """
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    monkeypatch.setenv("TRANSFORMERS_OFFLINE", "1")

    with pytest.raises(Exception):  # noqa: B017
        MiniLMEmbedder(allow_download=False)
