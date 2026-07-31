# SPDX-License-Identifier: Apache-2.0
"""Prebuilt-kind builder tests (P3a) against the real installed dspy.

Mirrors ``test_dspy_node_builder.py``: building a preset node constructs
real ``dspy.Predict`` / ``dspy.ChainOfThought`` modules but performs no
LM call. Locks the synthesized signature per kind and every config
loud-fail path.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("dspy", reason="dspy required for prebuilt builder tests")

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.errors import IRValidationError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.prebuilt import PrebuiltNode
from stargraph.nodes.registry import build_node_registry, node_kinds

pytestmark = pytest.mark.integration

_FAKE_LM_MODEL = "openai/fake-model-for-build-tests"


def _ctx() -> Any:
    """Typed shim over ``dspy.context`` (dspy is untyped; pyright is strict)."""
    return dspy.context(lm=dspy.LM(_FAKE_LM_MODEL, api_key="fake"))  # pyright: ignore[reportUnknownMemberType]


def _build(kind: str, **config: object) -> Any:
    registry = build_node_registry([NodeSpec(id="n", kind=kind, config=dict(config))])
    return registry["n"]


def _inner_module(node: Any) -> Any:
    inner = node._inner  # pyright: ignore[reportPrivateUsage]
    return inner._module  # pyright: ignore[reportPrivateUsage]


def _signature(node: Any) -> Any:
    """Signature of the wrapped module (ChainOfThought nests it under .predict)."""
    module = _inner_module(node)
    sig = getattr(module, "signature", None)
    return sig if sig is not None else module.predict.signature


def test_node_kinds_advertises_prebuilts() -> None:
    kinds = node_kinds()
    for kind in ("reason", "summarize", "classify", "extract", "judge", "plan"):
        assert kind in kinds


@pytest.mark.parametrize(
    ("kind", "config", "module_cls_name"),
    [
        ("reason", {}, "ChainOfThought"),
        ("summarize", {}, "ChainOfThought"),
        ("classify", {"labels": ["benign", "malicious"]}, "Predict"),
        ("extract", {"fields": {"name": "str"}}, "Predict"),
        ("judge", {"rubric": "complete + cited"}, "ChainOfThought"),
        ("plan", {}, "ChainOfThought"),
    ],
)
def test_each_kind_builds_expected_module(
    kind: str, config: dict[str, Any], module_cls_name: str
) -> None:
    with _ctx():
        node = _build(kind, **config)
    assert isinstance(node, PrebuiltNode)
    assert type(_inner_module(node)).__name__ == module_cls_name


def test_classify_signature_fields() -> None:
    with _ctx():
        node = _build("classify", labels=["yes", "no"], input="alert")
    signature: Any = _signature(node)
    assert list(signature.input_fields) == ["alert"]
    assert list(signature.output_fields) == ["label", "confidence"]


def test_judge_signature_and_rubric_instructions() -> None:
    with _ctx():
        node = _build("judge", rubric="grounded in sources")
    signature: Any = _signature(node)
    # ChainOfThought extends outputs with its reasoning field.
    assert "verdict" in signature.output_fields
    assert "score" in signature.output_fields
    assert "grounded in sources" in signature.instructions


def test_input_override_flows_into_signature() -> None:
    with _ctx():
        node = _build("summarize", input="document")
    signature: Any = _signature(node)
    assert list(signature.input_fields) == ["document"]


def test_per_node_model_override_needs_no_global_lm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FAKE_LM_KEY", "sk-from-env")
    with dspy.context(lm=None):  # pyright: ignore[reportUnknownMemberType]
        node = _build("reason", model=_FAKE_LM_MODEL, api_key_env="FAKE_LM_KEY")
    lm = _inner_module(node).get_lm()
    assert lm is not None
    assert lm.model == _FAKE_LM_MODEL


@pytest.mark.parametrize(
    ("kind", "config", "match"),
    [
        ("classify", {}, "invalid config"),  # labels required
        ("classify", {"labels": ["only-one"]}, "invalid config"),
        ("classify", {"labels": ["Dup", "dup"]}, "case-insensitively unique"),
        ("extract", {}, "invalid config"),  # fields required
        ("extract", {"fields": {"q": "banana"}}, "unknown type 'banana'"),
        ("judge", {}, "invalid config"),  # rubric required
        ("plan", {"input": "not an identifier"}, "valid identifier"),
        ("reason", {"unknown_key": 1}, "invalid config"),
    ],
)
def test_config_loud_fail_paths(kind: str, config: dict[str, Any], match: str) -> None:
    with _ctx(), pytest.raises(IRValidationError, match=match):
        _build(kind, **config)


def test_no_lm_anywhere_fails_loud() -> None:
    with (
        dspy.context(lm=None),  # pyright: ignore[reportUnknownMemberType]
        pytest.raises(IRValidationError, match="no LM configured"),
    ):
        _build("reason")
