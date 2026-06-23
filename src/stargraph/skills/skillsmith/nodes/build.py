# SPDX-License-Identifier: Apache-2.0
"""Build — the skill smith's binding of the shared generate → gate → repair loop.

The loop itself is domain-agnostic (:class:`stargraph.skills._smith.build.SmithBuild`);
this module supplies the skill specifics via :data:`SKILL_SPEC` — the full plug-in
the shared lifecycle nodes run against: how a generation dict becomes the five
bundle files (``state.py`` + ``nodes.py`` + an auto-wired ``graph.yaml`` + an
auto-wired ``manifest.yaml`` + ``test_nodes.py``), how those are gated (run the
subgraph + construct the Skill), which fields surface as state, how grounding is
recalled, and how a passing skill is named + recorded. ``SkillProgram`` is
constructed by name here so tests can monkeypatch it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.skills._smith.build import SmithBuild
from stargraph.skills._smith.spec import SmithSpec
from stargraph.skills.skillsmith._ledger import append_lesson, append_trainset, recall_lessons
from stargraph.skills.skillsmith.gate import (
    GRAPH_FILE,
    MANIFEST_FILE,
    NODES_FILE,
    STATE_FILE,
    TEST_FILE,
    assemble_graph_yaml,
    assemble_manifest_yaml,
    run_full_gate,
)
from stargraph.skills.skillsmith.program import SkillProgram
from stargraph.skills.skillsmith.retrieval import retrieve_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult


def _artifact_files(gen: dict[str, Any]) -> dict[str, str]:
    skill_name = str(gen.get("skill_name", ""))
    node_classes = [str(c) for c in gen.get("node_classes", [])]
    return {
        STATE_FILE: str(gen.get("state_source", "")),
        NODES_FILE: str(gen.get("nodes_source", "")),
        GRAPH_FILE: assemble_graph_yaml(skill_name, node_classes),
        MANIFEST_FILE: assemble_manifest_yaml(
            skill_name,
            str(gen.get("kind", "")),
            str(gen.get("description", "")),
            [str(c) for c in gen.get("requires", [])],
            str(gen.get("system_prompt", "")),
        ),
        TEST_FILE: str(gen.get("test_source", "")),
    }


def _gate(work: Path, files: dict[str, str], gen: dict[str, Any]) -> list[VerifierResult]:
    return run_full_gate(work, files, fixture=gen.get("fixture", {}))


def _summary_fields(gen: dict[str, Any]) -> dict[str, Any]:
    return {
        "skill_name": gen.get("skill_name", ""),
        "kind": gen.get("kind", ""),
        "description": gen.get("description", ""),
        "node_classes": [str(c) for c in gen.get("node_classes", [])],
        "requires": [str(c) for c in gen.get("requires", [])],
        "system_prompt": gen.get("system_prompt", ""),
        "fixture": gen.get("fixture", {}),
    }


def _landed_stem(state: BaseModel) -> str:
    return str(getattr(state, "skill_name", "") or "") or "skill"


def _trainset_fields(state: BaseModel) -> dict[str, Any]:
    # graph.yaml + manifest.yaml are auto-assembled from these fields, so the row
    # stores the fields (which regenerate the wiring), not the assembled files.
    files = getattr(state, "artifact_files", {}) or {}
    return {
        "skill_name": str(getattr(state, "skill_name", "") or ""),
        "kind": str(getattr(state, "kind", "") or ""),
        "description": str(getattr(state, "description", "") or ""),
        "node_classes": list(getattr(state, "node_classes", [])),
        "requires": list(getattr(state, "requires", [])),
        "system_prompt": str(getattr(state, "system_prompt", "") or ""),
        "fixture": dict(getattr(state, "fixture", {})),
        "state_source": files.get(STATE_FILE, ""),
        "nodes_source": files.get(NODES_FILE, ""),
        "test_source": files.get(TEST_FILE, ""),
    }


SKILL_SPEC = SmithSpec(
    name="skill",
    # ``bundle_files`` drives the shared ``SmithRecord._land`` to land all five under
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
    bundle_files=(STATE_FILE, NODES_FILE, GRAPH_FILE, MANIFEST_FILE, TEST_FILE),
    entry_file=MANIFEST_FILE,  # the skill's registration manifest is the entry point
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
            program=SkillProgram(),
            spec=SKILL_SPEC,
            max_attempts=max_attempts,
            work_dir=work_dir,
            on_progress=on_progress,
        )
