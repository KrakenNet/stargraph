# SPDX-License-Identifier: Apache-2.0
"""SmithBuild - the bounded generate -> gate -> repair loop (the "always works" core).

Domain-agnostic: every smith runs this same loop, differing only through its
:class:`stargraph.skills._smith.spec.SmithSpec`. Runs entirely inside one node so
the loop closes under plain ``stargraph run`` (linear routing, no rule engine).
Each failed attempt records a reflexion lesson and feeds the verifier findings
into the next generation. ``_program`` is the seam tests stub for determinism.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import NodeBase
from stargraph.skills._smith.gate import all_passed

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from stargraph.nodes.base import ExecutionContext
    from stargraph.skills._smith.program import SmithProgram
    from stargraph.skills._smith.spec import SmithSpec


class SmithBuild(NodeBase):
    """The shared build node. A smith subclass constructs its ``program`` (reading
    its own module global so tests can monkeypatch it) and binds its ``spec``.
    """

    def __init__(
        self,
        *,
        program: SmithProgram,
        spec: SmithSpec,
        max_attempts: int = 3,
        work_dir: Path | None = None,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._program = program
        self._spec = spec
        self._max_attempts = max_attempts
        self._work_dir_override = work_dir
        # Optional progress sink (e.g. a TUI log); called per attempt. Default
        # no-op so headless callers and tests are unaffected.
        self._on_progress = on_progress

    def _progress(self, msg: str) -> None:
        if self._on_progress is not None:
            self._on_progress(msg)

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        brief = str(getattr(state, "brief", "") or "")
        lessons = list(getattr(state, "recalled_lessons", []))
        context = str(getattr(state, "recalled_context", "") or "")
        # Fresh, private scratch dir per run (mode 0700) - avoids the symlink race
        # and stale-artifact accumulation of a predictable /tmp path. Cleaned in
        # finally; an injected override is the caller's to manage.
        override = self._work_dir_override
        work = override or Path(tempfile.mkdtemp(prefix=f"{self._spec.name}smith-"))
        try:
            return await self._build(brief, lessons, context, work)
        finally:
            if override is None:
                shutil.rmtree(work, ignore_errors=True)

    async def _build(
        self, brief: str, lessons: list[str], context: str, work: Path
    ) -> dict[str, Any]:
        last_findings: list[dict[str, Any]] = []
        results: list[Any] = []
        gen: dict[str, Any] = {}
        files: dict[str, str] = {}
        attempt = 0

        for attempt in range(1, self._max_attempts + 1):
            self._progress(f"attempt {attempt}/{self._max_attempts}: generating…")
            gen = self._program.generate(brief, lessons, last_findings, context)
            files = self._spec.artifact_files(gen)
            self._progress(f"attempt {attempt}: gating (static → contract → tests)…")
            results = self._spec.gate(work, files, gen)
            if all_passed(results):
                self._progress(f"attempt {attempt}: gate passed ✓")
                break

            failed = [r for r in results if not r.passed]
            last_findings = [f for r in failed for f in r.findings]
            first = failed[0]
            msg = "; ".join(str(f.get("msg", "")) for f in first.findings) or f"{first.kind} failed"
            self._progress(f"attempt {attempt}: {first.kind} failed — repairing")
            self._spec.append_lesson(
                brief=brief, failed_kind=first.kind, finding=msg, attempts=attempt
            )

        return {
            **self._spec.summary_fields(gen),
            "artifact_files": files,
            "verifier_results": results,
            "fix_attempts": attempt,
            "succeeded": all_passed(results),
        }
