# SPDX-License-Identifier: Apache-2.0
"""Foundry integration tests.

The LLM planner and each smith's generator are stubbed for determinism, but the
build runs for real otherwise: a real graphsmith bundle is generated + gated +
landed, the assembler places it, and the verifier ACTUALLY RUNS the assembled
graph to a terminal ``done`` in a subprocess. So the foundry only reports success
when the stargraph it built genuinely runs end-to-end.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError
from tests.fixtures.smith_testkit import CTX, stub_build

from stargraph.skills.foundry.manifest import BuildManifest, ManifestItem, coerce
from stargraph.skills.foundry.nodes.assemble import Assemble
from stargraph.skills.foundry.nodes.execute import Execute
from stargraph.skills.foundry.nodes.plan import Plan
from stargraph.skills.foundry.nodes.verify import Verify
from stargraph.skills.foundry.state import State
from stargraph.skills.graphsmith.nodes.build import Build as GraphBuild
from stargraph.skills.graphsmith.nodes.record import RecordBuild as GraphRecord
from stargraph.skills.graphsmith.seeds import (
    _NORMALIZE_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _NORMALIZE_NODES,  # pyright: ignore[reportPrivateUsage]
    _NORMALIZE_STATE,  # pyright: ignore[reportPrivateUsage]
    _NORMALIZE_TEST,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.graphsmith.state import State as GraphState

if TYPE_CHECKING:
    from stargraph.skills.foundry.manifest import ManifestItem as _Item

pytestmark = pytest.mark.integration

# The spine the stub planner asks for: graphsmith's proven-runnable seed bundle
# (normalize → classify). graphsmith's own suite guarantees this runs to done.
GOOD_GEN: dict[str, Any] = {
    "graph_id": "alert-normalizer",
    "node_classes": ["Normalize", "Classify"],
    "state_source": _NORMALIZE_STATE,
    "nodes_source": _NORMALIZE_NODES,
    "test_source": _NORMALIZE_TEST,
    "fixture": _NORMALIZE_FIXTURE,
}


def _planner(request: str) -> BuildManifest:
    """Stub planner: one graph spine whose brief is the request."""
    return BuildManifest(items=[ManifestItem(kind="graph", name="alert-normalizer", brief=request)])


async def _graph_executor(item: _Item, *, output_dir: str, model_id: str) -> dict[str, Any]:
    """Stub executor: drive the REAL graphsmith build (generator stubbed) + record."""
    state = GraphState(brief=item.brief, model_id=model_id, output_dir=output_dir)
    for node in (stub_build(GraphBuild, GOOD_GEN), GraphRecord()):
        out = await node.execute(state, CTX)
        state = state.model_copy(update=out)
    return {
        "kind": item.kind,
        "name": item.name,
        "landed_path": state.landed_path,
        "out_dir": output_dir,
        "ok": bool(state.landed_path),
        "fixture": dict(state.fixture),
    }


async def _drive(nodes: list[Any], state: State) -> State:
    for node in nodes:
        out = await node.execute(state, CTX)
        state = state.model_copy(update=out)
    return state


# --------------------------------------------------------------------------- #
# End-to-end: request → manifest → build → assemble → a graph that really runs
# --------------------------------------------------------------------------- #
async def test_foundry_builds_and_runs_a_spine(tmp_path: Path) -> None:
    state = State(
        request="normalize then classify an incoming alert",
        model_id="stub-model",
        output_dir=str(tmp_path / "out"),
    )
    final = await _drive(
        [Plan(planner=_planner), Execute(executor=_graph_executor), Assemble(), Verify()],
        state,
    )

    assert final.run_status == "done", final.verify_detail
    assert final.verified is True

    assembled = Path(final.assembled_dir)
    assert Path(final.graph_path) == assembled / "graph.yaml"
    for name in ("graph.yaml", "state.py", "nodes.py", "assembly.yaml"):
        assert (assembled / name).is_file(), f"missing {name} in assembled dir"


async def test_built_records_carry_the_landed_artifact(tmp_path: Path) -> None:
    state = State(request="x normalize classify", output_dir=str(tmp_path / "out"))
    after_exec = await _drive([Plan(planner=_planner), Execute(executor=_graph_executor)], state)
    assert len(after_exec.built) == 1
    record = after_exec.built[0]
    assert record["kind"] == "graph"
    assert record["ok"] is True
    assert record["landed_path"].endswith("graph.yaml")


# --------------------------------------------------------------------------- #
# The foundry is itself a valid, runnable Stargraph (its graph.yaml loads + wires)
# --------------------------------------------------------------------------- #
def test_foundry_graph_ir_is_a_valid_runnable_stargraph() -> None:
    import yaml

    import stargraph.skills.foundry as foundry_pkg
    from stargraph.cli.run import _build_node_registry  # pyright: ignore[reportPrivateUsage]
    from stargraph.graph import Graph
    from stargraph.ir import IRDocument

    graph_yaml = Path(foundry_pkg.__file__).with_name("graph.yaml")
    ir = IRDocument.model_validate(yaml.safe_load(graph_yaml.read_text(encoding="utf-8")))
    assert [n.id for n in ir.nodes] == ["plan", "execute", "assemble", "verify"]

    graph = Graph(ir)  # resolves state_class; raises if wiring is bad
    initial = graph.state_schema(request="build me a graph")
    assert initial.request == "build me a graph"  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]

    registry = _build_node_registry(ir.nodes, ir_dir=graph_yaml.parent)  # all kinds → NodeBase
    assert registry


# --------------------------------------------------------------------------- #
# Manifest schema (the planner ↔ executor contract)
# --------------------------------------------------------------------------- #
def test_manifest_requires_exactly_one_spine() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        BuildManifest(items=[ManifestItem(kind="store", name="s", brief="a store")])
    with pytest.raises(ValidationError, match="exactly one"):
        BuildManifest(
            items=[
                ManifestItem(kind="graph", name="a", brief="spine one"),
                ManifestItem(kind="graph", name="b", brief="spine two"),
            ]
        )


def test_manifest_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError, match="unknown build kind"):
        ManifestItem(kind="bogus", name="x", brief="y")


def test_coerce_parses_a_json_array_with_capabilities() -> None:
    manifest = coerce(
        '[{"kind":"graph","name":"g","brief":"do the thing"},'
        '{"kind":"pack","name":"p","brief":"govern the thing"}]'
    )
    assert manifest.spine.name == "g"
    assert [c.kind for c in manifest.capabilities] == ["pack"]


async def test_plan_rejects_empty_request() -> None:
    with pytest.raises(ValueError, match="request is required"):
        await Plan(planner=_planner).execute(State(request="   "), CTX)
