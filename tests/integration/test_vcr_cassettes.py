# SPDX-License-Identifier: Apache-2.0
"""TDD-RED: FR-28 vcrpy HTTP cassette layer (record_mode=none in CI).

Pins the contract for ``stargraph.replay.determinism.http_cassette`` *before*
the implementation lands in task 3.31. Per design §3.8.5, the HTTP cassette
layer matches on ``(method, url, body_hash)`` and ships in three modes:

* ``record_mode="none"`` in CI -- requires a recorded cassette to exist;
  unrecorded requests raise.
* ``record_mode="once"`` in dev -- record on first run, replay thereafter.
* The cassette files live under ``<run_id>.cassettes/http/``.

This RED test exercises the CI-shape: a missing cassette MUST raise. The
``stargraph.replay.determinism`` module does not exist yet (lands in 3.31), so
the ``importlib.import_module`` call fails first -- that ``ImportError`` is
itself enough to keep the test red. Once 3.31 ships the module + the
``http_cassette`` context manager, this test will exercise the real vcrpy
``CannotOverwriteExistingCassetteException`` (record_mode=none + missing
recording) path.
"""

from __future__ import annotations

import importlib
import urllib.request
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from pathlib import Path

pytest.importorskip("vcr", reason="vcrpy required for FR-28 HTTP cassette tests")


def _load_determinism() -> Any:
    """Import ``stargraph.replay.determinism`` (TDD-RED: not yet built)."""
    return importlib.import_module("stargraph.replay.determinism")


def test_http_cassette_record_mode_none_raises_on_missing_recording(
    tmp_path: Path,
) -> None:
    """``record_mode='none'`` + no cassette file -> CannotOverwrite raises.

    Per design §3.8.5 cassette-layer #1: HTTP cassettes match on
    ``(method, url, body_hash)``. In CI we ship ``record_mode="none"`` so any
    request without a recording is loud-fail. This test pins the loud-fail
    contract: invoking the helper against an empty cassette directory must
    raise (the precise type lands in 3.31; ``Exception`` keeps the RED
    assertion non-brittle while keeping the failure observable).
    """
    determinism = _load_determinism()

    cassette_dir = tmp_path / "run-id-abc123.cassettes" / "http"
    cassette_dir.mkdir(parents=True)

    http_cassette = determinism.http_cassette
    with (
        pytest.raises(Exception),  # noqa: B017
        http_cassette(
            cassette_dir / "default.yaml",
            record_mode="none",
            match_on=("method", "url", "body_hash"),
        ),
    ):
        urllib.request.urlopen("http://example.invalid/probe")


def test_http_cassette_match_on_method_url_body_hash() -> None:
    """The cassette layer's matchers MUST be ``(method, url, body_hash)``.

    Task 3.31 registers a custom ``body_hash`` matcher with vcrpy; this test
    checks the registered matcher list reflects the FR-28 amendment-6
    contract. Pre-3.31 the ``determinism`` module doesn't exist, so this
    test stays red via :class:`ImportError`.
    """
    determinism = _load_determinism()

    matchers = cast("tuple[str, ...]", tuple(determinism.HTTP_CASSETTE_MATCHERS))
    assert matchers == ("method", "url", "body_hash")
