# SPDX-License-Identifier: Apache-2.0
"""Build — the plugin smith's binding of the shared generate → gate → repair loop.

The loop itself is domain-agnostic (:class:`stargraph.skills._smith.build.SmithBuild`);
this module supplies the plugin specifics via :data:`PLUGIN_SPEC` — the full plug-in
the shared lifecycle nodes run against: how a generation dict becomes the two bundle
files (``plugin.py`` + ``test_plugin.py``), how those are gated (register the module
on an isolated pluggy ``PluginManager`` and drive its hooks + tool for real), which
fields surface as state, how grounding is recalled, and how a passing plugin is named
+ recorded. ``PluginProgram`` is constructed by name here so tests can monkeypatch it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.skills._smith.build import SmithBuild
from stargraph.skills._smith.spec import SmithSpec
from stargraph.skills.pluginsmith._ledger import append_lesson, append_trainset, recall_lessons
from stargraph.skills.pluginsmith.gate import PLUGIN_FILE, TEST_FILE, run_full_gate
from stargraph.skills.pluginsmith.program import PluginProgram
from stargraph.skills.pluginsmith.retrieval import retrieve_context

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from pydantic import BaseModel

    from stargraph.skills._smith.gate import VerifierResult


def _artifact_files(gen: dict[str, Any]) -> dict[str, str]:
    return {
        PLUGIN_FILE: str(gen.get("plugin_source", "")),
        TEST_FILE: str(gen.get("test_source", "")),
    }


def _gate(work: Path, files: dict[str, str], gen: dict[str, Any]) -> list[VerifierResult]:
    meta = {
        "tool_name": str(gen.get("tool_name", "")),
        "namespace": str(gen.get("namespace", "")),
        "tool_attr": str(gen.get("tool_attr", "")),
    }
    return run_full_gate(work, files, meta=meta, fixture=gen.get("fixture", {}))


def _summary_fields(gen: dict[str, Any]) -> dict[str, Any]:
    return {
        "plugin_name": gen.get("plugin_name", ""),
        "namespace": gen.get("namespace", ""),
        "tool_name": gen.get("tool_name", ""),
        "tool_attr": gen.get("tool_attr", ""),
        "fixture": gen.get("fixture", {}),
    }


def _landed_stem(state: BaseModel) -> str:
    return str(getattr(state, "plugin_name", "") or "") or "plugin"


def _trainset_fields(state: BaseModel) -> dict[str, Any]:
    files = getattr(state, "artifact_files", {}) or {}
    return {
        "plugin_name": str(getattr(state, "plugin_name", "") or ""),
        "namespace": str(getattr(state, "namespace", "") or ""),
        "tool_name": str(getattr(state, "tool_name", "") or ""),
        "tool_attr": str(getattr(state, "tool_attr", "") or ""),
        "fixture": dict(getattr(state, "fixture", {})),
        "plugin_source": files.get(PLUGIN_FILE, ""),
        "test_source": files.get(TEST_FILE, ""),
    }


PLUGIN_SPEC = SmithSpec(
    name="plugin",
    artifact_filenames=(PLUGIN_FILE, TEST_FILE),
    artifact_files=_artifact_files,
    gate=_gate,
    summary_fields=_summary_fields,
    recall_lessons=recall_lessons,
    retrieve_context=retrieve_context,
    landed_stem=_landed_stem,
    trainset_fields=_trainset_fields,
    append_lesson=append_lesson,
    append_trainset=append_trainset,
    # The test imports the module by the bare name ``plugin``, so land the two files
    # with their fixed names under output_dir/<stem>/; plugin.py is the entry point.
    bundle_files=(PLUGIN_FILE, TEST_FILE),
    entry_file=PLUGIN_FILE,
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
            program=PluginProgram(),
            spec=PLUGIN_SPEC,
            max_attempts=max_attempts,
            work_dir=work_dir,
            on_progress=on_progress,
        )
