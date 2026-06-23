# SPDX-License-Identifier: Apache-2.0
"""Build — the pack smith's binding of the shared generate → gate → repair loop.

The loop itself is domain-agnostic (:class:`stargraph.skills._smith.build.SmithBuild`);
this module supplies the rule-pack specifics via :data:`PACK_SPEC` — the full plug-in
the shared lifecycle nodes run against: how a generation dict becomes the four bundle
files (``rules.clp`` from the model + the deterministically-assembled ``pack.yaml`` +
``manifest.yaml`` + the model's ``test_pack.py``), how those are gated (load the rules
into a Fathom engine → fire → match the action → sign + verify the tree), which fields
surface as state, how grounding is recalled, and how a passing pack is named + recorded.
``PackProgram`` is constructed by name here so tests can monkeypatch it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.skills._smith.build import SmithBuild
from stargraph.skills._smith.spec import SmithSpec
from stargraph.skills.packsmith._ledger import append_lesson, append_trainset, recall_lessons
from stargraph.skills.packsmith.gate import (
    MANIFEST_FILE,
    PACK_FILE,
    RULES_FILE,
    TEST_FILE,
    assemble_manifest_yaml,
    assemble_pack_yaml,
    run_full_gate,
)
from stargraph.skills.packsmith.program import PackProgram
from stargraph.skills.packsmith.retrieval import retrieve_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult


def _artifact_files(gen: dict[str, Any]) -> dict[str, str]:
    pack_name = str(gen.get("pack_name", "") or "pack")
    flavor = str(gen.get("flavor", "") or "governance")
    output_template = str(gen.get("output_template", ""))
    return {
        RULES_FILE: str(gen.get("rules_clp", "")),
        PACK_FILE: assemble_pack_yaml(pack_name=pack_name, flavor=flavor),
        MANIFEST_FILE: assemble_manifest_yaml(pack_name=pack_name, output_template=output_template),
        TEST_FILE: str(gen.get("test_source", "")),
    }


def _gate(work: Path, files: dict[str, str], gen: dict[str, Any]) -> list[VerifierResult]:
    meta = {
        "input_template": str(gen.get("input_template", "")),
        "output_template": str(gen.get("output_template", "")),
        "pack_name": str(gen.get("pack_name", "") or "pack"),
    }
    return run_full_gate(work, files, meta=meta, fixture=gen.get("fixture", {}))


def _summary_fields(gen: dict[str, Any]) -> dict[str, Any]:
    return {
        "pack_name": gen.get("pack_name", ""),
        "flavor": gen.get("flavor", "") or "governance",
        "input_template": gen.get("input_template", ""),
        "output_template": gen.get("output_template", ""),
        "fixture": gen.get("fixture", {}),
    }


def _landed_stem(state: BaseModel) -> str:
    return str(getattr(state, "pack_name", "") or "") or "pack"


def _trainset_fields(state: BaseModel) -> dict[str, Any]:
    files = getattr(state, "artifact_files", {}) or {}
    return {
        "pack_name": str(getattr(state, "pack_name", "") or ""),
        "flavor": str(getattr(state, "flavor", "") or "governance"),
        "input_template": str(getattr(state, "input_template", "") or ""),
        "output_template": str(getattr(state, "output_template", "") or ""),
        "fixture": dict(getattr(state, "fixture", {})),
        "rules_clp": files.get(RULES_FILE, ""),
        "test_source": files.get(TEST_FILE, ""),
    }


PACK_SPEC = SmithSpec(
    name="pack",
    artifact_filenames=(RULES_FILE, TEST_FILE),
    artifact_files=_artifact_files,
    gate=_gate,
    summary_fields=_summary_fields,
    recall_lessons=recall_lessons,
    retrieve_context=retrieve_context,
    landed_stem=_landed_stem,
    trainset_fields=_trainset_fields,
    append_lesson=append_lesson,
    append_trainset=append_trainset,
    # A pack is a directory: land the four files verbatim under output_dir/<stem>/;
    # pack.yaml is the entry point a deployer points at.
    bundle_files=(RULES_FILE, PACK_FILE, MANIFEST_FILE, TEST_FILE),
    entry_file=PACK_FILE,
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
            program=PackProgram(),
            spec=PACK_SPEC,
            max_attempts=max_attempts,
            work_dir=work_dir,
            on_progress=on_progress,
        )
