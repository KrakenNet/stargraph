# SPDX-License-Identifier: Apache-2.0
"""Shared lifecycle nodes: triage → recall → record (build is SmithBuild).

These three nodes are identical for every smith — they read/write only the
generic :class:`stargraph.skills._smith.state.SmithState` spine and route all
domain decisions through the smith's :class:`SmithSpec`. A smith subclasses each
with a no-arg ``__init__`` that binds its spec (so the graph loader can construct
them by name), and ships no per-domain node logic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills._smith import web
from stargraph.skills._smith.gate import all_passed
from stargraph.skills._smith.retrieval import format_context

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.skills._smith.spec import SmithSpec

__all__ = ["SmithRecall", "SmithRecord", "SmithTriage", "snake"]


def snake(name: str, *, default: str = "artifact") -> str:
    """PascalCase/anything → snake_case, safe as a python module stem."""
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name or default).lower()
    return re.sub(r"[^a-z0-9_]", "_", s) or default


class SmithTriage(NodeBase):
    """Reject empty briefs before spending an LLM call."""

    def __init__(self, *, spec: SmithSpec) -> None:
        self._spec = spec

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        brief = getattr(state, "brief", None)
        if not brief or not str(brief).strip():
            raise ValueError(f"brief is required: describe the {self._spec.name} to build")
        return {}


class SmithRecall(NodeBase):
    """Gather grounding before the build: reflexion lessons + RAG + model-decided web.

    All three sources are best-effort. The web step calls the LM, so this node
    must run inside the configured ``dspy`` scope.
    """

    def __init__(self, *, spec: SmithSpec) -> None:
        self._spec = spec

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        brief = str(getattr(state, "brief", "") or "")
        lessons = self._spec.recall_lessons(brief, limit=3)
        snippets = self._spec.retrieve_context(brief, k=4)
        snippets += web.research(brief)  # model-decided, best-effort (→ [] if not needed)
        return {
            "recalled_lessons": lessons,
            "recalled_context": format_context(snippets),
        }


class SmithRecord(NodeBase):
    """Terminal node. On success: log the (spec → artifact) pair + land the files.
    On failure: log a summary reflexion lesson. The trainset append is gated on
    the actual verifier results, so a build that did not pass is never recorded.
    """

    def __init__(self, *, spec: SmithSpec) -> None:
        self._spec = spec

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        results = list(getattr(state, "verifier_results", []))
        attempts = int(getattr(state, "fix_attempts", 0)) or 1
        brief = str(getattr(state, "brief", "") or "")

        if not all_passed(results):
            failed = [r for r in results if not r.passed]
            summary = "; ".join(
                f"{r.kind}: {(r.findings[0].get('msg', '') if r.findings else '')[:120]}"
                for r in failed
            )
            self._spec.append_lesson(
                brief=brief,
                failed_kind="escalate",
                finding=f"unrepaired after {attempts} attempts: {summary}",
                attempts=attempts,
            )
            return {"landed_path": ""}

        self._spec.append_trainset(
            {
                **self._spec.trainset_fields(state),
                "brief": brief,
                "model_id": getattr(state, "model_id", ""),
                "attempts": attempts,
                "passed": True,
            }
        )
        return {"landed_path": self._land(state)}

    def _land(self, state: BaseModel) -> str:
        output_dir = str(getattr(state, "output_dir", "") or "")
        if not output_dir:
            return ""
        files = getattr(state, "artifact_files", {}) or {}
        stem = snake(self._spec.landed_stem(state))

        # A composite artifact is a multi-file bundle: write the declared files
        # verbatim into output_dir/<stem>/ and return the entry point's path.
        if self._spec.bundle_files:
            out = Path(output_dir) / stem
            out.mkdir(parents=True, exist_ok=True)
            for name in self._spec.bundle_files:
                (out / name).write_text(files.get(name, ""), encoding="utf-8")
            entry = self._spec.entry_file or self._spec.bundle_files[0]
            return str(out / entry)

        # Leaf artifact: the flat two-file landing (<stem>.py + test_<stem>.py).
        source_file, test_file = self._spec.artifact_filenames
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        source_path = out / f"{stem}.py"
        source_path.write_text(files.get(source_file, ""), encoding="utf-8")
        (out / f"test_{stem}.py").write_text(files.get(test_file, ""), encoding="utf-8")
        return str(source_path)
