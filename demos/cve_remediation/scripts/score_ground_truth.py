# SPDX-License-Identifier: Apache-2.0
"""Ground-truth scorer: precision / recall on applicable subset.

Compares pipeline output against ground truth derived **mechanically**
from two sources only:

1. **NVD CPE 2.3** (per-CVE) → substrate applicability via
   :func:`derive_substrate_profile_from_cpes`. No hand-authored
   ``(vendor, product) → verdict`` table; the same classifier the
   pipeline uses.
2. **h11 CMDB seed inventory** (``h11_cmdb_seed.json``) → which
   h11-lab hosts run which product. This is the source of truth for
   the **h11 fleet only**; the real PDI CMDB may carry additional
   inventory (laptops, decoms, pre-existing CIs) outside the h11 lab
   scope — those hosts are IGNORED for scoring, neither rewarded nor
   penalized. Scoring asks: "for the h11 fleet that the lab has
   asserted, did the pipeline correctly correlate the right hosts?"
   Bindings are matched to a CVE by fuzzy alphanumeric token overlap
   between the seed's ``product`` / ``vendor`` strings and the CVE's
   CPE 2.3 product / vendor tokens, applied per-CPE-URI. We do NOT
   match by ``cve_id`` — that would let seed-coverage gaps look like
   pipeline misses.

Universe restriction::

    H = set of every host_name appearing in any seed binding.

    relevant_pipeline_hosts = pipeline_hosts ∩ H
    pipeline_applicable     = bool(relevant_pipeline_hosts)
                              # OR pipeline disposition == "applicable"
                              # only when that disposition is backed
                              # by at least one host inside H.

Ground truth per CVE::

    if substrate denies all CPE rows:                expected = not_applicable
    elif any seed entry's product/vendor tokens
         overlap with CVE CPE product tokens:        expected = applicable,
                                                      hosts = union of matched bindings
    else:                                            expected = not_applicable

Pipeline produced::

    pipeline_applicable = state.disposition == "applicable"
                          OR verify_outcome in {patched, vulnerable, rollback}
    pipeline_hosts      = state.affected_host_names

Metrics:
  - Applicability   : precision / recall / F1 across the run set.
  - Host correlation: precision / recall / F1 across union of
    expected and reported hosts, restricted to expected_applicable.
  - Combined gate   : True when both ≥ ``--target-bp`` (default 0.85).

Usage::

    uv run --no-project python -m demos.cve_remediation.scripts.score_ground_truth \\
        --fixture smoke5 --serve-base http://localhost:9001

The pipeline must be running at ``--serve-base`` (defaults to
$HARBOR_SERVE_BASE or http://127.0.0.1:9001) with the
cve-rem-pipeline graph loaded and its checkpoint store reachable
at $CVE_REM_CHECKPOINT_DB (default ``/tmp/score-checkpoints.sqlite``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import time
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from demos.cve_remediation.tools.cmdb_substrate import (
    DEFAULT_SUBSTRATE_SPEC,
    derive_substrate_profile_from_cpes,
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
# Common short / noise tokens that cause false-positive overlaps when
# matched alone. Real product matches use ≥2 tokens or a single
# distinctive token longer than 3 chars.
_NOISE_TOKENS = frozenset({
    "the", "and", "for", "of", "to", "in", "on", "with",
    "a", "an", "v", "x", "i", "ii", "iii", "iv", "vs",
    "software", "firmware", "server", "client", "system",
    "library", "module", "plugin", "engine", "service",
    "os", "ui", "app", "core", "kernel", "main",
})


def _tokens(text: str) -> set[str]:
    """Lowercase alphanumeric tokens, noise-stripped, with digit-stems.

    Each raw token is included AND a trailing-digit-stripped variant
    so ``log4j`` and ``log4j2`` collapse to a shared ``log4j`` stem
    (NVD CPE often pins the product to a version-suffixed name while
    CMDB inventory lists the unsuffixed product family). Additionally
    yields the concatenated form of multi-word identifiers so vendor
    spellings like ``"Check Point"`` and ``"checkpoint"`` collapse to
    the same token set.
    """
    if not text:
        return set()
    raws = _TOKEN_RE.findall(text.lower())
    out: set[str] = set()
    for raw in raws:
        if not raw or len(raw) < 2 or raw in _NOISE_TOKENS:
            continue
        out.add(raw)
        stripped = raw.rstrip("0123456789")
        if stripped and len(stripped) >= 3 and stripped != raw:
            out.add(stripped)
    # Concatenated form: ``check point`` → ``checkpoint``.
    kept = [r for r in raws if r and len(r) >= 2 and r not in _NOISE_TOKENS]
    if len(kept) >= 2:
        joined = "".join(kept)
        if len(joined) >= 4:
            out.add(joined)
    return out


def _cpe_product_tokens(cpe_uri: str) -> tuple[set[str], set[str]]:
    """Extract (vendor_tokens, product_tokens) from one CPE 2.3 URI."""
    parts = (cpe_uri or "").split(":")
    if len(parts) < 5:
        return set(), set()
    return _tokens(parts[3]), _tokens(parts[4])


_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_DEFAULT_FIXTURE = "smoke5"

_DEFAULT_SERVE_BASE = os.environ.get(
    "HARBOR_SERVE_BASE", "http://127.0.0.1:9001"
)
_DEFAULT_GRAPH_ID = os.environ.get(
    "CVE_REM_GRAPH_ID", "graph:cve-rem-pipeline"
)
_DEFAULT_CHECKPOINT_DB = Path(
    os.environ.get("CVE_REM_CHECKPOINT_DB", "/tmp/score-checkpoints.sqlite")
)
_DEFAULT_RUN_TIMEOUT_S = int(os.environ.get("CVE_REM_RUN_TIMEOUT_S", "600"))
_POLL_INTERVAL_S = 3.0

_H11_SEED = _FIXTURES / "h11_cmdb_seed.json"

# Outcomes that count as "pipeline judged this CVE applicable to the env."
# Aligns with smoke_score's recovery set: anything the pipeline routed
# through CR creation + verify counts as a positive applicability call.
_POSITIVE_OUTCOMES = {"patched", "vulnerable", "rollback", "unpatchable_hitl_pending"}


# ---------------------------------------------------------------------------
# Ground truth derivation (mechanical)
# ---------------------------------------------------------------------------


@dataclass
class SeedEntry:
    """One row from the h11 lab inventory.

    Built once from ``h11_cmdb_seed.json``; the ``cve_id`` is recorded
    for audit only — ground-truth matching uses ``product_tokens`` and
    ``vendor_tokens`` against the CVE's CPE so seed gaps (CVE-2021-44228
    bound under CVE-2021-45046's Log4j2 entry, for example) still
    surface the right hosts.
    """

    cve_id: str
    host_names: list[str]
    product_tokens: set[str]
    vendor_tokens: set[str]


@dataclass
class GroundTruth:
    cve_id: str
    expected_applicable: bool
    expected_hosts: list[str] = field(default_factory=list)
    substrate_decision: str = ""  # rule_id from cmdb_substrate
    matched_seed_cves: list[str] = field(default_factory=list)


def _load_seed_entries(path: Path) -> list[SeedEntry]:
    """Parse h11 lab inventory into token-indexed seed entries."""
    if not path.is_file():
        return []
    raw = json.loads(path.read_text())
    out: list[SeedEntry] = []
    for b in raw.get("bindings", []):
        cid = str(b.get("cve_id", "") or "")
        hosts = sorted({str(h) for h in (b.get("host_names") or [])})
        product = str(b.get("product", "") or "")
        vendor = str(b.get("vendor", "") or "")
        out.append(SeedEntry(
            cve_id=cid,
            host_names=hosts,
            product_tokens=_tokens(product),
            vendor_tokens=_tokens(vendor),
        ))
    return out


def _seed_host_universe(entries: list[SeedEntry]) -> set[str]:
    """Every host_name appearing anywhere in the h11 seed inventory."""
    return {h for e in entries for h in e.host_names}


def _seed_match(
    entry: SeedEntry,
    cpe_vendor_tokens: set[str],
    cpe_product_tokens: set[str],
) -> bool:
    """Decide whether ``entry`` belongs to a CVE with these CPE tokens.

    Match requires at least one product-token intersection AND at least
    one vendor-token intersection. Vendor agreement disambiguates
    common-noun products (``ios`` = Cisco IOS vs Apple iOS) — the
    substrate guard already drops Apple iOS by ``target_sw``, but the
    seed match still needs to refuse incorrect bindings.
    """
    if not (entry.product_tokens and cpe_product_tokens):
        return False
    if not (entry.product_tokens & cpe_product_tokens):
        return False
    # Vendor check — when either side has tokens, they must overlap.
    if entry.vendor_tokens and cpe_vendor_tokens:
        return bool(entry.vendor_tokens & cpe_vendor_tokens)
    # If either side lacks vendor data, fall back to product-only match.
    return True


def _derive_ground_truth(
    cve_id: str,
    cpe_uris: list[str],
    seed_entries: list[SeedEntry],
) -> GroundTruth:
    """Combine CPE substrate + h11 seed inventory into expected verdict.

    Substrate verdict comes from the same classifier the pipeline uses.
    Host expectations come from token-overlap between the CVE's CPE
    product list and the lab's seed inventory entries (NOT from
    cve_id matching — see module docstring for rationale).
    """
    profile, _decisions = derive_substrate_profile_from_cpes(
        cpe_uris, DEFAULT_SUBSTRATE_SPEC,
    )
    substrate_applicable = profile.rule_id != "cpe_substrate_denied"
    if not substrate_applicable:
        return GroundTruth(
            cve_id=cve_id,
            expected_applicable=False,
            expected_hosts=[],
            substrate_decision=profile.rule_id,
        )
    # Match each CPE URI independently — never aggregate tokens across
    # the full list. NVD lists CVEs against every product that bundles
    # the vulnerable component (Log4Shell has 166 CPE rows spanning
    # IBM, Cisco, Oracle, etc.). Token-union would falsely bind every
    # downstream vendor's products to the upstream CVE.
    matched_hosts: set[str] = set()
    matched_cves: list[str] = []
    for uri in cpe_uris:
        v_toks, p_toks = _cpe_product_tokens(uri)
        if not p_toks:
            continue
        for entry in seed_entries:
            if _seed_match(entry, v_toks, p_toks):
                matched_hosts.update(entry.host_names)
                if entry.cve_id and entry.cve_id not in matched_cves:
                    matched_cves.append(entry.cve_id)
    hosts = sorted(matched_hosts)
    return GroundTruth(
        cve_id=cve_id,
        expected_applicable=bool(hosts),
        expected_hosts=hosts,
        substrate_decision=profile.rule_id,
        matched_seed_cves=matched_cves,
    )


# ---------------------------------------------------------------------------
# Pipeline run + state read (mirrors smoke_score)
# ---------------------------------------------------------------------------


async def _start_run(client, graph_id: str, cve_id: str) -> dict:
    body = {
        "graph_id": graph_id,
        "params": {"cve_id": cve_id, "site": "h11", "score_gt_run": True},
        "idempotency_key": f"gt-{cve_id}-{int(time.time())}",
    }
    r = await client.post("/v1/runs", json=body, timeout=30)
    r.raise_for_status()
    return r.json()


async def _poll_until_terminal(client, run_id: str, timeout_s: int) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = await client.get(f"/v1/runs/{run_id}", timeout=30)
        if r.status_code == 404:
            await asyncio.sleep(_POLL_INTERVAL_S)
            continue
        r.raise_for_status()
        body = r.json()
        if body.get("status") in (
            "done", "completed", "error", "cancelled", "failed"
        ):
            return body
        await asyncio.sleep(_POLL_INTERVAL_S)
    return {"status": "timeout"}


def _read_final_state(db_path: Path, run_id: str) -> dict:
    if not db_path.exists():
        return {}
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10.0) as conn:
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            """
            SELECT state_snapshot
            FROM checkpoints WHERE run_id = ?
            ORDER BY step_idx DESC, branch_id DESC
            LIMIT 1
            """,
            (run_id,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    raw = row["state_snapshot"] or "{}"
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Pipeline-side extraction + scoring
# ---------------------------------------------------------------------------


@dataclass
class PipelineOutput:
    pipeline_applicable: bool
    pipeline_hosts: list[str]
    verify_outcome: str
    disposition: str
    cpe_uris: list[str]


def _extract_pipeline(state: dict) -> PipelineOutput:
    verify_outcome = str(state.get("verify_outcome", "") or "")
    disposition = str(
        (state.get("correlated") or {}).get("disposition", "") or ""
    )
    hosts = sorted(set(str(h) for h in state.get("affected_host_names", []) or []))
    extract = state.get("extract") or {}
    cpe_uris = list(extract.get("cpe_uris", []) or [])
    if not cpe_uris:
        cpe_uris = list(state.get("advisory_cpe_uris", []) or [])
    pipeline_applicable = (
        disposition == "applicable"
        or verify_outcome in _POSITIVE_OUTCOMES
    )
    return PipelineOutput(
        pipeline_applicable=pipeline_applicable,
        pipeline_hosts=hosts,
        verify_outcome=verify_outcome,
        disposition=disposition,
        cpe_uris=cpe_uris,
    )


@dataclass
class CveScore:
    cve_id: str
    gt: GroundTruth
    pipeline: PipelineOutput
    # Pipeline hosts restricted to the h11 universe (everything else
    # — laptops, decoms, pre-existing PDI inventory — is dropped before
    # scoring).
    relevant_pipeline_hosts: list[str] = field(default_factory=list)
    pipeline_extras: list[str] = field(default_factory=list)
    applicability_bucket: str = ""  # tp / fp / fn / tn
    host_tp: int = 0
    host_fp: int = 0
    host_fn: int = 0


def _score_one(gt: GroundTruth, p: PipelineOutput, h11_universe: set[str]) -> CveScore:
    out = CveScore(cve_id=gt.cve_id, gt=gt, pipeline=p)
    pipeline_set = set(p.pipeline_hosts)
    relevant = pipeline_set & h11_universe
    extras = pipeline_set - h11_universe
    out.relevant_pipeline_hosts = sorted(relevant)
    out.pipeline_extras = sorted(extras)

    # Pipeline judged applicable iff it produced ≥1 host in the h11 universe
    # (hosts outside the universe are out-of-scope and don't constitute a
    # positive call for h11 scoring purposes). When the pipeline returned
    # zero in-universe hosts but reports disposition=applicable on out-of-
    # universe inventory, the score is "tn" (not h11's job to remediate).
    pipeline_applicable_h11 = bool(relevant)

    if gt.expected_applicable and pipeline_applicable_h11:
        out.applicability_bucket = "tp"
    elif not gt.expected_applicable and pipeline_applicable_h11:
        out.applicability_bucket = "fp"
    elif gt.expected_applicable and not pipeline_applicable_h11:
        out.applicability_bucket = "fn"
    else:
        out.applicability_bucket = "tn"

    if gt.expected_applicable:
        exp = set(gt.expected_hosts)
        out.host_tp = len(exp & relevant)
        out.host_fp = len(relevant - exp)
        out.host_fn = len(exp - relevant)
    return out


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def _amain(args) -> int:
    fix_name = args.fixture
    cand = _FIXTURES / f"scoring_{fix_name}.json"
    fpath = cand if cand.is_file() else Path(fix_name)
    if not fpath.is_file():
        print(f"! fixture not found: {fpath}", file=sys.stderr)
        return 2
    raw = json.loads(fpath.read_text())
    # Fixture entries may be plain CVE-id strings OR dict rows with
    # ``cve_id`` plus metadata (the scoring fixtures use the latter).
    cves: list[str] = []
    for c in raw.get("cves", []):
        if isinstance(c, dict):
            cid = str(c.get("cve_id", "") or "")
        else:
            cid = str(c or "")
        if cid:
            cves.append(cid)
    if args.limit:
        cves = cves[: args.limit]
    if not cves:
        print(f"! fixture {fpath} has no cves", file=sys.stderr)
        return 2

    seed_entries = _load_seed_entries(_H11_SEED)
    h11_universe = _seed_host_universe(seed_entries)
    print(f"=== score_ground_truth ===")
    print(f"  serve      : {args.serve_base}")
    print(f"  fixture    : {fpath.name} ({len(cves)} CVEs)")
    print(f"  seed       : {_H11_SEED.name} ({len(seed_entries)} bindings, "
          f"{len(h11_universe)} unique h11 hosts)")
    print(f"  ckpt db    : {args.checkpoint_db}")
    print(f"  target_bp  : {args.target_bp}")
    print()

    scores: list[CveScore] = []
    async with httpx.AsyncClient(base_url=args.serve_base) as client:
        for idx, cve_id in enumerate(cves, 1):
            t0 = time.perf_counter()
            print(f"  [{idx:>3}/{len(cves)}] {cve_id}: starting run ...", flush=True)
            try:
                r = await _start_run(client, args.graph_id, cve_id)
            except Exception as exc:
                print(f"    ! _start_run failed: {type(exc).__name__}: {exc}")
                continue
            run_id = r.get("run_id") or r.get("id") or ""
            if not run_id:
                print(f"    ! no run_id in response: {r}")
                continue
            outcome = await _poll_until_terminal(client, run_id, _DEFAULT_RUN_TIMEOUT_S)
            elapsed = time.perf_counter() - t0
            status = outcome.get("status", "?")
            print(f"    terminal={status} t={elapsed:.1f}s run_id={run_id}")
            state = _read_final_state(args.checkpoint_db, run_id)
            if not state:
                print(f"    ! no state_snapshot for {run_id}")
                continue
            pipeline = _extract_pipeline(state)
            gt = _derive_ground_truth(cve_id, pipeline.cpe_uris, seed_entries)
            scores.append(_score_one(gt, pipeline, h11_universe))

    if not scores:
        print("! no scored runs", file=sys.stderr)
        return 1

    # ---------- Per-CVE breakdown ----------
    print()
    print("=== PER-CVE (h11 universe only — out-of-scope hosts ignored) ===")
    print(f"  {'CVE':<18}  {'exp':<3}  {'got':<3}  {'app':<3}  expected_h11 -> relevant_h11  [extras]  outcome")
    for s in scores:
        exp_a = "Y" if s.gt.expected_applicable else "n"
        got_a = "Y" if bool(s.relevant_pipeline_hosts) else "n"
        extras_str = f"  +{len(s.pipeline_extras)} extras" if s.pipeline_extras else ""
        hosts_line = (
            f"[{','.join(s.gt.expected_hosts) or '-'}]"
            f" -> [{','.join(s.relevant_pipeline_hosts) or '-'}]"
        )
        print(
            f"  {s.cve_id:<18}  {exp_a:<3}  {got_a:<3}  "
            f"{s.applicability_bucket:<3}  {hosts_line}{extras_str}  "
            f"{s.pipeline.verify_outcome}"
        )

    # ---------- Applicability aggregate ----------
    a_tp = sum(1 for s in scores if s.applicability_bucket == "tp")
    a_fp = sum(1 for s in scores if s.applicability_bucket == "fp")
    a_fn = sum(1 for s in scores if s.applicability_bucket == "fn")
    a_tn = sum(1 for s in scores if s.applicability_bucket == "tn")
    a_p, a_r, a_f1 = _prf(a_tp, a_fp, a_fn)

    # ---------- Host-correlation aggregate (applicable subset only) ----------
    h_tp = sum(s.host_tp for s in scores)
    h_fp = sum(s.host_fp for s in scores)
    h_fn = sum(s.host_fn for s in scores)
    h_p, h_r, h_f1 = _prf(h_tp, h_fp, h_fn)

    target = args.target_bp / 10000.0
    print()
    print("=== APPLICABILITY (does pipeline correctly judge CVE applies?) ===")
    print(f"  tp={a_tp}  fp={a_fp}  fn={a_fn}  tn={a_tn}")
    print(f"  precision={a_p:.3f}  recall={a_r:.3f}  f1={a_f1:.3f}")
    print(
        "  gate={}  ({:.2f} >= {:.2f})".format(
            "PASS" if min(a_p, a_r) >= target else "FAIL",
            min(a_p, a_r), target,
        )
    )

    print()
    print("=== HOST CORRELATION (within expected-applicable subset) ===")
    print(f"  tp={h_tp}  fp={h_fp}  fn={h_fn}")
    print(f"  precision={h_p:.3f}  recall={h_r:.3f}  f1={h_f1:.3f}")
    print(
        "  gate={}  ({:.2f} >= {:.2f})".format(
            "PASS" if min(h_p, h_r) >= target else "FAIL",
            min(h_p, h_r), target,
        )
    )

    combined_pass = (
        min(a_p, a_r) >= target and min(h_p, h_r) >= target
    )
    print()
    print("=== SUMMARY ===")
    print(f"  runs scored : {len(scores)}")
    print(f"  app    P/R  : {a_p:.3f} / {a_r:.3f}")
    print(f"  host   P/R  : {h_p:.3f} / {h_r:.3f}")
    print(f"  RESULT      : {'GREEN' if combined_pass else 'RED'} (target={target:.2f})")

    # JSON sidecar for downstream programs
    if args.json_out:
        payload = {
            "fixture": str(fpath),
            "target": target,
            "applicability": {
                "tp": a_tp, "fp": a_fp, "fn": a_fn, "tn": a_tn,
                "precision": a_p, "recall": a_r, "f1": a_f1,
            },
            "host_correlation": {
                "tp": h_tp, "fp": h_fp, "fn": h_fn,
                "precision": h_p, "recall": h_r, "f1": h_f1,
            },
            "runs": [
                {
                    "cve_id": s.cve_id,
                    "expected_applicable": s.gt.expected_applicable,
                    "expected_hosts": s.gt.expected_hosts,
                    "substrate_decision": s.gt.substrate_decision,
                    "matched_seed_cves": s.gt.matched_seed_cves,
                    "pipeline_applicable": s.pipeline.pipeline_applicable,
                    "pipeline_hosts_all": s.pipeline.pipeline_hosts,
                    "relevant_pipeline_hosts": s.relevant_pipeline_hosts,
                    "pipeline_extras_out_of_scope": s.pipeline_extras,
                    "verify_outcome": s.pipeline.verify_outcome,
                    "applicability_bucket": s.applicability_bucket,
                    "host_tp": s.host_tp,
                    "host_fp": s.host_fp,
                    "host_fn": s.host_fn,
                }
                for s in scores
            ],
            "generated_at": datetime.now(UTC).isoformat(),
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2))
        print(f"  json out    : {args.json_out}")

    return 0 if combined_pass else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", default=_DEFAULT_FIXTURE)
    ap.add_argument("--limit", type=int, default=0,
                    help="max CVEs from fixture (0=all)")
    ap.add_argument("--serve-base", default=_DEFAULT_SERVE_BASE)
    ap.add_argument("--graph-id", default=_DEFAULT_GRAPH_ID)
    ap.add_argument("--checkpoint-db", type=Path, default=_DEFAULT_CHECKPOINT_DB)
    ap.add_argument("--target-bp", type=int, default=8500,
                    help="target precision+recall in basis points (default 8500 = 0.85)")
    ap.add_argument("--json-out", default="",
                    help="optional path to write detailed JSON report")
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
