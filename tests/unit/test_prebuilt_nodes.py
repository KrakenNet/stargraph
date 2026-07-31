# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the prebuilt-node presets (``stargraph.nodes.prebuilt``).

Pure surfaces only -- post-processors, instruction/signature synthesis,
and the :class:`PrebuiltNode` wrapper -- no dspy import, no LM. The
build path against installed dspy lives in
``tests/integration/test_prebuilt_node_builder.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from stargraph.errors import IRValidationError, StargraphRuntimeError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.nodes.prebuilt import (
    PREBUILT_KINDS,
    PrebuiltNode,
    classify_instructions,
    extract_signature,
    judge_instructions,
    post_classify,
    post_extract,
    post_judge,
    post_plan,
    post_reason,
    post_summarize,
)

pytestmark = pytest.mark.unit


def _spec(kind: str = "extract", **config: object) -> NodeSpec:
    return NodeSpec(id="n1", kind=kind, config=dict(config))


# ---------------------------------------------------------------- wrapper


class _FakeInner(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del state, ctx
        return {"answer": "42", "reasoning": "thought hard", "_leak": "internals"}


class _EmptyState(BaseModel):
    pass


async def test_prebuilt_node_applies_post_processor() -> None:
    node = PrebuiltNode(inner=_FakeInner(), post=post_reason)
    out = await node.execute(_EmptyState(), ctx=None)  # pyright: ignore[reportArgumentType]
    assert out == {"answer": "42", "rationale": "thought hard"}


# --------------------------------------------------------- post-processors


def test_post_reason_whitelists_and_renames() -> None:
    out = post_reason({"answer": "a", "reasoning": "r", "_store": "leak"})
    assert out == {"answer": "a", "rationale": "r"}


def test_post_summarize_whitelists() -> None:
    assert post_summarize({"summary": "s", "reasoning": "r"}) == {"summary": "s"}


def test_post_classify_normalizes_label_and_confidence() -> None:
    post = post_classify(["Benign", "Malicious"])
    out = post({"label": " malicious. ", "confidence": 1.7})
    assert out == {"verdict": "Malicious", "confidence": "1.0"}


def test_post_classify_unknown_label_fails_loud() -> None:
    post = post_classify(["yes", "no"])
    with pytest.raises(StargraphRuntimeError, match="outside the configured set"):
        post({"label": "maybe", "confidence": 0.5})


def test_post_classify_non_numeric_confidence_fails_loud() -> None:
    post = post_classify(["yes", "no"])
    with pytest.raises(StargraphRuntimeError, match="non-numeric confidence"):
        post({"label": "yes", "confidence": "very sure"})


def test_post_extract_keeps_only_declared_fields() -> None:
    post = post_extract({"name": "str", "age": "int"})
    out = post({"name": "Ada", "age": 36, "reasoning": "leak"})
    assert out == {"name": "Ada", "age": 36}
    assert post({}) == {"name": None, "age": None}


@pytest.mark.parametrize(
    ("raw", "expected"), [("pass", "pass"), (" PASS. ", "pass"), ("Fail", "fail")]
)
def test_post_judge_normalizes_verdict(raw: str, expected: str) -> None:
    out = post_judge({"verdict": raw, "score": -0.2, "reasoning": "because"})
    assert out == {"verdict": expected, "score": "0.0", "rationale": "because"}


def test_post_judge_rejects_other_verdicts() -> None:
    with pytest.raises(StargraphRuntimeError, match="other than pass/fail"):
        post_judge({"verdict": "maybe", "score": 0.5})


def test_post_plan_coerces_tasks_to_str_list() -> None:
    assert post_plan({"tasks": ["a", 2]}) == {"tasks": ["a", "2"]}


def test_post_plan_rejects_non_list() -> None:
    with pytest.raises(StargraphRuntimeError, match="non-list tasks"):
        post_plan({"tasks": "do everything"})


# ---------------------------------------------------------------- synthesis


def test_classify_instructions_name_labels() -> None:
    text = classify_instructions(["benign", "malicious"])
    assert "benign, malicious" in text
    assert "exactly one" in text


def test_judge_instructions_embed_rubric() -> None:
    text = judge_instructions("complete + cited")
    assert "complete + cited" in text
    assert "'pass' or 'fail'" in text


def test_extract_signature_compiles_field_map() -> None:
    sig = extract_signature("text", {"name": "str", "age": "int", "tags": "list"}, spec=_spec())
    assert sig == "text -> name: str, age: int, tags: list[str]"


def test_extract_signature_unknown_type_fails() -> None:
    with pytest.raises(IRValidationError, match="unknown type 'banana'"):
        extract_signature("text", {"q": "banana"}, spec=_spec())


def test_extract_signature_bad_field_name_fails() -> None:
    with pytest.raises(IRValidationError, match="must be a valid identifier"):
        extract_signature("text", {"bad-name": "str"}, spec=_spec())


def test_prebuilt_kinds_constant() -> None:
    assert PREBUILT_KINDS == (
        "classify",
        "extract",
        "judge",
        "plan",
        "reason",
        "summarize",
    )
