# SPDX-License-Identifier: Apache-2.0
"""Property-based round-trip tests for the provenance encoder (AC-6.3, NFR-11).

Hypothesis strategies generate values for each row of the 12-row coercion
table. For accept rows the assertion is "encode produces the documented type
and a string-decoded value (where applicable) recovers the input". For reject
rows (``float``, ``None``, naive ``datetime``, relative ``Path``, arbitrary
unsupported types) the assertion is "encoder raises
:class:`stargraph.errors.ValidationError` for every generated example".

The encoder is *type-driven*: content does not affect accept/reject decisions
for accepted types, so these properties exercise breadth over each type.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from stargraph.errors import ValidationError
from stargraph.fathom._provenance import _sanitize_provenance_slot

# Cap example budget per property; with 9+ properties this keeps wall time
# bounded without sacrificing breadth (each strategy still hits its edges).
_PROFILE = settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])


class _Color(Enum):
    RED = "r"
    GREEN = "g"
    BLUE = "b"


# ---------------------------------------------------------------------------
# Accept-row properties: encode succeeds, expected output type, value recovers.
# ---------------------------------------------------------------------------


@_PROFILE
@given(s=st.text())
def test_str_passthrough(s: str) -> None:
    """Row 3 (str): identity passthrough for every text value."""
    out = _sanitize_provenance_slot(s)
    assert out == s
    assert isinstance(out, str)


@_PROFILE
@given(n=st.integers())
def test_int_passthrough(n: int) -> None:
    """Row 2 (int): identity passthrough; bool branch must not steal real ints."""
    out = _sanitize_provenance_slot(n)
    assert out == n
    # Bool is a subclass of int -- confirm the encoder did not coerce to "TRUE"/"FALSE".
    assert isinstance(out, int)
    assert not isinstance(out, bool)


@_PROFILE
@given(b=st.booleans())
def test_bool_to_symbol(b: bool) -> None:
    """Row 1 (bool): True -> "TRUE", False -> "FALSE"; precedes the int branch."""
    out = _sanitize_provenance_slot(b)
    assert out == ("TRUE" if b else "FALSE")
    assert isinstance(out, str)


@_PROFILE
@given(
    d=st.decimals(
        allow_nan=False,
        allow_infinity=False,
        min_value=Decimal("-1e18"),
        max_value=Decimal("1e18"),
    )
)
def test_decimal_round_trip(d: Decimal) -> None:
    """Row 4 (Decimal): encoded form parses back to the same Decimal."""
    out = _sanitize_provenance_slot(d)
    assert isinstance(out, str)
    assert Decimal(out) == d


@_PROFILE
@given(u=st.uuids())
def test_uuid_to_hex_round_trip(u: UUID) -> None:
    """Row 5 (UUID): 32-char hex with no dashes; reparses to the same UUID."""
    out = _sanitize_provenance_slot(u)
    assert isinstance(out, str)
    assert len(out) == 32
    assert "-" not in out
    assert UUID(out) == u


_TZ = st.sampled_from([UTC, timezone(timedelta(hours=2)), timezone(timedelta(hours=-5))])


@_PROFILE
@given(
    dt=st.datetimes(
        min_value=datetime(1970, 1, 1),
        max_value=datetime(2100, 1, 1),
        timezones=_TZ,
    )
)
def test_datetime_aware_iso_z_suffix(dt: datetime) -> None:
    """Row 6 (datetime): tz-aware ISO-8601 with ``Z`` suffix; reparses to same instant."""
    out = _sanitize_provenance_slot(dt)
    assert isinstance(out, str)
    assert out.endswith("Z")
    parsed = datetime.fromisoformat(out.replace("Z", "+00:00"))
    # Compare instants (encoder normalizes to UTC).
    assert parsed == dt.astimezone(UTC).replace(tzinfo=UTC)


_ABS_PATH_PARTS = st.lists(
    st.text(
        alphabet=st.characters(
            min_codepoint=0x21,
            max_codepoint=0x7E,
            # Exclude path separators, NUL, and Windows-reserved chars so the
            # generated parts are valid path components on every platform.
            blacklist_characters='/\x00\\:*?"<>|',
        ),
        min_size=1,
        max_size=8,
    ),
    min_size=1,
    max_size=4,
)


@_PROFILE
@given(parts=_ABS_PATH_PARTS)
def test_absolute_path_to_posix(parts: list[str]) -> None:
    """Row 7 (Path): absolute Path -> POSIX string; reparses byte-equal.

    Builds the absolute path from ``Path.cwd()`` so the input is genuinely
    absolute on every platform (``Path("/foo")`` is *not* absolute on
    Windows -- it has no drive letter).
    """
    p = Path.cwd().joinpath(*parts)
    out = _sanitize_provenance_slot(p)
    assert isinstance(out, str)
    assert out == p.as_posix()


@_PROFILE
@given(member=st.sampled_from(list(_Color)))
def test_enum_class_dot_member(member: _Color) -> None:
    """Row 8 (Enum): ``ClassName.MEMBER`` form; deterministic per member."""
    out = _sanitize_provenance_slot(member)
    assert out == f"_Color.{member.name}"


@_PROFILE
@given(
    payload=st.dictionaries(
        keys=st.text(min_size=1, max_size=8),
        values=st.one_of(
            st.text(),
            st.integers(),
            st.booleans(),
            st.none(),
        ),
        max_size=6,
    )
)
def test_dict_to_canonical_json(payload: dict[str, Any]) -> None:
    """Row 9 (dict): sorted-keys compact JSON; reparses to the same dict."""
    out = _sanitize_provenance_slot(payload)
    assert isinstance(out, str)
    # Sorted-keys -> canonical: json.dumps(payload, sort_keys=True) matches.
    assert out == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert json.loads(out) == payload


@_PROFILE
@given(
    payload=st.lists(
        st.one_of(st.text(), st.integers(), st.booleans(), st.none()),
        max_size=8,
    )
)
def test_list_to_canonical_json(payload: list[Any]) -> None:
    """Row 10 (list): compact JSON; reparses to the same list."""
    out = _sanitize_provenance_slot(payload)
    assert isinstance(out, str)
    assert json.loads(out) == payload


# ---------------------------------------------------------------------------
# Reject-row properties: encoder raises ValidationError for every example.
# ---------------------------------------------------------------------------


@_PROFILE
@given(f=st.floats(allow_nan=True, allow_infinity=True))
def test_float_always_rejected(f: float) -> None:
    """Row 11a (float): every float -- finite, NaN, inf -- is rejected (FR-9)."""
    with pytest.raises(ValidationError) as excinfo:
        _sanitize_provenance_slot(f)
    assert "float not permitted" in excinfo.value.message


@_PROFILE
@given(dt=st.datetimes(allow_imaginary=False))
def test_naive_datetime_always_rejected(dt: datetime) -> None:
    """Row 11c: naive datetime (no tzinfo) is always rejected."""
    naive = dt.replace(tzinfo=None)
    with pytest.raises(ValidationError) as excinfo:
        _sanitize_provenance_slot(naive)
    assert "naive datetime" in excinfo.value.message


_REL_PATH_PARTS = st.lists(
    st.text(
        alphabet=st.characters(
            min_codepoint=0x21,
            max_codepoint=0x7E,
            # Match _ABS_PATH_PARTS: exclude path separators, NUL, and
            # Windows-reserved chars (notably ``:`` which Windows interprets
            # as a drive separator and could yield a drive-relative path).
            blacklist_characters='/\x00\\:*?"<>|',
        ),
        min_size=1,
        max_size=8,
    ),
    min_size=1,
    max_size=4,
)


@_PROFILE
@given(parts=_REL_PATH_PARTS)
def test_relative_path_always_rejected(parts: list[str]) -> None:
    """Row 11d: relative Path is always rejected (require absolute)."""
    p = Path("/".join(parts))  # no leading slash -> relative
    with pytest.raises(ValidationError) as excinfo:
        _sanitize_provenance_slot(p)
    assert "relative Path" in excinfo.value.message
