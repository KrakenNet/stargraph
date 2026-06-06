# SPDX-License-Identifier: Apache-2.0
"""FR-6 loud-fail integration tests for the DSPy adapter (verbatim amendment 1).

Asserts the three behaviours required by ``requirements.md §FR-6``:

1. Constructing the DSPy adapter with ``use_json_adapter_fallback=False`` and
   forcing a Pydantic-constraint violation raises
   :class:`stargraph.errors.AdapterFallbackError` -- never silently degrades.
2. The :class:`stargraph.adapters.dspy._LoudFallbackFilter` (design §3.3.1)
   installed on ``logging.getLogger("dspy.adapters.json_adapter")`` regex-matches
   the DSPy fallback warning ``'Failed to use structured output format, falling
   back to JSON mode'`` and converts it to an ``AdapterFallbackError`` -- the
   warning text MUST NOT leak through to the captured log handler.
3. A pin-range guard skips the suite if ``dspy.__version__ < "3.0.4"`` (the
   release that introduced the ``use_json_adapter_fallback`` flag) and is
   collected as a no-op when ``dspy`` is not installed.

This is the [TDD-RED] half of the loud-fail seam: ``stargraph.adapters.dspy`` does
not yet exist (created in Task 3.4 [TDD-GREEN]), so importing it raises
``ImportError`` -- the verify gate ``grep -qE "(FAILED|ERROR)"`` matches that.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pytest

# Skip the whole module cleanly if dspy isn't installed (e.g. minimal CI).
dspy = pytest.importorskip("dspy", reason="dspy required for FR-6 loud-fail tests")

# Pin-range guard from FR-6 amendment 1 ("dspy>=3.0.4,<3.4"). We compare the
# leading numeric components so dev tags like "3.0.4.dev0" still pass.
_MIN_DSPY = (3, 0, 4)


def _parse_version(raw: str) -> tuple[int, ...]:
    """Extract the leading ``MAJOR.MINOR.PATCH`` numeric tuple from a version."""
    parts: list[int] = []
    for chunk in raw.split("."):
        match = re.match(r"\d+", chunk)
        if match is None:
            break
        parts.append(int(match.group(0)))
    return tuple(parts)


if _parse_version(getattr(dspy, "__version__", "0")) < _MIN_DSPY:
    pytest.skip(
        f"dspy>={'.'.join(str(p) for p in _MIN_DSPY)} required for FR-6 "
        f"(use_json_adapter_fallback flag); got dspy {dspy.__version__}",
        allow_module_level=True,
    )


# Verbatim needle from FR-6 amendment 1; mirrored in
# ``stargraph.adapters.dspy._LoudFallbackFilter._NEEDLE``.
_FALLBACK_WARNING = "Failed to use structured output format, falling back to JSON mode"
_FALLBACK_RE = re.compile(re.escape(_FALLBACK_WARNING))


@pytest.fixture
def loud_dspy() -> Any:
    """Import the DSPy adapter under test.

    Lives behind a fixture so collection succeeds even in the [TDD-RED] state
    where ``stargraph.adapters.dspy`` is not yet implemented; per-test usage
    surfaces the ``ImportError`` as a test failure (which the verify gate
    matches via ``grep -qE "(FAILED|ERROR)"``).
    """
    import importlib

    stargraph_dspy: Any = importlib.import_module("stargraph.adapters.dspy")
    return stargraph_dspy


def test_schema_mismatch_raises_adapter_fallback_error(loud_dspy: Any) -> None:
    """FR-6 case 1: ``use_json_adapter_fallback=False`` + schema mismatch raises.

    Per FR-6 verbatim amendment 1: when the bound DSPy module is invoked with a
    prompt that forces a Pydantic-constraint violation in the structured-output
    parser, the adapter MUST raise :class:`AdapterFallbackError` rather than
    silently rewrite the call to ``JSONAdapter``.
    """
    from stargraph.errors import AdapterFallbackError

    # ``bind`` returns the stargraph-side DSPyNode; calling it with a schema-busting
    # input must surface ``AdapterFallbackError`` (no silent JSONAdapter swap).
    node = loud_dspy.bind(
        module=_schema_violating_module(),
        signature_map=_schema_violating_signature_map(),
    )
    with pytest.raises(AdapterFallbackError):
        node.acall(_schema_violating_input())


def test_fallback_warning_is_converted_no_leak(
    loud_dspy: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """FR-6 case 2: the loud-fallback filter converts the warning to an error.

    Per design §3.3.1, ``_LoudFallbackFilter`` is installed on
    ``logging.getLogger("dspy.adapters.json_adapter")`` and raises
    :class:`AdapterFallbackError` from inside ``filter()`` when the warning
    record's message matches ``_FALLBACK_WARNING``. The captured log MUST NOT
    contain the warning text -- the filter short-circuits emission.
    """
    from stargraph.errors import AdapterFallbackError

    target_logger = logging.getLogger("dspy.adapters.json_adapter")
    fallback_filter: logging.Filter = loud_dspy._LoudFallbackFilter()
    target_logger.addFilter(fallback_filter)

    record = logging.LogRecord(
        name="dspy.adapters.json_adapter",
        level=logging.WARNING,
        pathname=__file__,
        lineno=0,
        msg=_FALLBACK_WARNING,
        args=None,
        exc_info=None,
    )
    with (
        caplog.at_level(logging.WARNING, logger="dspy.adapters.json_adapter"),
        pytest.raises(AdapterFallbackError),
    ):
        target_logger.handle(record)

    # No leak: filter must short-circuit before the handler records the message.
    assert not any(_FALLBACK_RE.search(rec.getMessage()) for rec in caplog.records), (
        "loud-fallback filter must convert the warning to an error, not log it"
    )


# --- canary: DSPy needle still appears verbatim in upstream source --------------


def test_fallback_needle_present_in_installed_dspy(loud_dspy: Any) -> None:
    """CI canary: ``FALLBACK_NEEDLE`` must appear verbatim in the installed dspy package.

    The loud-fallback filter is a string-match against DSPy's warning text. Any
    DSPy patch-bump that rewords that warning would silently degrade the seam
    (filter stops matching, JSONAdapter fallback re-becomes silent). This
    canary scans every ``.py`` file in the installed ``dspy.adapters`` package
    for the verbatim needle; a fail signals "needle drifted, update
    ``FALLBACK_NEEDLE`` and re-test the filter".

    Scoped to ``dspy.adapters`` (not the whole package) to stay fast and to
    track the file the warning is actually emitted from.
    """
    import pathlib

    needle: str = loud_dspy.FALLBACK_NEEDLE
    adapters_pkg = pathlib.Path(dspy.adapters.__file__).parent  # type: ignore[attr-defined]
    matches: list[pathlib.Path] = [
        path for path in adapters_pkg.rglob("*.py") if needle in path.read_text(encoding="utf-8")
    ]
    assert matches, (
        f"FALLBACK_NEEDLE {needle!r} not found in any file under {adapters_pkg}; "
        f"DSPy {dspy.__version__} has likely reworded the JSONAdapter fallback warning. "
        f"Update stargraph.adapters.dspy.FALLBACK_NEEDLE to the new text."
    )


# --- helpers used only by case 1 -------------------------------------------------


def _schema_violating_module() -> object:
    """A minimal stand-in for a DSPy module whose signature is mismatched.

    The [TDD-GREEN] adapter (Task 3.4) wires this through ``dspy.Predict`` such
    that calling ``acall`` triggers a Pydantic-constraint violation; we keep the
    fixture inert here so [TDD-RED] still fails on the import line first.
    """
    return object()


def _schema_violating_signature_map() -> object:
    """Stand-in ``SignatureMap`` -- shape pinned in design §3.3.1."""
    return object()


def _schema_violating_input() -> dict[str, str]:
    """Input that violates the bound signature's Pydantic constraints."""
    return {"input": "value-that-fails-constraint"}
