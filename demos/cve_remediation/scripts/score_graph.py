# SPDX-License-Identifier: Apache-2.0
"""Unified scorecard for the cve_remediation Harbor demo.

Runs every ``verify_*.py`` harness (basic CRITERIA steps 1-12 + fancy
F1-F14) as a child process, captures rc + duration + tail of stdout,
and emits both a human-readable table and a JSON artifact at
``$HARBOR_ARTIFACTS_ROOT/scorecard/<run_id>.json``.

The scorecard is the single command operators run before declaring
the demo "production ready" — if any cell is FAIL, the corresponding
CRITERIA item has regressed.

Coverage tiers (filename pattern -> tier):

* ``verify_step<N>_*.py``  -> basic-N    (CRITERIA basic set, items 1-12)
* ``verify_F<N>_*.py``     -> fancy-N    (CRITERIA fancy set, items 1-14)
* ``verify_cr.py``         -> SKIPPED    (CLI takes a CR sys_id arg;
                                          run manually w/ --cr <id>)
* ``verify_F3_..._v2.py``  -> canonical  (v2 uses real PostgresCheckpointer;
                                          v1 retained as bespoke baseline)

Examples::

    # full scorecard, fail-fast off, parallel off (default)
    uv run --no-project python -m demos.cve_remediation.scripts.score_graph

    # only fancy items, fail-fast on
    uv run --no-project python -m demos.cve_remediation.scripts.score_graph \\
        --category fancy --fail-fast

    # one specific item
    uv run --no-project python -m demos.cve_remediation.scripts.score_graph \\
        --filter F12

    # JSON-only (machine-readable; no table)
    uv run --no-project python -m demos.cve_remediation.scripts.score_graph \\
        --json-only > scorecard.json
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import subprocess  # noqa: S404 -- driving repo-controlled verifier scripts
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEMO_PKG = "demos.cve_remediation.scripts"
_ARTIFACTS_ROOT = Path(
    os.environ.get("HARBOR_ARTIFACTS_ROOT", ".harbor/artifacts")
)
_SCORECARD_DIR = _ARTIFACTS_ROOT / "scorecard"
_DEFAULT_TIMEOUT_S = int(os.environ.get("CVE_REM_SCORE_TIMEOUT_S", "300"))
_TAIL_LINES = 8

# Verifiers that should not run in the auto-scorecard:
#   verify_cr.py: interactive CLI requiring --cr <sys_id>
#   verify_F3_hitl_durability.py (v1): superseded by v2 (real PostgresCheckpointer)
_SKIP_DEFAULT = {"verify_cr"}


@dataclass
class Result:
    name: str
    category: str            # "basic" | "fancy" | "other"
    item: str                # "step1", "F12", etc.
    rc: int
    elapsed_s: float
    tail: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""

    @property
    def status(self) -> str:
        if self.skipped:
            return "SKIP"
        return "PASS" if self.rc == 0 else "FAIL"


_STEP_RE = re.compile(r"^verify_step(\d+)_")
_FANCY_RE = re.compile(r"^verify_F(\d+)_")


def _classify(stem: str) -> tuple[str, str]:
    """Return (category, item) for a verifier filename stem."""
    m = _STEP_RE.match(stem)
    if m:
        return "basic", f"step{int(m.group(1))}"
    m = _FANCY_RE.match(stem)
    if m:
        return "fancy", f"F{int(m.group(1))}"
    return "other", stem.removeprefix("verify_")


def _discover() -> list[Path]:
    return sorted(_SCRIPTS_DIR.glob("verify_*.py"))


def _run_one(path: Path, timeout_s: int) -> Result:
    stem = path.stem
    category, item = _classify(stem)
    if stem in _SKIP_DEFAULT:
        return Result(
            name=stem,
            category=category,
            item=item,
            rc=0,
            elapsed_s=0.0,
            skipped=True,
            skip_reason="interactive CLI / requires args",
        )
    module = f"{_DEMO_PKG}.{stem}"
    cmd = ["uv", "run", "--no-project", "python", "-m", module]
    start = time.monotonic()
    try:
        proc = subprocess.run(  # noqa: S603 -- repo-controlled module path
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            cwd=str(_SCRIPTS_DIR.parent.parent.parent),
        )
        rc = proc.returncode
        out_lines = (proc.stdout or "").splitlines()
        err_lines = (proc.stderr or "").splitlines()
        # Prefer stdout tail; fall back to stderr if stdout is empty.
        tail_src = out_lines if out_lines else err_lines
        tail = tail_src[-_TAIL_LINES:]
    except subprocess.TimeoutExpired:
        rc = 124
        tail = [f"TIMEOUT after {timeout_s}s"]
    elapsed = time.monotonic() - start
    return Result(
        name=stem,
        category=category,
        item=item,
        rc=rc,
        elapsed_s=round(elapsed, 2),
        tail=tail,
    )


def _filter(paths: list[Path], args: argparse.Namespace) -> list[Path]:
    out = []
    want_categories = (
        {args.category} if args.category and args.category != "all" else None
    )
    for p in paths:
        stem = p.stem
        category, item = _classify(stem)
        if want_categories and category not in want_categories:
            continue
        if args.filter and args.filter.lower() not in stem.lower() \
                and args.filter.lower() != item.lower():
            continue
        if args.skip and stem in args.skip:
            continue
        out.append(p)
    return out


def _print_table(results: list[Result]) -> None:
    # Group by (basic, fancy, other).
    groups: dict[str, list[Result]] = {"basic": [], "fancy": [], "other": []}
    for r in results:
        groups.setdefault(r.category, []).append(r)
    for cat in ("basic", "fancy", "other"):
        rows = groups.get(cat) or []
        if not rows:
            continue
        rows.sort(key=lambda r: (
            int(re.findall(r"\d+", r.item)[0]) if re.findall(r"\d+", r.item)
            else 999, r.item
        ))
        print(f"\n=== {cat.upper()} ({len(rows)}) ===")
        print(f"  {'item':<8} {'status':<6} {'time':>8}  script")
        print(f"  {'-'*8} {'-'*6} {'-'*8}  {'-'*40}")
        for r in rows:
            time_str = (
                f"{r.elapsed_s:7.2f}s" if not r.skipped else "    --  "
            )
            print(f"  {r.item:<8} {r.status:<6} {time_str}  {r.name}")
            if r.status == "FAIL":
                for line in r.tail[-4:]:
                    print(f"           | {line}")


def _summary(results: list[Result]) -> dict[str, int]:
    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    for r in results:
        counts[r.status] += 1
    counts["TOTAL"] = len(results)
    return counts


def _emit_json(results: list[Result], summary: dict[str, int]) -> Path:
    _SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = _SCORECARD_DIR / f"scorecard-{ts}.json"
    payload = {
        "run_id": ts,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="cve_remediation graph scorecard runner"
    )
    ap.add_argument(
        "--category", choices=["all", "basic", "fancy", "other"],
        default="all",
    )
    ap.add_argument(
        "--filter",
        help="substring or item id (e.g. 'F12', 'step10') to include",
    )
    ap.add_argument(
        "--skip", action="append", default=[],
        help="verifier stem (e.g. verify_F3_hitl_durability) to skip; repeatable",
    )
    ap.add_argument(
        "--timeout", type=int, default=_DEFAULT_TIMEOUT_S,
        help=f"per-verifier timeout in seconds (default {_DEFAULT_TIMEOUT_S})",
    )
    ap.add_argument(
        "--parallel", type=int, default=1,
        help="parallel worker count (default 1; verifiers can share live "
             "PDI/PG/CargoNet state, so >1 is best-effort)",
    )
    ap.add_argument(
        "--fail-fast", action="store_true",
        help="exit on first FAIL (skips remaining)",
    )
    ap.add_argument(
        "--json-only", action="store_true",
        help="suppress table output; emit JSON artifact path on stderr",
    )
    args = ap.parse_args()

    paths = _filter(_discover(), args)
    if not paths:
        print("no verifiers matched filter", file=sys.stderr)
        return 2

    results: list[Result] = []
    if args.parallel > 1 and not args.fail_fast:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=args.parallel
        ) as ex:
            futures = {
                ex.submit(_run_one, p, args.timeout): p for p in paths
            }
            for fut in concurrent.futures.as_completed(futures):
                results.append(fut.result())
    else:
        for p in paths:
            r = _run_one(p, args.timeout)
            results.append(r)
            if not args.json_only:
                marker = "." if r.status == "PASS" else (
                    "F" if r.status == "FAIL" else "s"
                )
                print(marker, end="", flush=True, file=sys.stderr)
            if args.fail_fast and r.status == "FAIL":
                break
    if not args.json_only:
        print(file=sys.stderr)

    summary = _summary(results)
    artifact = _emit_json(results, summary)

    if not args.json_only:
        _print_table(results)
        print(
            f"\n=== SUMMARY ===  PASS={summary['PASS']}  "
            f"FAIL={summary['FAIL']}  SKIP={summary['SKIP']}  "
            f"TOTAL={summary['TOTAL']}"
        )
        print(f"artifact: {artifact}")
    else:
        print(f"artifact: {artifact}", file=sys.stderr)

    return 0 if summary["FAIL"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
