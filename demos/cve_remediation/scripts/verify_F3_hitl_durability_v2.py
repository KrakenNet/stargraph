# SPDX-License-Identifier: Apache-2.0
"""F3 v2: HITL durability via real harbor.checkpoint.postgres driver.

Closes CRITERIA fancy #3 gap-4: replaces the bespoke
``cve_rem_hitl_persistence`` shim with the canonical Harbor
:class:`PostgresCheckpointer`. Production ``harbor serve`` uses this
driver to checkpoint every step; an HITL gate at step ``N`` lands a
durable :class:`Checkpoint` row that survives any number of worker
restarts. The analyst response is then a fresh process reading
``read_latest(run_id)`` and resuming from the recorded ``next_action``.

Sequence:

1. Bootstrap the PostgresCheckpointer (idempotent schema migration).
2. Drive the cve-rem pipeline up through HitlChangeApprovalNode under
   ``ssvc_tier=act_hitl_required + CVE_REM_HITL_DECISION=block``.
3. **Write a real Checkpoint** capturing the blocked state's
   ``model_dump`` (JCS-serializable per the protocol).
4. Discard the in-memory state object (worker-restart simulation).
5. **read_latest(run_id)** returns the row; rehydrate
   :class:`CveRemState` from ``checkpoint.state``.
6. Patch in ``HitlResponse(decision=approve)``, clear the gate, drive
   ProgressiveExecuteNode + VerifyImmediateNode; assert rollout
   completes.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \
    uv run --no-project python -m demos.cve_remediation.scripts.verify_F3_hitl_durability_v2
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from harbor.checkpoint import Checkpoint
from harbor.checkpoint.postgres import PostgresCheckpointer

from demos.cve_remediation.graph.real_nodes import (
    AttachAllArtifactsNode,
    CanonicalizeTrustedNode,
    CodeWriterNode,
    CorrelateAssetsBrokerNode,
    CreateChangeRequestNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    HitlChangeApprovalNode,
    IntakeFetchNode,
    PlannerNode,
    ProgressiveExecuteNode,
    SandboxDispatchNode,
    SandboxRunNode,
    VerifyImmediateNode,
)
from demos.cve_remediation.graph.state import (
    CveRemState,
    HitlResponse,
    SsvcTier,
)


DEFAULT_CVE = os.environ.get("F3_CVE", "CVE-2024-26130")
GRAPH_HASH = "cve-rem-pipeline:v6"
RUNTIME_HASH = hashlib.sha256(b"harbor-runtime").hexdigest()


async def _drive_to_block(cve_id: str, run_id: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id, run_id=run_id)
    ctx = SimpleNamespace(run_id=run_id)
    pre = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        PlannerNode(),
        SandboxDispatchNode(),
        SandboxRunNode(),
        CodeWriterNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pre:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    state = state.model_copy(update={
        "ssvc_tier": SsvcTier.ACT_HITL_REQUIRED,
    })
    for node in (
        CreateChangeRequestNode(),
        AttachAllArtifactsNode(),
        HitlChangeApprovalNode(),
    ):
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


async def _resume(state: CveRemState) -> CveRemState:
    ctx = SimpleNamespace(run_id=state.run_id)
    state = state.model_copy(update={
        "hitl_blocked_at": "",
        "cr_status": "approved",
        "response": HitlResponse(
            decision="approve",
            actor="F3-v2-analyst",
            note="resumed via PostgresCheckpointer.read_latest",
            at=datetime.now(UTC),
        ),
    })
    for node in (
        ProgressiveExecuteNode(),
        VerifyImmediateNode(),
    ):
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


async def main() -> int:
    overall = True
    print("=== F3 v2 (HITL durability via harbor.checkpoint.postgres) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset; CR work-notes are dry-run.\n")
    dsn = os.environ.get("POSTGRES_DSN", "").strip()
    if not dsn:
        print("! POSTGRES_DSN unset; FAIL")
        return 1
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    os.environ["CVE_REM_SKIP_SEED"] = "1"
    os.environ["CVE_REM_HITL_DECISION"] = "block"

    cp = PostgresCheckpointer(dsn=dsn)
    print("--- Stage 1: bootstrap PostgresCheckpointer ---")
    await cp.bootstrap()
    print("  bootstrap done.")

    run_id = f"verify-F3v2-{datetime.now(UTC).timestamp():.0f}"

    # Stage 2: drive to gate.
    print(f"\n--- Stage 2: drive to HITL block (run_id={run_id}) ---")
    blocked = await _drive_to_block(DEFAULT_CVE, run_id)
    if not blocked.hitl_blocked_at:
        print("  ! gate did not fire")
        return 1
    print(f"  hitl_blocked_at  : {blocked.hitl_blocked_at!r}")
    print(f"  cr_status        : {blocked.cr_status!r}")

    # Stage 3: write a real Checkpoint.
    print("\n--- Stage 3: PostgresCheckpointer.write(blocked checkpoint) ---")
    snapshot = blocked.model_dump(mode="json")
    cp_row = Checkpoint(
        run_id=run_id,
        step=1,
        branch_id=None,
        parent_step_idx=None,
        graph_hash=GRAPH_HASH,
        runtime_hash=RUNTIME_HASH,
        state=snapshot,
        clips_facts=[],
        last_node="hitl_change_approval",
        next_action={
            "kind": "hitl-resume",
            "after_response": "progressive_execute",
        },
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash=hashlib.sha256(b"none").hexdigest(),
    )
    await cp.write(cp_row)
    print(f"  wrote step=1 last_node='hitl_change_approval'")

    # Stage 4: simulate worker kill.
    print("\n--- Stage 4: simulate worker kill (drop in-memory state) ---")
    blocked = None  # type: ignore[assignment]

    # Stage 5: fresh process -> read_latest -> rehydrate.
    print("\n--- Stage 5: read_latest + rehydrate ---")
    cp2 = PostgresCheckpointer(dsn=dsn)
    await cp2.bootstrap()
    latest = await cp2.read_latest(run_id)
    if latest is None:
        print("  ! read_latest returned None")
        overall = False
        return 1
    print(f"  step             : {latest.step}")
    print(f"  last_node        : {latest.last_node!r}")
    print(f"  next_action      : {latest.next_action!r}")
    rehydrated = CveRemState.model_validate(latest.state)
    print(f"  rehydrated.run_id        : {rehydrated.run_id}")
    print(f"  rehydrated.hitl_blocked_at: {rehydrated.hitl_blocked_at!r}")
    print(f"  rehydrated.plan_hash     : {rehydrated.plan_hash!r}")
    if rehydrated.run_id != run_id:
        print("  ! run_id mismatch")
        overall = False
    if not rehydrated.hitl_blocked_at:
        print("  ! gate state lost on rehydrate")
        overall = False

    # Stage 6: analyst approves; resume.
    print("\n--- Stage 6: analyst approves; resume ---")
    resumed = await _resume(rehydrated)
    print(f"  hitl_blocked_at   : {resumed.hitl_blocked_at!r}")
    print(f"  cr_status         : {resumed.cr_status!r}")
    print(f"  canary/stage/fleet: "
          f"{resumed.canary_passed}/{resumed.stage_passed}/{resumed.fleet_passed}")
    print(f"  per_host_apply    : {len(resumed.per_host_apply_results)}")
    print(f"  verify_outcome    : {resumed.verify_outcome!r}")
    if resumed.hitl_blocked_at:
        print("  ! gate not cleared")
        overall = False
    if not (resumed.canary_passed and resumed.stage_passed
            and resumed.fleet_passed):
        print("  ! rollout did not complete after resume")
        overall = False
    if not resumed.per_host_apply_results:
        print("  ! per_host_apply empty after resume")
        overall = False

    # Stage 7: write the resumed checkpoint (close out the run).
    print("\n--- Stage 7: write resumed checkpoint ---")
    resumed_snapshot = resumed.model_dump(mode="json")
    await cp2.write(Checkpoint(
        run_id=run_id, step=2, branch_id=None, parent_step_idx=1,
        graph_hash=GRAPH_HASH, runtime_hash=RUNTIME_HASH,
        state=resumed_snapshot, clips_facts=[],
        last_node="verify_immediate",
        next_action={"kind": "goto", "target": "write_retrospective"},
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash=hashlib.sha256(b"resumed").hexdigest(),
    ))
    final = await cp2.read_latest(run_id)
    print(f"  final step       : {final.step if final else None}")
    print(f"  final last_node  : {final.last_node if final else None}")
    if not final or final.step != 2:
        print("  ! resumed checkpoint not visible")
        overall = False

    await cp.close_pool()
    await cp2.close_pool()

    print()
    if overall:
        print("=== OVERALL: PASS ===")
    else:
        print("=== OVERALL: FAIL ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
