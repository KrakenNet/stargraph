# SPDX-License-Identifier: Apache-2.0
"""Adversarial property tests for the AC-6.2 identifier-slot regex check.

The Fathom-safe identifier shape is ``^[A-Za-z_][A-Za-z0-9_\\-]*$``. Any string
that does not match must be rejected when used as an ``_origin`` or ``_source``
provenance slot value -- both of which feed CLIPS as bare symbols.

Hypothesis generates arbitrary text via :func:`hypothesis.strategies.text`,
filters to the *non-matching* subset (``not _IDENT_RE.match(s)``), and
asserts the adapter raises :class:`stargraph.errors.ValidationError` for each
example. The conjugate property -- matching identifiers pass -- is also
asserted to pin the regex's accept side.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from stargraph.errors import ValidationError
from stargraph.fathom import FathomAdapter

if TYPE_CHECKING:
    from stargraph.fathom._provenance import ProvenanceBundle

# Local copy of the adapter's identifier regex (FR-15 / AC-6.5 contract).
# A pin asserts the patterns match -- if the adapter's source ever drifts,
# the pin fails immediately so the property is grounded in the real check.
_IDENT_PATTERN = r"^[A-Za-z_][A-Za-z0-9_\-]*$"
_IDENT_RE = re.compile(_IDENT_PATTERN)


class _RecordingEngine:
    """Minimal stand-in for :class:`fathom.Engine` -- records ``assert_fact`` calls.

    A bare ``MagicMock`` rejects ``assert_fact`` because ``unittest.mock`` treats
    any ``assert*`` attribute access as a typo'd assertion check. Using a tiny
    recorder class avoids that quirk while keeping the property test pure.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def assert_fact(self, template: str, slots: dict[str, Any]) -> None:
        self.calls.append((template, slots))


_PROFILE = settings(
    max_examples=150,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    deadline=None,
)


def _bundle(**overrides: Any) -> ProvenanceBundle:
    """Build a canonical provenance bundle with overrides for ``origin``/``source``."""
    base: dict[str, Any] = {
        "origin": "user",
        "source": "rule",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "step": 0,
        "confidence": Decimal("1.0"),
        "timestamp": datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return cast("ProvenanceBundle", base)


def _adapter() -> tuple[FathomAdapter, _RecordingEngine]:
    """Adapter wired to a recording stand-in -- no real Fathom required for slot checks."""
    engine = _RecordingEngine()
    return FathomAdapter(cast("Any", engine)), engine


# ---------------------------------------------------------------------------
# Regression strategy: arbitrary text filtered to non-matching identifiers.
# ---------------------------------------------------------------------------


def _is_invalid_identifier(s: str) -> bool:
    """Identifier check matches the adapter's ``_IDENT_RE`` exactly."""
    return _IDENT_RE.match(s) is None


_INVALID_IDENT = st.text(min_size=0, max_size=24).filter(_is_invalid_identifier)


@_PROFILE
@given(payload=_INVALID_IDENT)
def test_origin_slot_rejects_non_identifier(payload: str) -> None:
    """Every non-matching ``_origin`` value triggers a ValidationError."""
    adapter, _ = _adapter()
    with pytest.raises(ValidationError):
        adapter.assert_with_provenance(
            template="evidence",
            slots={},
            provenance=_bundle(origin=payload),
        )


@_PROFILE
@given(payload=_INVALID_IDENT)
def test_source_slot_rejects_non_identifier(payload: str) -> None:
    """Every non-matching ``_source`` value triggers a ValidationError."""
    adapter, _ = _adapter()
    with pytest.raises(ValidationError):
        adapter.assert_with_provenance(
            template="evidence",
            slots={},
            provenance=_bundle(source=payload),
        )


# ---------------------------------------------------------------------------
# Conjugate property: matching identifiers pass.
# ---------------------------------------------------------------------------


_VALID_IDENT = st.from_regex(_IDENT_RE, fullmatch=True).filter(
    lambda s: "\x00" not in s and s.count("(") == s.count(")")
)


@_PROFILE
@given(payload=_VALID_IDENT)
def test_valid_identifier_passes_origin_check(payload: str) -> None:
    """Strings matching the identifier regex flow through the structural checks."""
    adapter, recorder = _adapter()
    adapter.assert_with_provenance(
        template="evidence",
        slots={},
        provenance=_bundle(origin=payload),
    )
    assert len(recorder.calls) == 1
    assert recorder.calls[0][1]["_origin"] == payload


@_PROFILE
@given(payload=_VALID_IDENT)
def test_valid_identifier_passes_source_check(payload: str) -> None:
    """Strings matching the identifier regex pass the ``_source`` check."""
    adapter, recorder = _adapter()
    adapter.assert_with_provenance(
        template="evidence",
        slots={},
        provenance=_bundle(source=payload),
    )
    assert len(recorder.calls) == 1
    assert recorder.calls[0][1]["_source"] == payload


# ---------------------------------------------------------------------------
# Spot-check: regex pattern matches the documented Fathom shape.
# ---------------------------------------------------------------------------


def test_local_ident_re_matches_documented_pattern() -> None:
    """The local identifier regex IS ``^[A-Za-z_][A-Za-z0-9_\\-]*$`` -- pinned here."""
    assert _IDENT_RE.pattern == r"^[A-Za-z_][A-Za-z0-9_\-]*$"
    assert _IDENT_RE.match("user_origin-v1")
    assert _IDENT_RE.match("_underscore")
    assert not _IDENT_RE.match("")
    assert not _IDENT_RE.match("0starts_with_digit")
    assert not _IDENT_RE.match("contains spaces")
    assert not _IDENT_RE.match("(parens)")


def test_local_ident_re_matches_adapter_pattern() -> None:
    """Local pin equals the adapter's compiled pattern (FR-15 / AC-6.5 contract).

    Importing the adapter's private ``_CLIPS_IDENT_RE`` would trip pyright's
    ``reportPrivateUsage``; instead we read the source and assert the pattern
    appears verbatim, keeping the property's filter grounded in the real check.
    """
    from pathlib import Path as _Path

    adapter_src = (
        _Path(__file__).resolve().parents[2] / "src" / "stargraph" / "fathom" / "_adapter.py"
    ).read_text(encoding="utf-8")
    assert _IDENT_PATTERN in adapter_src, (
        f"adapter regex pattern {_IDENT_PATTERN!r} not found in _adapter.py"
    )


def test_clips_ident_re_compiles() -> None:
    """The local identifier regex is a real compiled :class:`re.Pattern`."""
    assert isinstance(_IDENT_RE, re.Pattern)
