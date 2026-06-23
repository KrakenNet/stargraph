# SPDX-License-Identifier: Apache-2.0
"""Build — the graph smith's binding of the shared generate → gate → repair loop.

The loop itself is domain-agnostic (:class:`stargraph.skills._smith.build.SmithBuild`);
this module supplies the graph specifics via :data:`GRAPH_SPEC` — the full plug-in
the shared lifecycle nodes (triage → recall → build → record) run against: how a
generation dict becomes the four bundle files (``state.py`` + ``nodes.py`` + an
auto-wired ``graph.yaml`` + ``test_nodes.py``), how those are gated (load the graph
and RUN it), which fields surface as state, how grounding is recalled, and how a
passing bundle is named + recorded. ``GraphProgram`` is constructed by name here so
tests can monkeypatch it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.skills._smith.build import SmithBuild
from stargraph.skills._smith.spec import SmithSpec
from stargraph.skills.graphsmith._ledger import append_lesson, append_trainset, recall_lessons
from stargraph.skills.graphsmith.gate import (
    GRAPH_FILE,
    NODES_FILE,
    STATE_FILE,
    TEST_FILE,
    assemble_graph_yaml,
    run_full_gate,
)
from stargraph.skills.graphsmith.program import GraphProgram
from stargraph.skills.graphsmith.retrieval import retrieve_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult


def _artifact_files(gen: dict[str, Any]) -> dict[str, str]:
    graph_id = str(gen.get("graph_id", ""))
    node_classes = [str(c) for c in gen.get("node_classes", [])]
    return {
        STATE_FILE: str(gen.get("state_source", "")),
        NODES_FILE: str(gen.get("nodes_source", "")),
        GRAPH_FILE: assemble_graph_yaml(graph_id, node_classes),
        TEST_FILE: str(gen.get("test_source", "")),
    }


def _gate(work: Path, files: dict[str, str], gen: dict[str, Any]) -> list[VerifierResult]:
    return run_full_gate(work, files, fixture=gen.get("fixture", {}))


def _summary_fields(gen: dict[str, Any]) -> dict[str, Any]:
    return {
        "graph_id": gen.get("graph_id", ""),
        "node_classes": [str(c) for c in gen.get("node_classes", [])],
        "fixture": gen.get("fixture", {}),
    }


def _landed_stem(state: BaseModel) -> str:
    return str(getattr(state, "graph_id", "") or "") or "graph"


def _trainset_fields(state: BaseModel) -> dict[str, Any]:
    # graph.yaml is auto-assembled from graph_id + node_classes, so the row stores
    # those (it regenerates the wiring) rather than the assembled yaml itself.
    files = getattr(state, "artifact_files", {}) or {}
    return {
        "graph_id": str(getattr(state, "graph_id", "") or ""),
        "node_classes": list(getattr(state, "node_classes", [])),
        "fixture": dict(getattr(state, "fixture", {})),
        "state_source": files.get(STATE_FILE, ""),
        "nodes_source": files.get(NODES_FILE, ""),
        "test_source": files.get(TEST_FILE, ""),
    }


GRAPH_SPEC = SmithSpec(
    name="graph",
    # ``bundle_files`` drives the shared ``SmithRecord._land`` to land all four under
    # output_dir/<stem>/; ``artifact_filenames`` are just the static/test anchors.
    artifact_filenames=(STATE_FILE, TEST_FILE),
    artifact_files=_artifact_files,
    gate=_gate,
    summary_fields=_summary_fields,
    recall_lessons=recall_lessons,
    retrieve_context=retrieve_context,
    landed_stem=_landed_stem,
    trainset_fields=_trainset_fields,
    append_lesson=append_lesson,
    append_trainset=append_trainset,
    bundle_files=(STATE_FILE, NODES_FILE, GRAPH_FILE, TEST_FILE),
    entry_file=GRAPH_FILE,  # the runnable graph IR is the bundle's entry point
)


class Build(SmithBuild):
    def __init__(
        self,
        *,
        max_attempts: int = 3,
        work_dir: Path | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(
            program=GraphProgram(),
            spec=GRAPH_SPEC,
            max_attempts=max_attempts,
            work_dir=work_dir,
            on_progress=on_progress,
        )
