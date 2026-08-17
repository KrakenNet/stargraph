# SPDX-License-Identifier: Apache-2.0
"""``_lm_usage`` -- reading LM calls (and cache hits) off the client.

A ``kind: dspy`` node calls its LM directly, so no LM event ever reaches the
bus and the progress printer cannot count one. The summary therefore asks the
configured client afterwards. DSPy's disk cache is on by default, so the count
that matters is really two counts: completions, and how many of those came
back from cache without a request.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from stargraph.cli.run import _lm_usage  # pyright: ignore[reportPrivateUsage]

if TYPE_CHECKING:
    import pytest


def _fake_dspy(history: list[dict[str, Any]] | None) -> SimpleNamespace:
    lm = None if history is None else SimpleNamespace(history=history)
    return SimpleNamespace(settings=SimpleNamespace(lm=lm))


def _entry(*, cache_hit: bool) -> dict[str, Any]:
    response: SimpleNamespace = SimpleNamespace()
    if cache_hit:
        response.cache_hit = True
    return {"response": response}


def test_a_run_that_never_imported_dspy_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delitem(sys.modules, "dspy", raising=False)
    assert _lm_usage() == (0, 0)


def test_no_configured_lm_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "dspy", _fake_dspy(None))  # pyright: ignore[reportArgumentType]
    assert _lm_usage() == (0, 0)


def test_every_completion_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    history = [_entry(cache_hit=False), _entry(cache_hit=False)]
    monkeypatch.setitem(sys.modules, "dspy", _fake_dspy(history))  # pyright: ignore[reportArgumentType]
    assert _lm_usage() == (2, 0)


def test_cached_completions_are_counted_apart_from_the_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: a cached run must be distinguishable from a live one.

    Both runs record the same number of completions; only the second number
    says whether a server was ever contacted.
    """
    history = [_entry(cache_hit=True), _entry(cache_hit=False), _entry(cache_hit=True)]
    monkeypatch.setitem(sys.modules, "dspy", _fake_dspy(history))  # pyright: ignore[reportArgumentType]
    assert _lm_usage() == (3, 2)
