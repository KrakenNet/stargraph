# SPDX-License-Identifier: Apache-2.0
"""Build — the bounded generate→gate→repair loop (the "always works" core).

Runs entirely inside one node so the loop closes under plain ``stargraph run``
(which routes linearly, no rule engine required). Each failed attempt records a
reflexion lesson and feeds the verifier findings into the next generation.
``_program`` is the seam tests stub for determinism.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills.nodesmith._ledger import append_lesson
from stargraph.skills.nodesmith.gate import NODE_FILE, TEST_FILE, all_passed, run_full_gate
from stargraph.skills.nodesmith.program import NodeProgram

if TYPE_CHECKING:
    from pydantic import BaseModel


class Build(NodeBase):
    def __init__(self, *, max_attempts: int = 3, work_dir: Path | None = None) -> None:
        self._program = NodeProgram()
        self._max_attempts = max_attempts
        self._work_dir_override = work_dir

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        brief = str(getattr(state, "brief", "") or "")
        lessons = list(getattr(state, "recalled_lessons", []))
        # Fresh, private scratch dir per run (mode 0700) — avoids the symlink race
        # and stale-artifact accumulation of a predictable /tmp path. Cleaned in
        # finally; an injected override is the caller's to manage.
        override = self._work_dir_override
        work = override or Path(tempfile.mkdtemp(prefix="nodesmith-"))
        try:
            return await self._build(brief, lessons, work)
        finally:
            if override is None:
                shutil.rmtree(work, ignore_errors=True)

    async def _build(
        self, brief: str, lessons: list[str], work: Path
    ) -> dict[str, Any]:
        last_findings: list[dict[str, Any]] = []
        results: list[Any] = []
        gen: dict[str, Any] = {}
        attempt = 0

        for attempt in range(1, self._max_attempts + 1):
            gen = self._program.generate(brief, lessons, last_findings)
            files = {NODE_FILE: gen["node_source"], TEST_FILE: gen["test_source"]}
            results = run_full_gate(
                work, files, reads=gen["reads"], writes=gen["writes"], fixture=gen["fixture"]
            )
            if all_passed(results):
                break

            failed = [r for r in results if not r.passed]
            last_findings = [f for r in failed for f in r.findings]
            first = failed[0]
            msg = "; ".join(str(f.get("msg", "")) for f in first.findings) or f"{first.kind} failed"
            append_lesson(brief=brief, failed_kind=first.kind, finding=msg, attempts=attempt)

        return {
            "class_name": gen.get("class_name", ""),
            "reads": gen.get("reads", []),
            "writes": gen.get("writes", []),
            "fixture": gen.get("fixture", {}),
            "artifact_files": {
                NODE_FILE: gen.get("node_source", ""),
                TEST_FILE: gen.get("test_source", ""),
            },
            "verifier_results": results,
            "fix_attempts": attempt,
            "succeeded": all_passed(results),
        }
