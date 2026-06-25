# SPDX-License-Identifier: Apache-2.0

import pytest

from stargraph.cli._summary import (
    _fmt_duration,
    _is_artifact_field,
    _is_default,
)  # pyright: ignore[reportPrivateUsage]

@pytest.mark.unit
@pytest.mark.parametrize(
    ("milliseconds", "expected"),
    [
        (500, "500ms"),
        (1000, "1.0s"),
        (1500, "1.5s"),
    ],
)
def test_fmt_duration(milliseconds: int, expected: str) -> None:
    assert _fmt_duration(milliseconds) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        [],
        {},
        0,
        False,
    ],
)
def test_is_default_returns_true(value) -> None:
    assert _is_default(value) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "value",
    [
        "x",
        5,
        [1],
    ],
)
def test_is_default_returns_false(value) -> None:
    assert _is_default(value) is False


@pytest.mark.unit
def test_is_artifact_field_true() -> None:
    assert (
        _is_artifact_field(
            "out_files",
            {"a.txt": "hello"},
        )
        is True
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("output", {"a.txt": "hello"}),
        ("out_files", []),
        ("out_files", {"a.txt": 5}),
    ],
)
def test_is_artifact_field_false(name, value) -> None:
    assert _is_artifact_field(name, value) is False
