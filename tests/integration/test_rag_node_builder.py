# SPDX-License-Identifier: Apache-2.0
"""``kind: rag`` builder + execute tests against real dspy + real SQLite.

Builder paths mirror ``test_react_code_builder.py`` (no LM calls). The
execute test ingests three documents into a real ``SQLiteDocStore``,
then drives the full node -- lexical retrieval, context assembly, and a
``DummyLM``-scripted ChainOfThought answer -- end to end.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("dspy", reason="dspy required for rag builder tests")

import dspy  # pyright: ignore[reportMissingTypeStubs]
from dspy.utils import DummyLM  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel

from stargraph.errors import IRValidationError
from stargraph.ir._models import NodeSpec
from stargraph.nodes.rag import RagNode
from stargraph.nodes.registry import build_node_registry
from stargraph.stores.sqlite_doc import SQLiteDocStore

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

_FAKE_LM_MODEL = "openai/fake-model-for-build-tests"


def _ctx_lm(lm: Any) -> Any:
    return dspy.context(lm=lm)  # pyright: ignore[reportUnknownMemberType]


def _build(**config: object) -> Any:
    registry = build_node_registry([NodeSpec(id="n", kind="rag", config=dict(config))])
    return registry["n"]


def _store_cfg(tmp_path: Path) -> list[dict[str, str]]:
    return [{"provider": "sqlite_doc", "path": str(tmp_path / "kb.db")}]


def test_rag_builds_from_registry(tmp_path: Path) -> None:
    with _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")):
        node = _build(stores=_store_cfg(tmp_path))
    assert isinstance(node, RagNode)
    assert node.config.k == 5


@pytest.mark.parametrize(
    ("config", "match"),
    [
        ({}, "invalid config"),  # stores required
        ({"stores": []}, "invalid config"),
        ({"stores": [{"provider": "unknown", "path": "x"}]}, "invalid config"),
        ({"stores": [{"provider": "sqlite_doc"}]}, "invalid config"),  # path required
        ({"stores": [{"provider": "sqlite_doc", "path": "x"}], "k": 0}, "invalid config"),
        (
            {"stores": [{"provider": "sqlite_doc", "path": "x"}], "query": "not id"},
            "valid identifier",
        ),
        ({"stores": [{"provider": "sqlite_doc", "path": "x"}], "extra": 1}, "invalid config"),
    ],
)
def test_config_loud_fail_paths(config: dict[str, Any], match: str) -> None:
    with (
        _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")),
        pytest.raises(IRValidationError, match=match),
    ):
        _build(**config)


def test_no_lm_anywhere_fails_loud(tmp_path: Path) -> None:
    with _ctx_lm(None), pytest.raises(IRValidationError, match="no LM configured"):
        _build(stores=_store_cfg(tmp_path))


# ------------------------------------------------------------ execute path


class _RagState(BaseModel):
    query: str = "why is the sky blue"


async def test_rag_execute_end_to_end_sqlite(tmp_path: Path) -> None:
    store = SQLiteDocStore(tmp_path / "kb.db")
    await store.bootstrap()
    await store.put("doc-rayleigh", "the sky is blue because Rayleigh scattering")
    await store.put("doc-ocean", "why the ocean looks blue on clear days")
    await store.put("doc-tax", "annual tax filing deadlines")

    with _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")):
        node = _build(stores=_store_cfg(tmp_path), k=2)
    assert isinstance(node, RagNode)

    lm = DummyLM(
        [
            {
                "reasoning": "Context [doc-rayleigh] explains it.",
                "answer": "Rayleigh scattering [doc-rayleigh]",
            }
        ]
    )
    ctx: Any = SimpleNamespace(run_id="run-rag-e2e")
    with _ctx_lm(lm):
        out = await node.execute(_RagState(), ctx)

    assert out["answer"] == "Rayleigh scattering [doc-rayleigh]"
    assert out["citations"][0] == "doc-rayleigh"
    assert "doc-tax" not in out["citations"]
    assert set(out) == {"answer", "citations"}
