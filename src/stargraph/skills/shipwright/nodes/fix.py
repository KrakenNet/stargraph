# SPDX-License-Identifier: Apache-2.0
"""FixLoop — bound retries, route to synthesize_graph or escalate to human_input.

Plan-2 introduces `landing_summary` as the success-path next-node; in
Plan-1 we name it explicitly here so the engine has a clear handoff
even though the landing nodes don't exist yet (they're stubbed in
stargraph.yaml topology).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills.shipwright._pack import fresh_engine, load_pack

if TYPE_CHECKING:
    from pydantic import BaseModel

_MAX_ATTEMPTS = 3


class FixLoop(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        verifier_results = list(getattr(state, "verifier_results", []))
        fix_attempts = int(getattr(state, "fix_attempts", 0))

        latest = {r.kind: r for r in verifier_results}
        all_pass = bool(latest) and all(r.passed for r in latest.values())
        if all_pass:
            return {"next_node": "landing_summary"}

        failed = next(
            (r for r in reversed(verifier_results) if not r.passed),
            None,
        )
        if failed is None:
            return {"next_node": "verify_static"}

        eng = fresh_engine()
        load_pack(eng, "edits")
        eng._env.assert_string(f'(verify.failed (kind "{failed.kind}"))')  # pyright: ignore[reportPrivateUsage]
        eng._env.assert_string(f"(fix.attempts (value {fix_attempts}))")  # pyright: ignore[reportPrivateUsage]
        eng._env.run()  # pyright: ignore[reportPrivateUsage]

        targets = {
            str(dict(raw)["node"])
            for raw in eng._env.find_template("fix.target").facts()  # pyright: ignore[reportPrivateUsage]
        }
        if not targets:
            return {"next_node": "verify_static"}

        next_node = "human_input" if "human_input" in targets else next(iter(targets))
        return {
            "fix_attempts": min(fix_attempts + 1, _MAX_ATTEMPTS),
            "next_node": next_node,
        }
