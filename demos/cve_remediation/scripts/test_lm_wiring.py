#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Acceptance test: LM-emitted bundle drives host execution.

Asserts the pipeline actually USES CodeWriterNode's output. Does NOT
mock anything. Runs a single CVE end-to-end via ``score_run.py``
(real ``harbor serve``) and inspects the resulting JSONL state to
detect theater patterns.

Theater detection rules (any rule failing → test fails):

  R1. ``state.bundle.apply_bundle_ref`` MUST be a non-empty
      ``file://`` URL pointing to a parseable Ansible playbook YAML
      with at least 2 tasks AND not the deterministic stub.

  R2. For every entry in ``state.per_host_apply_results``, the
      ``install_command`` (or equivalent recorded apply command)
      MUST NOT match any hardcoded legacy patterns:
        - ``pip install ...`` / ``apt-get install ...`` /
          ``yum install ...`` / ``npm install ...``
        - the static_detection ``firmware probe`` fallback
      The recorded command MUST be derivable from the LM-emitted
      playbook tasks.

  R3. ``state.bundle.verify_probe_ref`` MUST be a non-empty
      reference, AND ``state.verify_probe_method`` MUST NOT equal
      ``"cargonet"`` or ``"firmware"`` (legacy hardcoded probes).

  R4. At least one host in ``per_host_apply_results`` MUST have
      ``ok=True`` AND a non-empty ``evidence`` field, indicating the
      LM-derived apply ran AND verify confirmed.

Usage::

    uv run python demos/cve_remediation/scripts/test_lm_wiring.py \
        --cve CVE-2024-21893 \
        --serve-base http://127.0.0.1:9001

Exit: 0 = PASS, 1 = FAIL (theater detected), 2 = infrastructure error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


_DEMO_ROOT = Path(__file__).resolve().parent.parent
_SCORE_RUN = _DEMO_ROOT / "scripts" / "score_run.py"

_HARDCODED_APPLY_PATTERNS = (
    r"\bpip\s+install\b",
    r"\bapt-get\s+install\b",
    r"\byum\s+install\b",
    r"\bdnf\s+install\b",
    r"\bnpm\s+install\b",
    r"^firmware\s+probe$",
)
_HARDCODED_VERIFY_METHODS = (
    "cargonet",  # legacy per-host probe (hardcoded pip show / show version)
    "firmware",  # static_detection fallback
)


def _matches_hardcoded(cmd: str) -> str | None:
    if not cmd:
        return None
    for pat in _HARDCODED_APPLY_PATTERNS:
        if re.search(pat, cmd, re.IGNORECASE):
            return pat
    return None


def _run_score_run(cve: str, serve_base: str, artifacts_root: Path,
                   timeout_s: int) -> Path:
    """Spawn score_run for a single CVE; return path to the resulting JSONL."""
    artifacts_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["HARBOR_ARTIFACTS_ROOT"] = str(artifacts_root)
    cmd = [
        "uv", "run", "--no-project", "python",
        str(_SCORE_RUN),
        "--cve", cve,
        "--serve-base", serve_base,
        "--timeout", str(timeout_s),
    ]
    print(f"  running: {' '.join(cmd)}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True,
                          timeout=timeout_s + 60)
    if proc.returncode != 0:
        raise RuntimeError(
            f"score_run failed (rc={proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout[-2000:]}\n\n"
            f"STDERR:\n{proc.stderr[-2000:]}"
        )
    # score_run writes to <HARBOR_ARTIFACTS_ROOT>/scorecard/run_<ts>.jsonl
    jsonls = sorted((artifacts_root / "scorecard").glob("run_*.jsonl"))
    if not jsonls:
        raise RuntimeError(f"no JSONL produced under {artifacts_root}")
    return jsonls[-1]


def _load_state(jsonl: Path, cve: str) -> dict[str, Any]:
    with jsonl.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("cve_id") == cve:
                return row.get("outcome") or row
    raise RuntimeError(f"no row for {cve} in {jsonl}")


def _check_r1_bundle(state: dict[str, Any]) -> tuple[bool, str]:
    bundle = state.get("bundle") or {}
    apply_ref = str(bundle.get("apply_bundle_ref") or "")
    if not apply_ref:
        return False, "bundle.apply_bundle_ref is empty"
    if not apply_ref.startswith("file://"):
        return False, f"apply_bundle_ref is not file://: {apply_ref!r}"
    path = Path(apply_ref.removeprefix("file://"))
    if not path.is_file():
        return False, f"apply_bundle_ref path missing: {path}"
    try:
        import yaml as _yaml
        parsed = _yaml.safe_load(path.read_text())
    except Exception as exc:
        return False, f"apply playbook not valid YAML: {exc}"
    if not isinstance(parsed, list) or not parsed:
        return False, f"apply playbook is not a list-of-plays"
    tasks = parsed[0].get("tasks") if isinstance(parsed[0], dict) else None
    if not tasks or len(tasks) < 2:
        return False, f"apply playbook has <2 tasks: {len(tasks or [])}"
    body = path.read_text()
    stub_markers = ("deterministic stub", "stub fallback", "TODO: real")
    if any(m in body for m in stub_markers):
        return False, "apply playbook is a stub fallback"
    return True, f"R1 ok: {len(tasks)} tasks in {path.name}"


def _check_r2_apply_commands(state: dict[str, Any]) -> tuple[bool, str]:
    rows = state.get("per_host_apply_results") or []
    if not isinstance(rows, list) or not rows:
        return False, "per_host_apply_results empty (no apply traced)"
    hardcoded_hits: list[str] = []
    derived_hits: list[str] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        cmd = str(
            r.get("install_command")
            or r.get("apply_command")
            or r.get("install_cmd")
            or ""
        )
        m = _matches_hardcoded(cmd)
        if m:
            hardcoded_hits.append(f"{r.get('host','?')}: matches {m!r}")
        elif cmd:
            derived_hits.append(f"{r.get('host','?')}: {cmd[:60]}")
    if hardcoded_hits:
        return False, (
            "hardcoded apply commands detected:\n      "
            + "\n      ".join(hardcoded_hits)
        )
    if not derived_hits:
        return False, "no apply commands recorded"
    return True, f"R2 ok: {len(derived_hits)} apply commands, none hardcoded"


def _check_r3_verify(state: dict[str, Any]) -> tuple[bool, str]:
    bundle = state.get("bundle") or {}
    verify_ref = str(bundle.get("verify_probe_ref") or "")
    if not verify_ref:
        return False, "bundle.verify_probe_ref is empty"
    method = str(state.get("verify_probe_method") or "")
    if method in _HARDCODED_VERIFY_METHODS:
        return False, (
            f"verify_probe_method={method!r} is a legacy hardcoded "
            f"probe; LM verify_probe_ref={verify_ref!r} not used"
        )
    if not method:
        return False, "verify_probe_method unset"
    return True, f"R3 ok: method={method!r} ref={verify_ref!r}"


def _check_r4_evidence(state: dict[str, Any]) -> tuple[bool, str]:
    rows = state.get("per_host_apply_results") or []
    if not rows:
        return False, "no host results to inspect"
    ok_with_evidence = [
        r for r in rows
        if isinstance(r, dict) and r.get("ok") and r.get("evidence")
    ]
    if not ok_with_evidence:
        return False, (
            "no host shows ok=true with evidence string; either apply "
            "did not run or evidence is empty"
        )
    return True, f"R4 ok: {len(ok_with_evidence)} host(s) with evidence"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cve", default="CVE-2024-21893")
    ap.add_argument("--serve-base", default="http://127.0.0.1:9001")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--artifacts-root", default="")
    args = ap.parse_args()

    print(f"=== LM wiring acceptance test ===")
    print(f"  cve:      {args.cve}")
    print(f"  serve:    {args.serve_base}")
    print(f"  timeout:  {args.timeout}s")

    if args.artifacts_root:
        artifacts_root = Path(args.artifacts_root)
    else:
        artifacts_root = Path(tempfile.mkdtemp(prefix="lm-wiring-"))
    print(f"  out:      {artifacts_root}\n")

    try:
        jsonl = _run_score_run(args.cve, args.serve_base,
                               artifacts_root, args.timeout)
    except Exception as exc:
        print(f"! score_run failed: {exc}", file=sys.stderr)
        return 2

    print(f"\n  jsonl:    {jsonl}\n")
    try:
        state = _load_state(jsonl, args.cve)
    except Exception as exc:
        print(f"! could not load state: {exc}", file=sys.stderr)
        return 2

    rules = [
        ("R1 bundle",   _check_r1_bundle),
        ("R2 apply",    _check_r2_apply_commands),
        ("R3 verify",   _check_r3_verify),
        ("R4 evidence", _check_r4_evidence),
    ]
    print("=== rule results ===")
    failed = 0
    for name, fn in rules:
        ok, msg = fn(state)
        marker = "PASS" if ok else "FAIL"
        print(f"  [{marker}] {name}: {msg}")
        if not ok:
            failed += 1
    print()
    if failed:
        print(f"=== TEST FAILED: {failed} rule(s) detected theater ===")
        return 1
    print("=== TEST PASSED: LM bundle wired through executor ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
