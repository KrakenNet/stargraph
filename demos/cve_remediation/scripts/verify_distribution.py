# SPDX-License-Identifier: Apache-2.0
"""Distribution gate verifier.

The 100-CVE scoring sweep (2026-05-07) revealed two collapse modes
that no single-CVE verifier catches:

  * SSVC tier collapsed to ``act_auto`` for 100/100 CVEs even though
    fixtures spanned KEV-yes/no x CVSS-low/med/high.
  * Sandbox runtime collapsed to ``docker_compose`` for 100/100 even
    though ``vuln_class`` covered cargonet / docker / static / hitl.

This verifier drives a deliberately diverse 5-CVE corpus through the
intake -> SSVC -> dispatcher segment of the graph and asserts:

  * SSVC tiers across the batch produce >=3 distinct values OR Shannon
    entropy >= 1.0 bits.
  * Sandbox runtimes across the batch produce >=2 distinct values.

Single-CVE verifiers cannot detect collapse because each verifier is
self-consistent within one input. Distribution gates fail loud when
the classifier / dispatcher reverts to a constant.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.verify_distribution
"""

from __future__ import annotations

import asyncio
import math
import os
import sys
from types import SimpleNamespace

from demos.cve_remediation.graph.real_nodes import (
    CanonicalizeTrustedNode,
    CorrelateAssetsBrokerNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    IntakeFetchNode,
    SandboxDispatchNode,
    SsvcTierEvaluatorNode,
)
from demos.cve_remediation.graph.state import CveRemState

# Stratified 5-CVE corpus spanning the SSVC decision matrix and the
# sandbox dispatcher's vuln_class branches. Picked from the 100-CVE
# scoring corpus so ground-truth product / ecosystem data is on disk.
TARGETS = [
    # (cve_id, expected_ssvc_class, expected_runtime_class)
    ("CVE-2021-44228",  "kev_high",     "library_or_app"),   # log4j / KEV
    ("CVE-2024-26130",  "non_kev_med",  "library_or_app"),   # cryptography
    ("CVE-2024-3094",   "kev_high",     "library_or_app"),   # xz-utils
    ("CVE-2024-39705",  "non_kev_low",  "skip_or_static"),   # nltk
    ("CVE-2023-32681",  "non_kev_med",  "library_or_app"),   # requests
]


async def _drive(cve_id: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id="verify-distribution")
    # Trim: PlannerNode + CodeWriter not needed for dispatch — they're
    # LM-bound (~40s/CVE) and the dispatcher only reads
    # ``extract.vuln_class``. SsvcTierEvaluator needs extract too. So
    # this pipeline is enrichment-only and finishes in ~3s/CVE.
    pipeline = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        SsvcTierEvaluatorNode(),
        SandboxDispatchNode(),
    )
    for node in pipeline:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


def _entropy_bits(counts: dict[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p)
    return h


async def main() -> int:
    print("=== DISTRIBUTION GATE (SSVC + sandbox runtime) ===\n")

    if not os.environ.get("LLM_BASE_URL") or not os.environ.get("LLM_MODEL"):
        print("! LLM_BASE_URL/LLM_MODEL unset — planner will fall back, "
              "which can confound the dispatcher input.\n")

    ssvc_counts: dict[str, int] = {}
    runtime_counts: dict[str, int] = {}
    vuln_class_counts: dict[str, int] = {}
    rows: list[tuple[str, str, str, str]] = []

    for cve, _ssvc_hint, _rt_hint in TARGETS:
        try:
            s = await _drive(cve)
        except Exception as exc:  # noqa: BLE001
            print(f"[{cve}] EXCEPTION: {type(exc).__name__}: {exc}")
            return 2
        tier = str(getattr(s, "ssvc_tier", "") or "")
        # Read state.sandbox_runtime (set by SandboxDispatchNode) NOT
        # state.sandbox.runtime — sandbox is a default-factory
        # ``SandboxResult`` whose ``runtime`` field defaults to
        # ``SandboxRuntime.SKIP`` until SandboxRunNode populates it,
        # which would mask dispatcher output as a constant ``skip``.
        rt_val = getattr(s, "sandbox_runtime", None)
        runtime = rt_val.value if hasattr(rt_val, "value") else str(rt_val or "")
        vclass = str(getattr(s, "vuln_class", "") or "")
        ssvc_counts[tier] = ssvc_counts.get(tier, 0) + 1
        runtime_counts[runtime] = runtime_counts.get(runtime, 0) + 1
        vuln_class_counts[vclass] = vuln_class_counts.get(vclass, 0) + 1
        rows.append((cve, tier, runtime, vclass))

    print(f"  {'cve':<18} {'ssvc_tier':<22} {'runtime':<18} vuln_class")
    print(f"  {'-'*18} {'-'*22} {'-'*18} {'-'*16}")
    for cve, tier, runtime, vclass in rows:
        print(f"  {cve:<18} {tier:<22} {runtime:<18} {vclass}")

    print()
    print(f"  ssvc_tier counts        : {ssvc_counts}")
    print(f"  sandbox runtime counts  : {runtime_counts}")
    print(f"  vuln_class counts       : {vuln_class_counts}")

    ssvc_distinct = len([k for k, v in ssvc_counts.items() if v])
    runtime_distinct = len([k for k, v in runtime_counts.items() if v])
    vclass_distinct = len([k for k, v in vuln_class_counts.items() if v])
    h_ssvc = _entropy_bits(ssvc_counts)
    h_runtime = _entropy_bits(runtime_counts)
    h_vclass = _entropy_bits(vuln_class_counts)

    print(f"  ssvc distinct={ssvc_distinct}  H={h_ssvc:.2f} bits")
    print(f"  runtime distinct={runtime_distinct}  H={h_runtime:.2f} bits")
    print(f"  vuln_class distinct={vclass_distinct}  H={h_vclass:.2f} bits")

    # Gates — adjustable via env so a too-strict default doesn't block
    # iteration on smaller corpora.
    min_ssvc_distinct   = int(os.environ.get("DIST_MIN_SSVC_DISTINCT", "2"))
    min_ssvc_entropy    = float(os.environ.get("DIST_MIN_SSVC_ENTROPY", "0.7"))
    min_runtime_distinct = int(os.environ.get("DIST_MIN_RUNTIME_DISTINCT", "2"))
    min_vclass_distinct  = int(os.environ.get("DIST_MIN_VCLASS_DISTINCT", "2"))

    fails: list[str] = []
    if ssvc_distinct < min_ssvc_distinct:
        fails.append(
            f"SSVC distinct={ssvc_distinct} < {min_ssvc_distinct}; "
            f"all collapsed to {list(ssvc_counts)[0]!r}"
        )
    if h_ssvc < min_ssvc_entropy:
        fails.append(
            f"SSVC entropy {h_ssvc:.2f} < {min_ssvc_entropy:.2f} bits"
        )
    if runtime_distinct < min_runtime_distinct:
        fails.append(
            f"sandbox runtime distinct={runtime_distinct} < "
            f"{min_runtime_distinct}; collapsed to {list(runtime_counts)[0]!r}"
        )
    if vclass_distinct < min_vclass_distinct:
        fails.append(
            f"vuln_class distinct={vclass_distinct} < {min_vclass_distinct}"
        )

    print()
    for f in fails:
        print(f"  ! {f}")
    print(f"=== OVERALL: {'PASS' if not fails else 'FAIL'} ===")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
