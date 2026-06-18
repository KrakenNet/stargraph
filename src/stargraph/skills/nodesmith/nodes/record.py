# SPDX-License-Identifier: Apache-2.0
"""Record — terminal node. On success: log the (spec → node) pair + land the
files. On failure: log a summary reflexion lesson. The trainset append is
gated on the actual verifier results, so a build that did not pass can never
be logged as a training pair.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills.nodesmith import _ledger
from stargraph.skills.nodesmith.gate import NODE_FILE, TEST_FILE, all_passed

if TYPE_CHECKING:
    from pydantic import BaseModel


def _snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name or "node").lower()
    return re.sub(r"[^a-z0-9_]", "_", s) or "node"


def _land(state: BaseModel, files: dict[str, str], class_name: str) -> str:
    output_dir = str(getattr(state, "output_dir", "") or "")
    if not output_dir:
        return ""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = _snake(class_name)
    node_path = out / f"{stem}.py"
    node_path.write_text(files.get(NODE_FILE, ""), encoding="utf-8")
    (out / f"test_{stem}.py").write_text(files.get(TEST_FILE, ""), encoding="utf-8")
    return str(node_path)


class RecordBuild(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        results = list(getattr(state, "verifier_results", []))
        files = getattr(state, "artifact_files", {}) or {}
        class_name = str(getattr(state, "class_name", "") or "")
        attempts = int(getattr(state, "fix_attempts", 0)) or 1
        brief = str(getattr(state, "brief", "") or "")

        if not all_passed(results):
            failed = [r for r in results if not r.passed]
            summary = "; ".join(
                f"{r.kind}: {(r.findings[0].get('msg', '') if r.findings else '')[:120]}"
                for r in failed
            )
            _ledger.append_lesson(
                brief=brief,
                failed_kind="escalate",
                finding=f"unrepaired after {attempts} attempts: {summary}",
                attempts=attempts,
            )
            return {"landed_path": ""}

        _ledger.append_trainset(
            {
                "brief": brief,
                "node_name": getattr(state, "node_name", "") or class_name,
                "class_name": class_name,
                "reads": list(getattr(state, "reads", [])),
                "writes": list(getattr(state, "writes", [])),
                "fixture": dict(getattr(state, "fixture", {})),
                "node_source": files.get(NODE_FILE, ""),
                "test_source": files.get(TEST_FILE, ""),
                "model_id": getattr(state, "model_id", ""),
                "attempts": attempts,
                "passed": True,
            }
        )
        return {"landed_path": _land(state, files, class_name)}
