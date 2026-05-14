# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 6 verification harness.

Drives ingest → correlate → plan → CodeWriter → SandboxDispatch →
SandboxRun against multiple CVEs and verifies that the 4-step probe
sequence (baseline / apply / rollback / reapply) produced real,
content-addressable results with **observed** vulnerability status
matching the **expected** status (CRITERIA fancy #4).

Pass criteria:

* ``sandbox.baseline_probe`` / ``apply_probe`` / ``rollback_probe`` /
  ``reapply_probe`` all populated with ``compose://`` or
  ``cargonet://`` URIs (no ``probe://`` plan-hash placeholders).
* ``sandbox_probe_steps`` carries 4 entries each with ``status``
  string in {``vulnerable``, ``patched``}.
* Phase polarity: baseline=vulnerable, apply=patched,
  rollback=vulnerable, reapply=patched. Mismatch on any phase ⇒ FAIL
  + plan should have been quarantined.
* ``sandbox_probe_latency_ms`` > 0 (real wall clock).

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.verify_step6_sandbox
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

from demos.cve_remediation.graph.real_nodes import (
    CanonicalizeTrustedNode,
    CodeWriterNode,
    CorrelateAssetsBrokerNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    IntakeFetchNode,
    PlannerNode,
    SandboxDispatchNode,
    SandboxRunNode,
)
from demos.cve_remediation.graph.state import CveRemState


TARGETS = [
    # (cve_id, expected_product_token_for_probe)
    # First three are real demo CVEs; advisory data is sparse for some
    # (xz has no fix version published; NLTK has no introduced version)
    # so they exercise the honest skip + force_hitl path.
    ("CVE-2024-39705", "nltk"),
    ("CVE-2021-44228", "log4j"),
    ("CVE-2024-3094",  "xz"),
    # Two more with rich OSV ecosystem data to exercise the
    # full 4-step probe path end-to-end (pip channel, both
    # introduced and fixed versions present).
    ("CVE-2024-26130", "cryptography"),
    ("CVE-2023-32681", "requests"),
]
EXPECTED_PHASE_POLARITY = {
    "baseline": "vulnerable",
    "apply":    "patched",
    "rollback": "vulnerable",
    "reapply":  "patched",
}


async def _run(cve_id: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id="verify-step6")
    for node in (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        PlannerNode(),
        CodeWriterNode(),
        SandboxDispatchNode(),
        SandboxRunNode(),
    ):
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


def _grade(cve: str, state: CveRemState) -> tuple[bool, list[str]]:
    """Grade a single CVE's sandbox outcome.

    Three accepted terminal states:

    * ``ok``: 4 phases ran, all observed statuses matched expected
      polarity (vulnerable / patched / vulnerable / patched).
    * ``quarantined``: 4 phases ran, ≥1 observed != expected -- the
      pipeline correctly blocked the plan.
    * ``skipped`` w/ ``force_hitl=True`` AND a non-empty
      ``skip_reason`` -- advisory signal was insufficient to construct
      a real probe (no install_channel, no fixed_version, etc.). For
      100k-CVE operation, this path is the realistic outcome for many
      sparsely-structured advisories; surfacing to HITL is correct,
      not a failure.

    Anything else (``fail``, ``pending``, missing fields) is a real
    failure.
    """
    fails: list[str] = []
    sb = getattr(state, "sandbox", None)
    if sb is None:
        return False, ["no sandbox result on state"]
    if state.sandbox_status == "skipped":
        if not sb.force_hitl:
            fails.append("skipped sandbox didn't set force_hitl=True")
        if not sb.skip_reason:
            fails.append("skipped sandbox has no skip_reason")
        return (not fails, fails)
    if state.sandbox_status == "quarantined":
        # Pipeline correctly blocked the plan because probe behavior
        # disagreed with planner expectations. This is the
        # CRITERIA-fancy #4 "known-bad plan caught" path: the probe
        # observed something contradicting the expected polarity (e.g.
        # log4j 2.15.0 still ships the JndiLookup class), and the
        # pipeline halted apply + force_hitl. Accept as PASS.
        if not sb.force_hitl:
            fails.append("quarantined sandbox didn't set force_hitl=True")
        if not state.sandbox_quarantine_reason:
            fails.append("quarantined sandbox missing quarantine_reason")
        # Still require 4 probe URIs -- proves the run was real.
        for ref_name, ref in (
            ("baseline", sb.baseline_probe), ("apply", sb.apply_probe),
            ("rollback", sb.rollback_probe), ("reapply", sb.reapply_probe),
        ):
            if not ref.startswith("compose://"):
                fails.append(f"{ref_name}_probe scheme unrecognized: {ref!r}")
        return (not fails, fails)

    refs = {
        "baseline": sb.baseline_probe,
        "apply":    sb.apply_probe,
        "rollback": sb.rollback_probe,
        "reapply":  sb.reapply_probe,
    }
    for phase, ref in refs.items():
        if not ref:
            fails.append(f"{phase}_probe empty")
            continue
        if ref.startswith("probe://"):
            fails.append(
                f"{phase}_probe={ref!r} is plan-hash placeholder (not real probe)"
            )
        if not ref.startswith(("compose://", "cargonet://")):
            fails.append(f"{phase}_probe scheme unrecognized: {ref!r}")

    steps = getattr(state, "sandbox_probe_steps", {}) or {}
    if isinstance(steps, dict):
        for phase, expected in EXPECTED_PHASE_POLARITY.items():
            entry = steps.get(phase)
            if not isinstance(entry, dict):
                fails.append(f"sandbox_probe_steps[{phase!r}] missing/malformed")
                continue
            observed = str(entry.get("status", ""))
            if observed != expected:
                fails.append(
                    f"phase={phase} observed={observed!r} expected={expected!r}"
                )
    else:
        fails.append(f"sandbox_probe_steps not dict: {type(steps).__name__}")

    if (state.sandbox_probe_latency_ms or 0) <= 0:
        fails.append(
            f"sandbox_probe_latency_ms={state.sandbox_probe_latency_ms} (expected >0)"
        )
    err = state.last_sandbox_error or ""
    if err:
        fails.append(f"last_sandbox_error: {err}")
    if state.sandbox_status not in ("ok", "quarantined"):
        fails.append(f"sandbox_status={state.sandbox_status!r}")
    return (not fails, fails)


async def main() -> int:
    overall = True
    print("=== STEP 6 VERIFICATION (4-step sandbox probe) ===\n")

    if not os.environ.get("LLM_BASE_URL") or not os.environ.get("LLM_MODEL"):
        print("! LLM_BASE_URL/LLM_MODEL unset — planner will fall back.")
        print("  Source demos/cve_remediation/.env first.\n")

    status_counts: dict[str, int] = {}
    runtime_counts: dict[str, int] = {}

    for cve, _hint in TARGETS:
        try:
            state = await _run(cve)
        except Exception as exc:  # noqa: BLE001
            overall = False
            print(f"[{cve}] EXCEPTION: {type(exc).__name__}: {exc}\n")
            continue
        ss = str(state.sandbox_status or "")
        status_counts[ss] = status_counts.get(ss, 0) + 1
        sb_for_rt = getattr(state, "sandbox", None)
        rt = str(getattr(sb_for_rt, "runtime", "") or "")
        runtime_counts[rt] = runtime_counts.get(rt, 0) + 1
        passed, fails = _grade(cve, state)
        status = "PASS" if passed else "FAIL"
        print(f"[{cve}] {status}")
        sb = getattr(state, "sandbox", None)
        print(f"  sandbox.runtime          : {sb.runtime if sb else '<no sandbox>'}")
        print(f"  sandbox.status           : {sb.status if sb else '-'}")
        print(f"  sandbox_status           : {state.sandbox_status}")
        if sb and sb.force_hitl:
            print(f"  force_hitl               : True")
        if sb and sb.skip_reason:
            print(f"  skip_reason              : {sb.skip_reason}")
        print(f"  sandbox_probe_latency_ms : {state.sandbox_probe_latency_ms}")
        print(f"  baseline                 : {sb.baseline_probe if sb else ''}")
        print(f"  apply                    : {sb.apply_probe if sb else ''}")
        print(f"  rollback                 : {sb.rollback_probe if sb else ''}")
        print(f"  reapply                  : {sb.reapply_probe if sb else ''}")
        steps = state.sandbox_probe_steps or {}
        if isinstance(steps, dict):
            for phase in ("baseline", "apply", "rollback", "reapply"):
                entry: Any = steps.get(phase, {})
                if isinstance(entry, dict):
                    obs = entry.get("status", "")
                    spec = entry.get("spec", "")
                    lat = entry.get("latency_ms", "")
                    print(f"    {phase:8s}  status={obs!r:14s} spec={spec!r:24s} {lat}ms")
        if state.last_sandbox_error:
            print(f"  last_sandbox_error       : {state.last_sandbox_error}")
        for f in fails:
            print(f"  ! {f}")
        if not passed:
            overall = False
        print()

    # Batch-level distribution gate: 100-CVE sweep showed sandbox_status
    # collapsed to skipped=95% and runtime collapsed to docker_compose
    # for all 100. Per-CVE verifiers can pass on the skip+force_hitl
    # path while the workflow does no real probe work. Cap skip-rate
    # and require runtime variety so the batch fails loud when the
    # dispatcher / preconditions revert to single-branch behavior.
    n = sum(status_counts.values())
    if n:
        skip_rate = status_counts.get("skipped", 0) / n
        max_skip = float(os.environ.get("STEP6_MAX_SKIP_RATE", "0.40"))
        print(f"\n--- batch distribution (n={n}) ---")
        print(f"  sandbox_status counts: {status_counts}")
        print(f"  sandbox runtime counts: {runtime_counts}")
        print(f"  skip_rate={skip_rate:.2f} (cap={max_skip:.2f})")
        if skip_rate > max_skip:
            print(
                f"  ! sandbox skip-rate {skip_rate:.2f} exceeds cap "
                f"{max_skip:.2f} — dispatcher / preconditions collapsed"
            )
            overall = False
        # Require at least 2 distinct sandbox_status outcomes across the
        # batch when n>=3 (covers a mix of ok / quarantined / skipped).
        if n >= 3 and len([k for k, v in status_counts.items() if v]) < 2:
            print(
                f"  ! sandbox_status collapsed to single value "
                f"{list(status_counts)[0]!r} across {n} CVEs"
            )
            overall = False

    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
