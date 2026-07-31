# SPDX-License-Identifier: Apache-2.0
"""Prebuilt subgraph bundles (P3b) -- build, compile, and loop end-to-end.

Every shipped bundle must: validate as IR, compile its state class,
build every node from the registry, and compile its SKILL.md wrapper.
Rule-carrying bundles must produce a live routing engine. The capstone
test mounts evaluator-optimizer as a rule-routed subgraph child and
drives one full fail->refine->pass loop with a scripted DummyLM,
asserting the judge's rationale actually reached the second draft's
brief (the feedback re-injection contract).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
import yaml

pytest.importorskip("dspy", reason="dspy required for bundle build tests")

import dspy  # pyright: ignore[reportMissingTypeStubs]
from dspy.utils import DummyLM  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, Field

from stargraph.bundles import BUNDLE_NAMES, bundle_path
from stargraph.errors import StargraphRuntimeError
from stargraph.fathom import build_ir_routing
from stargraph.graph.definition import Graph
from stargraph.ir import IRDocument
from stargraph.ir._models import NodeSpec
from stargraph.nodes.registry import build_node_registry
from stargraph.nodes.subgraph import SubGraphNode
from stargraph.skills.markdown import compile_skill_md

pytestmark = pytest.mark.integration

_FAKE_LM_MODEL = "openai/fake-model-for-build-tests"

#: Bundles whose rules must compile to a live child routing engine.
_RULED = tuple(name for name in BUNDLE_NAMES if name != "hitl-approval")


def _ctx_lm(lm: Any) -> Any:
    return dspy.context(lm=lm)  # pyright: ignore[reportUnknownMemberType]


def _load_ir(name: str) -> IRDocument:
    raw = yaml.safe_load((bundle_path(name) / "graph.yaml").read_text(encoding="utf-8"))
    return IRDocument.model_validate(raw)


def test_bundle_path_unknown_is_loud() -> None:
    with pytest.raises(StargraphRuntimeError, match="unknown bundle"):
        bundle_path("no-such-bundle")


@pytest.mark.parametrize("name", BUNDLE_NAMES)
def test_bundle_builds(name: str) -> None:
    ir = _load_ir(name)
    graph = Graph(ir)  # validates IR + resolves the state class
    assert graph.state_schema().__class__  # instantiable with defaults

    with _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")):
        registry = build_node_registry(ir.nodes, ir_dir=bundle_path(name))
    assert set(registry) == {n.id for n in ir.nodes}


@pytest.mark.parametrize("name", BUNDLE_NAMES)
def test_bundle_skill_md_compiles(name: str) -> None:
    compiled = compile_skill_md(bundle_path(name) / "SKILL.md")
    spec: Any = compiled.spec
    assert spec.name == name
    assert spec.subgraph is not None and spec.subgraph.endswith("graph.yaml")


@pytest.mark.parametrize("name", _RULED)
def test_ruled_bundles_compile_live_routing(name: str) -> None:
    ir = _load_ir(name)
    assert build_ir_routing(ir, Graph(ir).state_schema) is not None


def test_hitl_bundle_is_sequential() -> None:
    ir = _load_ir("hitl-approval")
    assert build_ir_routing(ir, Graph(ir).state_schema) is None


# --------------------------------------------- evaluator-optimizer loop


class _RecordingBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def send(self, event: Any, *, fathom: Any = None) -> None:
        del fathom
        self.events.append(event)


class _ParentState(BaseModel):
    task: str = "write a haiku about CLIPS"
    brief: str = ""
    answer: str = ""
    verdict: str = ""
    score: str = ""
    rationale: str = ""
    citations: list[str] = Field(default_factory=list)


async def test_evaluator_optimizer_loop_end_to_end() -> None:
    spec = NodeSpec(
        id="refine-loop",
        kind="subgraph",
        spec=str(bundle_path("evaluator-optimizer") / "graph.yaml"),
    )
    with _ctx_lm(dspy.LM(_FAKE_LM_MODEL, api_key="fake")):
        registry = build_node_registry([spec])
    node = registry["refine-loop"]
    assert isinstance(node, SubGraphNode)

    lm = DummyLM(
        [
            {"reasoning": "first try", "answer": "draft-1"},
            {"reasoning": "too vague, name the engine", "verdict": "fail", "score": "0.2"},
            {"reasoning": "second try", "answer": "draft-2 names CLIPS"},
            {"reasoning": "specific now", "verdict": "pass", "score": "0.9"},
        ]
    )
    bus = _RecordingBus()
    ctx: Any = SimpleNamespace(run_id="run-bundle-eo", bus=bus, fathom=None)

    with _ctx_lm(lm):
        outputs = await node.execute(_ParentState(), ctx)

    assert outputs["answer"] == "draft-2 names CLIPS"
    assert outputs["verdict"] == "pass"
    assert outputs["score"] == "0.9"
    # Feedback re-injection: the judge's rationale reached the retry brief.
    assert "too vague, name the engine" in outputs["brief"]

    hops = [(e.from_node, e.to_node, e.reason) for e in bus.events]
    assert hops == [
        ("brief", "draft", "continue"),
        ("draft", "evaluate", "continue"),
        ("evaluate", "brief", "goto"),
        ("brief", "draft", "continue"),
        ("draft", "evaluate", "continue"),
        ("evaluate", "", "halt"),
    ]
    assert all(e.branch_id == "refine-loop" for e in bus.events)
