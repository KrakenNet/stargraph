# SPDX-License-Identifier: Apache-2.0
"""Reranker entry-point loader unit tests (FR-16, AC-4.4, Task 3.30).

Pins the public surface of :func:`stargraph.stores._rerank_loader.load_reranker`:

1. ``load_reranker(None)`` -- returns the always-available
   :class:`~stargraph.stores.rerankers.RRFReranker` (the documented default).
2. ``load_reranker("")`` -- empty string is treated as "no name",
   returns :class:`RRFReranker` (loader uses ``if not name``).
3. ``load_reranker("does-not-exist")`` -- raises :class:`KeyError`
   when no matching entry point is registered under
   ``stargraph.rerankers``.

Reaffirms task 2.9's loader contract; lives here as the canonical
unit test once the entry-point group ships further plug-ins in Phase-2.
"""

from __future__ import annotations

import pytest

from stargraph.stores._rerank_loader import load_reranker
from stargraph.stores.rerankers import RRFReranker

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


def test_load_reranker_none_returns_rrf() -> None:
    """``load_reranker(None)`` returns an :class:`RRFReranker` instance."""
    rr = load_reranker(None)
    assert isinstance(rr, RRFReranker)


def test_load_reranker_empty_string_returns_rrf() -> None:
    """Empty string is treated as 'no name' and falls through to the default."""
    rr = load_reranker("")
    assert isinstance(rr, RRFReranker)


def test_load_reranker_unknown_name_raises_keyerror() -> None:
    """A name with no matching entry point raises :class:`KeyError`."""
    with pytest.raises(KeyError, match="nonexistent"):
        load_reranker("nonexistent-reranker-xyz")
