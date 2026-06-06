# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest
import typer

from stargraph.cli._inputs import parse_inputs


def test_parses_string_int_bool() -> None:
    schema = {"name": "str", "n": "int", "ok": "bool"}
    out = parse_inputs(["name=alice", "n=42", "ok=true"], schema)
    assert out == {"name": "alice", "n": 42, "ok": True}


def test_zero_fills_missing_keys() -> None:
    schema = {"name": "str", "n": "int"}
    out = parse_inputs(["name=alice"], schema)
    assert out == {"name": "alice", "n": 0}


def test_unknown_key_raises() -> None:
    with pytest.raises(typer.BadParameter, match="unknown input"):
        parse_inputs(["bogus=x"], {"name": "str"})


def test_bad_int_raises() -> None:
    with pytest.raises(typer.BadParameter, match="not an integer"):
        parse_inputs(["n=notanumber"], {"n": "int"})


def test_value_can_contain_equals() -> None:
    out = parse_inputs(["brief=a=b=c"], {"brief": "str"})
    assert out == {"brief": "a=b=c"}


def test_bool_variants() -> None:
    schema = {"a": "bool", "b": "bool", "c": "bool", "d": "bool"}
    out = parse_inputs(["a=yes", "b=1", "c=false", "d=n"], schema)
    assert out == {"a": True, "b": True, "c": False, "d": False}


def test_missing_equals_raises() -> None:
    with pytest.raises(typer.BadParameter, match="key=value"):
        parse_inputs(["just-a-key"], {"name": "str"})


def test_bytes_coerces_from_string() -> None:
    out = parse_inputs(["data=hello"], {"data": "bytes"})
    assert out == {"data": b"hello"}
