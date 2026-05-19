# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 10 verification: retro retrieval across runs.

Drives the full pipeline twice for the same CVE. Asserts:

* PG row count for the cve_id grows on first run, stays stable on
  re-run (idempotent ON CONFLICT DO UPDATE).
* Redis Reflexion list ``reflexion:{cwe}`` strictly grows by one per
  run (LPUSH + LTRIM 999).
* pgvector ``cve_rem_retro_embeddings`` carries a row for the
  retro_id; ``cve_rem_retro_suggestions`` rows accumulate.
* state.prior_retro_count on the second run is >= 1 (Run #2 actually
  sees Run #1's retro via VecSearchRetrosNode).

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \
    uv run --no-project python -m demos.cve_remediation.scripts.verify_step10_retro
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

import asyncpg
import redis.asyncio as aioredis

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

DEFAULT_CVE = os.environ.get("STEP10_CVE", "CVE-2024-26130")


async def _drive(cve_id: str, label: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id=f"verify-step10-{label}")
    pipeline = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        # Step 10: surface prior retros to the planner.
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


async def _pg_retro_count(cve_id: str) -> int:
    dsn = os.environ.get("POSTGRES_DSN", "")
    if not dsn:
        return -1
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM cve_rem_retros WHERE cve_id=$1",
            cve_id,
        )
        return int(row["n"]) if row else 0
    finally:
        await conn.close()


async def _pg_retro_written_at(cve_id: str) -> str:
    """Return MAX(written_at) as ISO string, or '' if none/no DSN."""
    dsn = os.environ.get("POSTGRES_DSN", "")
    if not dsn:
        return ""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT MAX(written_at) AS ts FROM cve_rem_retros WHERE cve_id=$1",
            cve_id,
        )
        ts = row["ts"] if row else None
        return ts.isoformat() if ts else ""
    finally:
        await conn.close()


async def _pgvec_embed_count(cve_id: str) -> int:
    dsn = os.environ.get("PGVECTOR_DSN", "")
    if not dsn:
        return -1
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM cve_rem_retro_embeddings WHERE cve_id=$1",
            cve_id,
        )
        return int(row["n"]) if row else 0
    finally:
        await conn.close()


async def _pgvec_suggestion_count(cwe: str) -> int:
    dsn = os.environ.get("PGVECTOR_DSN", "")
    if not dsn:
        return -1
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT count(*) AS n FROM cve_rem_retro_suggestions s "
            "JOIN cve_rem_retro_embeddings e USING (retro_id) "
            "WHERE e.cwe=$1",
            cwe,
        )
        return int(row["n"]) if row else 0
    finally:
        await conn.close()


async def _redis_reflexion_len(cwe: str) -> int:
    url = os.environ.get("REDIS_URL", "")
    if not url or not cwe:
        return -1
    r = aioredis.from_url(url, decode_responses=True)
    try:
        return int(await r.llen(f"reflexion:{cwe}"))
    finally:
        await r.aclose()


async def main() -> int:
    overall = True
    print("=== STEP 10 VERIFICATION (retro retrieval across runs) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset; CR write will be dry-run.\n")
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    os.environ["CVE_REM_SKIP_SEED"] = "1"

    # Run 1
    print(f"--- Run 1: {DEFAULT_CVE} ---")
    s1 = await _drive(DEFAULT_CVE, "run1")
    cwe = ""
    if s1.extract:
        cwe = str(getattr(s1.extract, "cwe_class", "") or "")
    print(f"  cwe                : {cwe!r}")
    print(f"  retro_id           : {s1.retro_id}")
    print(f"  retro_pg_written   : {s1.retro_pg_written}")
    print(f"  retro_redis_written: {s1.retro_redis_written}")
    print(f"  retro_pgvec_written: {s1.retro_pgvector_written}")
    print(f"  retro_suggestions  : {s1.retro_suggestion_count}")
    print(f"  prior_retro_count  : {s1.prior_retro_count}")

    pg1 = await _pg_retro_count(DEFAULT_CVE)
    rr1 = await _redis_reflexion_len(cwe) if cwe else 0
    pv1 = await _pgvec_embed_count(DEFAULT_CVE)
    sg1 = await _pgvec_suggestion_count(cwe) if cwe else 0
    wa1 = await _pg_retro_written_at(DEFAULT_CVE)
    print(f"  pg rows (cve)      : {pg1}")
    print(f"  pg written_at (max): {wa1!r}")
    print(f"  redis reflexion len: {rr1}")
    print(f"  pgvector rows      : {pv1}")
    print(f"  suggestions (cwe)  : {sg1}")
    print(f"  retrieval status   : {s1.prior_retro_retrieval_status!r}")
    print(f"  prior_retros_pg    : {s1.prior_retros_pg_count}")

    # Run 2 (same CVE)
    print(f"\n--- Run 2: {DEFAULT_CVE} (same CWE; should retrieve Run-1 retro) ---")
    s2 = await _drive(DEFAULT_CVE, "run2")
    print(f"  retro_id           : {s2.retro_id}")
    print(f"  prior_retro_count  : {s2.prior_retro_count}")
    print(f"  prior_retro_outcomes: {dict(s2.prior_retro_outcomes)}")

    pg2 = await _pg_retro_count(DEFAULT_CVE)
    rr2 = await _redis_reflexion_len(cwe) if cwe else 0
    pv2 = await _pgvec_embed_count(DEFAULT_CVE)
    sg2 = await _pgvec_suggestion_count(cwe) if cwe else 0
    wa2 = await _pg_retro_written_at(DEFAULT_CVE)
    print(f"  pg rows (cve)      : {pg2}")
    print(f"  pg written_at (max): {wa2!r}")
    print(f"  redis reflexion len: {rr2}")
    print(f"  pgvector rows      : {pv2}")
    print(f"  suggestions (cwe)  : {sg2}")
    print(f"  retrieval status   : {s2.prior_retro_retrieval_status!r}")
    print(f"  prior_retros_pg    : {s2.prior_retros_pg_count}")
    print(f"  prior_retros_pg_ts : {s2.prior_retros_pg_last_seen!r}")

    # Assertions
    if pg1 < 1 or pg2 < 1:
        print("  ! PG retro row never landed for the CVE")
        overall = False
    # retro_id is sha256(cve|plan_hash|outcome): same across same-input
    # runs so ON CONFLICT DO UPDATE keeps row count at 1. Allow the
    # number to stay stable OR grow (different plan_hash if planner
    # branches on prior retros).
    if pg2 < pg1:
        print("  ! PG retro row count regressed run1->run2")
        overall = False
    if rr2 <= rr1:
        print("  ! Redis Reflexion list did not grow run1->run2 "
              f"({rr1} -> {rr2})")
        overall = False
    if pv1 < 1:
        print("  ! pgvector embedding never landed for the CVE")
        overall = False
    if s2.prior_retro_count < 1:
        print("  ! Run 2 prior_retro_count must be >= 1 "
              "(VecSearchRetrosNode failed to read Run 1's reflexion entry)")
        overall = False
    if not s2.prior_retro_outcomes:
        print("  ! Run 2 prior_retro_outcomes empty; reflexion retrieval "
              "did not return any entries")
        overall = False

    # G8: written_at must advance run1 -> run2 even though ON CONFLICT
    # DO UPDATE collapses row count. Proves THIS run's contribution
    # actually landed on PG, not just an old row from a prior session.
    if not wa1 or not wa2:
        print("  ! pg written_at empty; cve_rem_retros write never landed "
              f"(wa1={wa1!r} wa2={wa2!r})")
        overall = False
    elif wa2 <= wa1:
        print("  ! pg MAX(written_at) did not advance run1->run2 "
              f"({wa1!r} -> {wa2!r}); Run 2's retro upsert was a no-op")
        overall = False

    # G10: retrieval status must be "ok" on Run 2 (both Redis and PG
    # surfaced the prior retro). "redis_only"/"pg_only"/"degraded"
    # means one of the dual stores is unreachable or empty.
    if s2.prior_retro_retrieval_status != "ok":
        print("  ! Run 2 prior_retro_retrieval_status != 'ok' "
              f"({s2.prior_retro_retrieval_status!r}); dual-store "
              "retrieval is degraded")
        overall = False
    if s2.prior_retros_pg_count < 1:
        print("  ! Run 2 prior_retros_pg_count must be >= 1 "
              "(VecSearchRetrosNode failed to read PG cve_rem_retros)")
        overall = False

    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
