# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``stargraph.fathom._provenance`` (AC-6.3, AC-6.4, AC-6.5).

Exercises the 12-row coercion table in
:func:`stargraph.fathom._provenance._sanitize_provenance_slot` exhaustively --
each row is asserted both for its accept case (correct encoded type and
value) and, where applicable, its rejection case (silent ``str()`` coercion
forbidden, FR-9 / AC-6.4).

A second fixture pins AC-6.5 regex hazards: embedded quotes, parentheses,
NUL bytes, malformed ``?var`` bindings, and non-ASCII payloads must either
round-trip unchanged through the encoder (the encoder doesn't validate slot
*contents*, only types) or reach the adapter's three structural checks --
this module pins the encoder half. Three sanitization-check tests
(NUL, parens, identifier regex) live alongside in
``test_fathom_adapter.py``; here we only verify the encoder produces
identifier-shaped strings for typical bundle inputs and never silently
swallows hazardous payloads.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID

import pytest

from stargraph.errors import ValidationError
from stargraph.fathom._provenance import _sanitize_provenance_slot

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _Origin(Enum):
    USER = "user-v"
    SYSTEM = "system-v"


# AC-6.5: regex hazards fixture. Each entry is a string payload that must
# survive the encoder unchanged (encoder is type-driven, not content-driven)
# but might still be rejected by the adapter's structural checks downstream.
_AC65_HAZARDS: list[str] = [
    'embedded "double" quotes',
    "embedded 'single' quotes",
    "balanced (parens)",
    "unbalanced (parens",
    "trailing parens )",
    "embedded \x00 NUL byte",
    "?var malformed binding",
    "?",  # bare question-mark (truncated ?var)
    "non-ascii café-naïve-π",
    "non-bmp 𝓗arbor",  # noqa: RUF001 -- intentional ambiguous-char test (outside BMP)
    "newline\nembedded",
    "tab\tembedded",
    "; rule injection",
    "(deftemplate evil)",
]


# Platform-aware absolute Path for Row 7. ``Path("/abs/...")`` is *not*
# absolute on Windows (a drive letter is required), so anchoring at
# ``Path.cwd()`` produces a path that ``Path.is_absolute()`` accepts on every
# platform. Both the parametrized input and the expected POSIX output are
# derived from the same Path so the assertion stays exact.
_ABS_PATH_ROW7 = Path.cwd() / "abs" / "path" / "to" / "thing"


# ---------------------------------------------------------------------------
# AC-6.3: 12-row coercion table -- exhaustive accept cases.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_type", "expected"),
    [
        # Row 1: bool -> "TRUE" / "FALSE" (must match before int).
        (True, str, "TRUE"),
        (False, str, "FALSE"),
        # Row 2: int -> int passthrough.
        (0, int, 0),
        (-7, int, -7),
        (42, int, 42),
        # Row 3: str -> str passthrough.
        ("hello", str, "hello"),
        ("", str, ""),
        # Row 4: Decimal -> str(Decimal) (preserves precision; no float).
        (Decimal("1.0"), str, "1.0"),
        (Decimal("3.14159265358979323846"), str, "3.14159265358979323846"),
        # Row 5: UUID -> hex (no dashes; deterministic encoding).
        (
            UUID("12345678-1234-5678-1234-567812345678"),
            str,
            "12345678123456781234567812345678",
        ),
        # Row 6: tz-aware datetime (UTC) -> ISO8601 with Z suffix.
        (
            datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
            str,
            "2026-04-26T12:00:00Z",
        ),
        # Row 6b: tz-aware datetime with microseconds preserves them.
        (
            datetime(2026, 4, 26, 12, 0, 0, 123456, tzinfo=UTC),
            str,
            "2026-04-26T12:00:00.123456Z",
        ),
        # Row 6c: non-UTC tz-aware datetime is normalized to UTC.
        (
            datetime(2026, 4, 26, 14, 0, 0, tzinfo=timezone(timedelta(hours=2))),
            str,
            "2026-04-26T12:00:00Z",
        ),
        # Row 7: absolute Path -> POSIX string (platform-aware -- see
        # ``_ABS_PATH_ROW7`` for why ``Path("/...")`` is insufficient).
        (_ABS_PATH_ROW7, str, _ABS_PATH_ROW7.as_posix()),
        # Row 8: Enum -> "ClassName.MEMBER" form.
        (_Origin.USER, str, "_Origin.USER"),
        (_Origin.SYSTEM, str, "_Origin.SYSTEM"),
        # Row 9: dict -> sorted-keys JSON, no whitespace.
        ({"b": 2, "a": 1}, str, '{"a":1,"b":2}'),
        ({}, str, "{}"),
        # Row 10: list -> compact JSON.
        ([1, 2, 3], str, "[1,2,3]"),
        ([], str, "[]"),
    ],
)
def test_sanitize_provenance_slot_12_row_table_accept(
    value: Any, expected_type: type, expected: Any
) -> None:
    """Each accept row of AC-6.3 returns the expected type and value."""
    out = _sanitize_provenance_slot(value)
    assert isinstance(out, expected_type), f"{value!r} -> {out!r} (type {type(out).__name__})"
    assert out == expected


def test_sanitize_provenance_slot_bool_precedes_int() -> None:
    """``isinstance(True, int)`` is True -- bool branch must fire first.

    Pinned separately so a future refactor that re-orders the branches breaks
    this single assertion before the parametrized table even runs.
    """
    assert _sanitize_provenance_slot(True) == "TRUE"
    assert _sanitize_provenance_slot(False) == "FALSE"
    # Sanity: actual ints still work.
    assert _sanitize_provenance_slot(1) == 1
    assert _sanitize_provenance_slot(0) == 0


# ---------------------------------------------------------------------------
# AC-6.3 / AC-6.4: rejection cases -- no silent str() coercion.
# ---------------------------------------------------------------------------


class _Untyped:
    """Arbitrary unsupported type with a __repr__ that would deceive str()."""

    def __repr__(self) -> str:
        return "_Untyped()"


_REJECT_CASES: list[tuple[Any, str]] = [
    # Row 11a: float forbidden (FR-9).
    (1.0, "float not permitted"),
    (0.5, "float not permitted"),
    (float("nan"), "float not permitted"),
    (float("inf"), "float not permitted"),
    # Row 11b: None forbidden (slots are required by ProvenanceBundle).
    (None, "None not permitted"),
    # Row 11c: naive datetime forbidden (require tz-aware UTC).
    (datetime(2026, 4, 26, 12, 0, 0), "naive datetime not permitted"),
    # Row 11d: relative Path forbidden (require absolute).
    (Path("relative/path"), "relative Path not permitted"),
    (Path("./also/relative"), "relative Path not permitted"),
    # Row 12: arbitrary unsupported type -- no silent str() fallback.
    (_Untyped(), "unsupported type"),
    (object(), "unsupported type"),
    (b"raw bytes", "unsupported type"),
    (PurePosixPath("/abs"), "unsupported type"),  # not a real Path subclass
    (set[int](), "unsupported type"),
    ((1, 2), "unsupported type"),  # tuple not in accept list
]


@pytest.mark.parametrize(("value", "reason_substr"), _REJECT_CASES)
def test_sanitize_provenance_slot_rejects_with_validation_error(
    value: Any, reason_substr: str
) -> None:
    """Every rejected row raises :class:`ValidationError` with type+reason context (AC-6.4)."""
    with pytest.raises(ValidationError) as excinfo:
        _sanitize_provenance_slot(value)
    err = excinfo.value
    assert reason_substr in err.message
    # Context carries both the offending Python type name and the reason.
    assert "type" in err.context
    assert "reason" in err.context
    assert err.context["type"] == type(value).__name__
    assert reason_substr in err.context["reason"]


def test_sanitize_provenance_slot_rejection_does_not_leak_repr() -> None:
    """Rejection messages are reason-driven; they MUST NOT embed the rejected value.

    A silent ``str()`` fallback would be a subtle FR-9 / AC-6.4 violation: this
    test fails immediately if any future change starts interpolating the
    rejected value into the error message.
    """
    sentinel = "DO_NOT_LEAK_THIS_MARKER_INTO_ERRORS"

    class _Sneaky:
        def __repr__(self) -> str:
            return sentinel

        def __str__(self) -> str:
            return sentinel

    with pytest.raises(ValidationError) as excinfo:
        _sanitize_provenance_slot(_Sneaky())
    assert sentinel not in excinfo.value.message
    # Context records the type *name*, not str(value).
    assert excinfo.value.context.get("type") == "_Sneaky"


# ---------------------------------------------------------------------------
# AC-6.5: regex hazards fixture -- encoder is type-driven, not content-driven.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hazard", _AC65_HAZARDS)
def test_sanitize_provenance_slot_passes_through_string_hazards(hazard: str) -> None:
    """Hazardous strings pass the encoder unchanged.

    The encoder validates *types*, not content. AC-6.5 hazards (NUL bytes,
    parens, ``?var`` patterns, non-ASCII) are caught downstream by the
    adapter's three structural checks; pinning them here ensures we don't
    accidentally drop hazardous bytes (which would be a silent-coercion bug).
    """
    assert _sanitize_provenance_slot(hazard) == hazard


def test_sanitize_provenance_slot_dict_with_hazardous_value() -> None:
    """Dicts containing hazardous strings encode into compact JSON unchanged.

    The dict-encoding branch uses ``json.dumps(..., sort_keys=True)`` which
    escapes structural characters per JSON rules -- so ``'foo)'`` becomes
    ``"foo)"`` (parens escaped only as JSON; not stripped).
    """
    encoded = _sanitize_provenance_slot({"k": "balanced(parens)"})
    assert isinstance(encoded, str)
    # JSON encoding preserves the content but quotes it.
    assert '"k":' in encoded
    assert "balanced(parens)" in encoded


# ---------------------------------------------------------------------------
# AC-6.2 sanitization checks: 3-check primitives also exposed via adapter,
# but pinned here at module level so refactors that move the checks elsewhere
# still get caught. Live tests for the adapter-level wiring live in
# ``test_fathom_adapter.py``.
# ---------------------------------------------------------------------------


def test_sanitize_passes_balanced_parens_strings() -> None:
    """Balanced parens are content; the encoder doesn't reject them itself."""
    assert _sanitize_provenance_slot("(balanced)") == "(balanced)"
    assert _sanitize_provenance_slot("((nested))") == "((nested))"


def test_sanitize_passes_strings_with_nul_bytes_through_encoder() -> None:
    """NUL bytes pass the encoder; rejection happens at the adapter layer (AC-6.2).

    Pinned to make the layered design explicit: the 12-row encoder is
    type-only; the three structural checks are adapter-level concerns.
    """
    payload = "before\x00after"
    assert _sanitize_provenance_slot(payload) == payload


def test_sanitize_strings_through_identifier_shape() -> None:
    """Identifier-like strings round-trip; non-identifier strings also round-trip.

    The identifier regex check is enforced at the adapter, only on
    ``_origin``/``_source`` slots -- the encoder itself is permissive.
    """
    assert _sanitize_provenance_slot("user_origin-v1") == "user_origin-v1"
    assert _sanitize_provenance_slot("contains spaces") == "contains spaces"
