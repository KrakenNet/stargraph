# SPDX-License-Identifier: Apache-2.0
"""Build - the node smith's binding of the shared generate -> gate -> repair loop.

The loop itself is domain-agnostic (:class:`stargraph.skills._smith.build.SmithBuild`);
this module supplies the node specifics via :data:`NODE_SPEC` - the full plug-in
the shared lifecycle nodes (triage → recall → build → record) run against: how a
generation dict becomes ``node.py`` + ``test_node.py``, how those are gated (the
node contract), which fields surface as state, how grounding is recalled, and how
a passing node is named + recorded. ``NodeProgram`` is constructed by name here
so tests can monkeypatch it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.skills._smith.build import SmithBuild
from stargraph.skills._smith.spec import SmithSpec
from stargraph.skills.nodesmith._ledger import append_lesson, append_trainset, recall_lessons
from stargraph.skills.nodesmith.gate import NODE_FILE, TEST_FILE, run_full_gate
from stargraph.skills.nodesmith.program import NodeProgram
from stargraph.skills.nodesmith.retrieval import retrieve_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult


def _artifact_files(gen: dict[str, Any]) -> dict[str, str]:
    return {
        NODE_FILE: str(gen.get("node_source", "")),
        TEST_FILE: str(gen.get("test_source", "")),
    }


def _gate(work: Path, files: dict[str, str], gen: dict[str, Any]) -> list[VerifierResult]:
    return run_full_gate(
        work,
        files,
        reads=gen.get("reads", []),
        writes=gen.get("writes", []),
        fixture=gen.get("fixture", {}),
    )


def _summary_fields(gen: dict[str, Any]) -> dict[str, Any]:
    return {
        "class_name": gen.get("class_name", ""),
        "reads": gen.get("reads", []),
        "writes": gen.get("writes", []),
        "fixture": gen.get("fixture", {}),
    }


def _landed_stem(state: BaseModel) -> str:
    return str(getattr(state, "class_name", "") or "") or "node"


def _trainset_fields(state: BaseModel) -> dict[str, Any]:
    files = getattr(state, "artifact_files", {}) or {}
    class_name = str(getattr(state, "class_name", "") or "")
    return {
        "node_name": getattr(state, "node_name", "") or class_name,
        "class_name": class_name,
        "reads": list(getattr(state, "reads", [])),
        "writes": list(getattr(state, "writes", [])),
        "fixture": dict(getattr(state, "fixture", {})),
        "node_source": files.get(NODE_FILE, ""),
        "test_source": files.get(TEST_FILE, ""),
    }


NODE_SPEC = SmithSpec(
    name="node",
    artifact_filenames=(NODE_FILE, TEST_FILE),
    artifact_files=_artifact_files,
    gate=_gate,
    summary_fields=_summary_fields,
    recall_lessons=recall_lessons,
    retrieve_context=retrieve_context,
    landed_stem=_landed_stem,
    trainset_fields=_trainset_fields,
    append_lesson=append_lesson,
    append_trainset=append_trainset,
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
            program=NodeProgram(),
            spec=NODE_SPEC,
            max_attempts=max_attempts,
            work_dir=work_dir,
            on_progress=on_progress,
        )
