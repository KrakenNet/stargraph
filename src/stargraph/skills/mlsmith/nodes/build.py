# SPDX-License-Identifier: Apache-2.0
"""Build — the ML smith's binding of the shared generate → gate → repair loop.

The loop itself is domain-agnostic (:class:`stargraph.skills._smith.build.SmithBuild`);
this module supplies the model-node specifics via :data:`ML_SPEC` — the full plug-in
the shared lifecycle nodes run against: how a generation dict becomes the two bundle
files (``trainer.py`` + ``test_trainer.py``), how those are gated (run the trainer →
construct a live MLNode against the produced model → run it on the fixture), which
fields surface as state, how grounding is recalled, and how a passing trainer is named
+ recorded. ``MLProgram`` is constructed by name here so tests can monkeypatch it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.skills._smith.build import SmithBuild
from stargraph.skills._smith.spec import SmithSpec
from stargraph.skills.mlsmith._ledger import append_lesson, append_trainset, recall_lessons
from stargraph.skills.mlsmith.gate import TEST_FILE, TRAINER_FILE, run_full_gate
from stargraph.skills.mlsmith.program import MLProgram
from stargraph.skills.mlsmith.retrieval import retrieve_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult


def _artifact_files(gen: dict[str, Any]) -> dict[str, str]:
    return {
        TRAINER_FILE: str(gen.get("trainer_source", "")),
        TEST_FILE: str(gen.get("test_source", "")),
    }


def _gate(work: Path, files: dict[str, str], gen: dict[str, Any]) -> list[VerifierResult]:
    meta = {
        "runtime": str(gen.get("runtime", "")),
        "input_field": str(gen.get("input_field", "") or "x"),
        "output_field": str(gen.get("output_field", "") or "y"),
    }
    return run_full_gate(work, files, meta=meta, fixture=gen.get("fixture", {}))


def _summary_fields(gen: dict[str, Any]) -> dict[str, Any]:
    return {
        "model_name": gen.get("model_name", ""),
        "runtime": gen.get("runtime", ""),
        "input_field": gen.get("input_field", "") or "x",
        "output_field": gen.get("output_field", "") or "y",
        "fixture": gen.get("fixture", {}),
    }


def _landed_stem(state: BaseModel) -> str:
    return str(getattr(state, "model_name", "") or "") or "model"


def _trainset_fields(state: BaseModel) -> dict[str, Any]:
    files = getattr(state, "artifact_files", {}) or {}
    return {
        "model_name": str(getattr(state, "model_name", "") or ""),
        "runtime": str(getattr(state, "runtime", "") or ""),
        "input_field": str(getattr(state, "input_field", "") or "x"),
        "output_field": str(getattr(state, "output_field", "") or "y"),
        "fixture": dict(getattr(state, "fixture", {})),
        "trainer_source": files.get(TRAINER_FILE, ""),
        "test_source": files.get(TEST_FILE, ""),
    }


ML_SPEC = SmithSpec(
    name="ml",
    artifact_filenames=(TRAINER_FILE, TEST_FILE),
    artifact_files=_artifact_files,
    gate=_gate,
    summary_fields=_summary_fields,
    recall_lessons=recall_lessons,
    retrieve_context=retrieve_context,
    landed_stem=_landed_stem,
    trainset_fields=_trainset_fields,
    append_lesson=append_lesson,
    append_trainset=append_trainset,
    # The test imports the module by the bare name ``trainer``, so land the two files
    # with their fixed names under output_dir/<stem>/; trainer.py is the entry point.
    bundle_files=(TRAINER_FILE, TEST_FILE),
    entry_file=TRAINER_FILE,
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
            program=MLProgram(),
            spec=ML_SPEC,
            max_attempts=max_attempts,
            work_dir=work_dir,
            on_progress=on_progress,
        )
