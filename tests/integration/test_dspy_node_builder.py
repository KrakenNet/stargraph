# SPDX-License-Identifier: Apache-2.0
"""``kind: dspy`` builder tests (P0.2 — real DSPyNode from NodeSpec.config).

Exercises :func:`stargraph.nodes.dspy.dspy_node_from_config` through the
registry path with the real installed dspy: signature parsing, module
selection (predict/cot), identity + explicit signature maps, the per-node
LM override, and every loud-fail path (no LM anywhere, malformed
signature, unknown config keys). No test performs an LM call — building a
``dspy.Predict``/``dspy.ChainOfThought`` and a ``dspy.LM`` handle is
network-free.
"""

from __future__ import annotations

from typing import Any

import pytest

# Skip cleanly if dspy isn't installed (matches loud-fallback test pattern).
pytest.importorskip("dspy", reason="dspy required for kind:dspy builder tests")

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.errors import IRValidationError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.dspy import DSPyNode, dspy_node_from_config
from stargraph.nodes.registry import build_node_registry

_FAKE_LM_MODEL = "openai/fake-model-for-build-tests"


def _lm() -> Any:
    return dspy.LM(_FAKE_LM_MODEL, api_key="fake")


def _ctx(lm: Any) -> Any:
    """Typed shim over ``dspy.context`` (dspy is untyped; pyright is strict)."""
    return dspy.context(lm=lm)  # pyright: ignore[reportUnknownMemberType]


def _spec(**config: object) -> NodeSpec:
    return NodeSpec(id="triage", kind="dspy", config=dict(config))


def test_no_lm_anywhere_fails_loud() -> None:
    """signature but no global LM and no config.model → build-time error."""
    with _ctx(None), pytest.raises(IRValidationError, match="no LM configured"):
        dspy_node_from_config(_spec(signature="question -> answer"))


def test_global_lm_builds_predict_node() -> None:
    with _ctx(_lm()):
        node = dspy_node_from_config(_spec(signature="question -> answer"))
    assert isinstance(node, DSPyNode)
    assert isinstance(
        node._module,  # pyright: ignore[reportPrivateUsage]
        dspy.Predict,
    )


def test_module_cot_builds_chain_of_thought() -> None:
    with _ctx(_lm()):
        node = dspy_node_from_config(_spec(signature="question -> answer", module="cot"))
    assert isinstance(
        node._module,  # pyright: ignore[reportPrivateUsage]
        dspy.ChainOfThought,
    )


def test_identity_signature_map_derived_from_inputs() -> None:
    """No signature_map → identity over the signature's *input* fields only."""
    with _ctx(_lm()):
        node = dspy_node_from_config(_spec(signature="alert, priors -> disposition"))
    assert node._signature_map == {  # pyright: ignore[reportPrivateUsage]
        "alert": "alert",
        "priors": "priors",
    }


def test_explicit_signature_map_wins() -> None:
    with _ctx(_lm()):
        node = dspy_node_from_config(
            _spec(signature="question -> answer", signature_map={"user_query": "question"})
        )
    assert node._signature_map == {  # pyright: ignore[reportPrivateUsage]
        "user_query": "question"
    }


def test_per_node_model_override_needs_no_global_lm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """config.model builds without a global LM; key comes from api_key_env."""
    monkeypatch.setenv("FAKE_LM_KEY", "sk-from-env")
    with _ctx(None):
        node = dspy_node_from_config(
            _spec(
                signature="question -> answer",
                model=_FAKE_LM_MODEL,
                api_key_env="FAKE_LM_KEY",
            )
        )
    module = node._module  # pyright: ignore[reportPrivateUsage]
    lm = module.get_lm()
    assert lm is not None
    assert lm.model == _FAKE_LM_MODEL


def test_malformed_signature_fails_loud() -> None:
    with (
        _ctx(_lm()),
        pytest.raises(IRValidationError, match="invalid signature"),
    ):
        dspy_node_from_config(_spec(signature="no arrow in here"))


def test_unknown_config_key_rejected() -> None:
    with pytest.raises(IRValidationError, match="invalid config"):
        dspy_node_from_config(_spec(signature="q -> a", not_a_real_key=0.7))


def test_unknown_module_value_rejected() -> None:
    with pytest.raises(IRValidationError, match="invalid config"):
        dspy_node_from_config(_spec(signature="q -> a", module="react"))


def test_registry_path_builds_real_node() -> None:
    """The registry's ``dspy`` short-kind reaches the real builder."""
    with _ctx(_lm()):
        registry = build_node_registry(
            [NodeSpec(id="d", kind="dspy", config={"signature": "question -> answer"})]
        )
    assert isinstance(registry["d"], DSPyNode)
