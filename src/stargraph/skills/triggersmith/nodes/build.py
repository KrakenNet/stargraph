# SPDX-License-Identifier: Apache-2.0
"""Build - the trigger smith's binding of the generate → gate → repair loop.

The loop itself is domain-agnostic (:class:`stargraph.skills._smith.build.SmithBuild`);
this module supplies the trigger specifics via :data:`TRIGGER_SPEC` - the full
plug-in the shared lifecycle nodes (triage → recall → build → record) run
against: how a generation dict becomes ``trigger.py`` + ``test_trigger.py``, how
those are gated (the trigger contract), which fields surface as state, how
grounding is recalled, and how a passing trigger is named + recorded.
``TriggerProgram`` is constructed by name here so tests can monkeypatch it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.skills._smith.build import SmithBuild
from stargraph.skills._smith.spec import SmithSpec
from stargraph.skills.triggersmith._ledger import append_lesson, append_trainset, recall_lessons
from stargraph.skills.triggersmith.gate import TEST_FILE, TRIGGER_FILE, run_full_gate
from stargraph.skills.triggersmith.program import TriggerProgram
from stargraph.skills.triggersmith.retrieval import retrieve_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult


def _artifact_files(gen: dict[str, Any]) -> dict[str, str]:
    return {
        TRIGGER_FILE: str(gen.get("trigger_source", "")),
        TEST_FILE: str(gen.get("test_source", "")),
    }


def _gate(work: Path, files: dict[str, str], gen: dict[str, Any]) -> list[VerifierResult]:
    return run_full_gate(work, files, fixture=gen.get("fixture", {}))


def _summary_fields(gen: dict[str, Any]) -> dict[str, Any]:
    return {
        "variant": "manual",
        "class_name": gen.get("class_name", ""),
        "fixture": gen.get("fixture", {}),
    }


def _landed_stem(state: BaseModel) -> str:
    return str(getattr(state, "class_name", "") or "") or "trigger"


def _trainset_fields(state: BaseModel) -> dict[str, Any]:
    files = getattr(state, "artifact_files", {}) or {}
    return {
        "variant": str(getattr(state, "variant", "manual") or "manual"),
        "class_name": str(getattr(state, "class_name", "") or ""),
        "fixture": dict(getattr(state, "fixture", {})),
        "trigger_source": files.get(TRIGGER_FILE, ""),
        "test_source": files.get(TEST_FILE, ""),
    }


TRIGGER_SPEC = SmithSpec(
    name="trigger",
    artifact_filenames=(TRIGGER_FILE, TEST_FILE),
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
            program=TriggerProgram(),
            spec=TRIGGER_SPEC,
            max_attempts=max_attempts,
            work_dir=work_dir,
            on_progress=on_progress,
        )
