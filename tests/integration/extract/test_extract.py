# SPDX-License-Identifier: Apache-2.0
"""Extract skill — the deterministic value-add, with the LLM seam stubbed.

Journey: given text + the fields a caller wants, the node returns the ones the
model produced, flags the rest as ``missing``, and reports ``valid`` only when
nothing is missing. The model call is injected, so no live LM is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

from stargraph.skills.extract import EXTRACT, ExtractState
from stargraph.skills.extract.nodes.extract import Extract

pytestmark = pytest.mark.integration


class _Ctx:
    run_id = "extract-test"


def _stub(returns: dict[str, Any]) -> Extract:
    return Extract(extractor=lambda _text, _fields: returns)


async def test_all_fields_present_is_valid() -> None:
    node = _stub({"vendor": "Acme", "total": "$1,240.00"})
    state = ExtractState(text="Acme invoice total $1,240.00", target_fields=["vendor", "total"])
    out = await node.execute(state, _Ctx())
    assert out["valid"] is True
    assert out["missing"] == []
    assert out["fields"] == {"vendor": "Acme", "total": "$1,240.00"}


async def test_missing_fields_are_flagged() -> None:
    node = _stub({"vendor": "Acme", "total": ""})  # blank value counts as missing
    state = ExtractState(text="Acme invoice", target_fields=["vendor", "total", "due_date"])
    out = await node.execute(state, _Ctx())
    assert out["valid"] is False
    assert set(out["missing"]) == {"total", "due_date"}
    assert out["fields"] == {"vendor": "Acme"}


async def test_extra_keys_from_model_are_dropped() -> None:
    node = _stub({"vendor": "Acme", "hallucinated": "x"})
    state = ExtractState(text="Acme", target_fields=["vendor"])
    out = await node.execute(state, _Ctx())
    assert out["fields"] == {"vendor": "Acme"}  # only requested fields survive


async def test_empty_inputs_raise() -> None:
    node = _stub({})
    with pytest.raises(ValueError, match="text is required"):
        await node.execute(ExtractState(text="  ", target_fields=["x"]), _Ctx())
    with pytest.raises(ValueError, match="target_fields is required"):
        await node.execute(ExtractState(text="hi", target_fields=[]), _Ctx())


def test_skill_declares_only_state_channels() -> None:
    assert EXTRACT.kind.value == "utility"
    assert EXTRACT.site_id == "extract@0.1.0"
    assert EXTRACT.declared_output_keys == frozenset(
        {"text", "target_fields", "fields", "missing", "valid"}
    )
