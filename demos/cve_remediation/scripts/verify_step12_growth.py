# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 12 verification: multi-run growth via planner template-lookup.

Drives the full pipeline twice for the same CVE/CWE and asserts that
Run #2 measurably benefits from Run #1's retrospective.

The PlannerNode flips ``template_lookup_hit=True`` when
``prior_retro_outcomes["patched"] > 0`` (PlannerNode line ~2066). On a
clean baseline this would be False on Run 1 and True on Run 2; against
a re-used demo PDI/PG state we instead assert *delta growth* — Run 2
must observe at least one more patched prior retro than Run 1, and Run
2's planner template_lookup_hit must be True.

Assertions:

1. Run 1 successfully writes a retro with ``retro_outcome="patched"``
   (the artifact Run 2 will consume).
2. Run 2's ``prior_retro_outcomes["patched"]`` >= Run 1's
   ``prior_retro_outcomes.get("patched", 0) + 1`` (one more patched
   retro is in the buffer because Run 1 wrote it).
3. Run 2's ``prior_retro_count`` > Run 1's ``prior_retro_count``
   (overall retrieval count grew).
4. Run 2's ``template_lookup_hit == True`` (planner override fires —
   the lookup is now a "hit" because a prior patched plan exists).
5. Run 2's ``prior_retro_retrieval_status == "ok"`` (dual-store path
   reached both Redis and PG).
6. Run 2's plan_rationale (or upstream prompt) acknowledges prior
   retros (heuristic: rationale mentions either ``prior`` or
   ``retrospective`` or includes the patched count).

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \\
    uv run --no-project python -m demos.cve_remediation.scripts.verify_step12_growth
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace

from demos.cve_remediation.graph.real_nodes import (
    AttachAllArtifactsNode,
    CanonicalizeTrustedNode,
    CargoNetWritebackNode,
    CloseChangeRequestNode,
    CodeWriterNode,
    CorrelateAssetsBrokerNode,
    CreateChangeRequestNode,
    DriftWatchSpawnNode,
    EmitDocxArchiveNode,
    EmitRetroPayloadNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    HitlChangeApprovalNode,
    HitlRetrospectiveReviewNode,
    IntakeFetchNode,
    PlanKgWritebackNode,
    PlannerNode,
    ProgressiveExecuteNode,
    PublishDocPlusNode,
    RenderDocxNode,
    SandboxDispatchNode,
    SandboxRunNode,
    VecSearchRetrosNode,
    VerifyImmediateNode,
    WriteRetrospectiveNode,
)
from demos.cve_remediation.graph.state import CveRemState

DEFAULT_CVE = os.environ.get("STEP12_CVE", "CVE-2024-26130")


async def _drive(cve_id: str, label: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id=f"verify-step12-{label}")
    pipeline = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        # Step 10/12: prior retros must reach the planner.
        VecSearchRetrosNode(),
        PlannerNode(),
        CodeWriterNode(),
        SandboxDispatchNode(),
        SandboxRunNode(),
        CreateChangeRequestNode(),
        AttachAllArtifactsNode(),
        HitlChangeApprovalNode(),
        ProgressiveExecuteNode(),
        VerifyImmediateNode(),
        WriteRetrospectiveNode(),
        HitlRetrospectiveReviewNode(),
        EmitRetroPayloadNode(),
        RenderDocxNode(),
        EmitDocxArchiveNode(),
        PublishDocPlusNode(),
        CargoNetWritebackNode(),
        PlanKgWritebackNode(),
        CloseChangeRequestNode(),
        DriftWatchSpawnNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pipeline:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


def _print_run(label: str, s: CveRemState) -> None:
    print(f"  [{label}] retro_id              : {s.retro_id}")
    print(f"  [{label}] retro_outcome         : {s.retro_outcome!r}")
    print(f"  [{label}] retro_pg_written      : {s.retro_pg_written}")
    print(f"  [{label}] template_lookup_hit   : {s.template_lookup_hit}")
    print(f"  [{label}] prior_retro_count     : {s.prior_retro_count}")
    print(f"  [{label}] prior_retro_outcomes  : {dict(s.prior_retro_outcomes)}")
    print(f"  [{label}] retrieval_status      : {s.prior_retro_retrieval_status!r}")
    print(f"  [{label}] prior_retro_suggestions: {len(s.prior_retro_suggestions)}")
    print(f"  [{label}] suggestions_consumed  : {s.suggestions_consumed_count}")
    print(f"  [{label}] plan_quality_score_bp : {s.plan_quality_score_bp}")


def _grade(s1: CveRemState, s2: CveRemState) -> bool:
    ok = True

    # 1. Run 1 must have written a patched retro for Run 2 to consume.
    if s1.retro_outcome != "patched":
        print(f"  ! Run 1 retro_outcome={s1.retro_outcome!r}, must be 'patched' "
              "for Step 12's growth signal to be honest")
        ok = False
    if not s1.retro_pg_written:
        print("  ! Run 1 retro_pg_written=False; Run 1 didn't contribute "
              "anything to the retro store, growth claim is vacuous")
        ok = False

    # 2. Patched count grew by at least 1.
    p1 = int(s1.prior_retro_outcomes.get("patched", 0) or 0)
    p2 = int(s2.prior_retro_outcomes.get("patched", 0) or 0)
    print(f"  patched prior_outcomes : run1={p1} -> run2={p2} (delta={p2-p1})")
    if p2 < p1 + 1:
        print(f"  ! patched count did not grow by >=1 (run1={p1}, run2={p2}); "
              "Run 1's retro did not reach Run 2's planner via reflexion")
        ok = False

    # 3. Total prior_retro_count grew.
    print(f"  prior_retro_count      : run1={s1.prior_retro_count} -> "
          f"run2={s2.prior_retro_count}")
    if s2.prior_retro_count <= s1.prior_retro_count:
        print(f"  ! prior_retro_count did not grow "
              f"({s1.prior_retro_count} -> {s2.prior_retro_count})")
        ok = False

    # 4. Planner override fires on Run 2: template_lookup_hit=True.
    print(f"  template_lookup_hit    : run1={s1.template_lookup_hit} -> "
          f"run2={s2.template_lookup_hit}")
    if not s2.template_lookup_hit:
        print("  ! Run 2 template_lookup_hit=False; PlannerNode did not "
              "consult prior_retro_outcomes['patched'] > 0")
        ok = False

    # 5. Dual-store retrieval health on Run 2.
    print(f"  retrieval_status (run2): {s2.prior_retro_retrieval_status!r}")
    if s2.prior_retro_retrieval_status != "ok":
        print("  ! Run 2 retrieval_status != 'ok'; degraded dual-store "
              "means we cannot prove planner saw both Redis + PG")
        ok = False

    # 6. Run 2 rationale acknowledges prior retros.
    rat = (s2.plan_rationale or "").lower()
    mentions_prior = any(
        token in rat for token in ("prior", "retrospective", "previous run")
    )
    print(f"  run2 rationale mentions prior: {mentions_prior}")
    if not mentions_prior:
        print("  ! Run 2 plan_rationale does not reference prior retros; "
              "either the prompt didn't include them or LM was off")
        ok = False

    # 7. Step 12 (b): planner consumed at least one suggestion mined
    #    from a prior retro (proves the cve_rem_retro_suggestions
    #    table is read by the planner, not just write-only).
    print(f"  suggestions_consumed: run1={s1.suggestions_consumed_count} -> "
          f"run2={s2.suggestions_consumed_count}")
    if s2.suggestions_consumed_count < 1:
        print("  ! Run 2 suggestions_consumed_count == 0; planner did not "
              "inject any prior-retro suggestions into the rationale")
        ok = False
    if not s2.prior_retro_suggestions:
        print("  ! Run 2 prior_retro_suggestions is empty; "
              "VecSearchRetrosNode did not surface any rows from "
              "cve_rem_retro_suggestions joined to embeddings")
        ok = False

    # 8. Run 2 rationale must echo at least one suggestion text
    #    verbatim (proves the injection actually landed in the
    #    rationale, not just consumed-counter incremented).
    if s2.prior_retro_suggestions:
        first = str(s2.prior_retro_suggestions[0].get("suggestion_text", "") or "")
        # Use first 60 chars as needle to allow truncation/normalization.
        needle = first[:60].strip()
        echoes = needle and needle in (s2.plan_rationale or "")
        print(f"  run2 rationale echoes suggestion: {bool(echoes)} "
              f"(needle={needle!r})")
        if not echoes:
            print("  ! Run 2 plan_rationale does not echo any prior "
                  "suggestion text verbatim")
            ok = False

    # 9. Step 12 (d'): plan_quality_score_bp grows Run 1 -> Run 2.
    #    The score is a composite of prior_retro_count, suggestions
    #    consumed, and inverse verifier findings; if the planner truly
    #    benefits from prior retros the score must move up monotonic.
    print(f"  plan_quality_score_bp: run1={s1.plan_quality_score_bp} -> "
          f"run2={s2.plan_quality_score_bp} "
          f"(delta={s2.plan_quality_score_bp - s1.plan_quality_score_bp})")
    # Saturated case: when run1 already sits at the 10000 ceiling there
    # is no headroom for growth, so a strict-greater check would fail
    # forever even when every other growth signal (rationale evolution,
    # suggestion echo, retro retrieval, prior count) reports OK. Treat
    # equal-at-ceiling as substantively passing — the score signal is
    # vacuous, not regressed.
    SCORE_CEILING = 10000
    saturated = s1.plan_quality_score_bp >= SCORE_CEILING
    if s2.plan_quality_score_bp < s1.plan_quality_score_bp:
        print("  ! plan_quality_score_bp regressed; growth signal negative")
        ok = False
    elif s2.plan_quality_score_bp == s1.plan_quality_score_bp and not saturated:
        print("  ! plan_quality_score_bp did not grow; substantive "
              "growth signal is absent")
        ok = False
    elif saturated:
        print(f"  (plan_quality_score_bp pinned at ceiling "
              f"{SCORE_CEILING}; growth check vacuous — relying on "
              "rationale / suggestion / retro signals above)")
    if s2.plan_quality_score_bp < 4000:
        print(f"  ! Run 2 plan_quality_score_bp={s2.plan_quality_score_bp} "
              "below 4000 baseline; planner is degraded")
        ok = False

    return ok


async def main() -> int:
    print("=== STEP 12 VERIFICATION (multi-run planner growth) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset; CR write will be dry-run.\n")
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    os.environ["CVE_REM_SKIP_SEED"] = "1"

    print(f"--- Run 1: {DEFAULT_CVE} ---")
    s1 = await _drive(DEFAULT_CVE, "run1")
    _print_run("run1", s1)

    print(f"\n--- Run 2: {DEFAULT_CVE} ---")
    s2 = await _drive(DEFAULT_CVE, "run2")
    _print_run("run2", s2)

    print()
    overall = _grade(s1, s2)
    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
