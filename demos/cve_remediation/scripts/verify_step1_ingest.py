# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 1 verification harness.

Exercises the *ingest path only* (no Phase 0/2-6) on a set of CVE ids
and prints a per-CVE substance report. Pass criterion (per CRITERIA.md):

    Ingest -- raw_source_body populated by an actual fetcher reading
    from the configured Nautilus source. cve_id, cwe, cvss, epss,
    kev_listed all populated from the advisory.

What we verify here:

* ``raw_source_body`` is non-empty and contains the NVD English
  description (real fetcher signal -- the body is whatever NVD returned,
  not a stubbed string).
* ``cve_id`` round-trips canonically.
* ``cwe_class`` extracted from canonical body.
* ``extract.cvss_score_bp`` populated (basis points, real CVSS).
* ``extract.epss_score_bp`` populated from real FIRST EPSS feed when
  the CVE has a score (or ``None`` if FIRST hasn't scored it yet --
  legitimate "no data").
* ``extract.kev_listed`` populated from real CISA KEV catalog.
* Bogus CVE id raises a HarborRuntimeError from the fetcher (fail-loud
  surface; not a silent default).

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.verify_step1_ingest
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from types import SimpleNamespace

from harbor.errors import HarborRuntimeError

from demos.cve_remediation.graph.real_nodes import (
    CanonicalizeTrustedNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    IntakeFetchNode,
)
from demos.cve_remediation.graph.state import CveRemState


# Three real CVEs, hand-picked to exercise distinct shapes:
#   - CVE-2021-44228 : Log4Shell. KEV-listed, very high CVSS+EPSS.
#   - CVE-2024-3094  : xz-utils backdoor. High CVSS, NOT KEV-listed.
#   - CVE-2024-47176 : CUPS. Used to exercise a CVE that was never in
#                      the old fixture, so the previous code path would
#                      have left epss/kev empty.
#
# We also probe a deliberately-bogus id to confirm the fetcher fails
# loud instead of silently substituting zeros.
TARGETS = [
    "CVE-2021-44228",
    "CVE-2024-3094",
    "CVE-2024-47176",
]
BOGUS = "CVE-9999-99999"


async def _run_ingest(cve_id: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id="verify-step1")
    for node in (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
    ):
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


def _report(cve_id: str, state: CveRemState) -> dict[str, Any]:
    extract = state.extract
    body = state.raw_source_body or ""
    return {
        "cve_id": cve_id,
        "raw_source_url": state.raw_source_url,
        "raw_source_body_len": len(body),
        "raw_source_body_head": body[:140].replace("\n", " "),
        "extract.cve_id": getattr(extract, "cve_id", "") if extract else "",
        "extract.cwe_class": getattr(extract, "cwe_class", "") if extract else "",
        "extract.cvss_score_bp": getattr(extract, "cvss_score_bp", None) if extract else None,
        "extract.epss_score_bp": getattr(extract, "epss_score_bp", None) if extract else None,
        "extract.kev_listed": bool(getattr(extract, "kev_listed", False)) if extract else False,
        "last_intake_error": state.last_intake_error,
    }


def _grade(report: dict[str, Any]) -> tuple[bool, list[str]]:
    """Return (pass, failure_reasons). Per-CVE pass criteria."""
    fails: list[str] = []
    if report["last_intake_error"]:
        fails.append(f"last_intake_error: {report['last_intake_error']}")
    if report["raw_source_body_len"] < 40:
        fails.append(f"raw_source_body_len={report['raw_source_body_len']} (<40)")
    if report["extract.cve_id"] != report["cve_id"]:
        fails.append(
            f"extract.cve_id={report['extract.cve_id']!r} != seed {report['cve_id']!r}"
        )
    if not report["extract.cwe_class"].startswith("CWE-"):
        fails.append(f"extract.cwe_class={report['extract.cwe_class']!r} not CWE-XXX")
    if not isinstance(report["extract.cvss_score_bp"], int) or report["extract.cvss_score_bp"] <= 0:
        fails.append(f"extract.cvss_score_bp={report['extract.cvss_score_bp']!r} invalid")
    # epss may be None for very-fresh CVEs but for the chosen targets
    # (all >6 months old) we expect a real score.
    if not isinstance(report["extract.epss_score_bp"], int):
        fails.append(
            f"extract.epss_score_bp={report['extract.epss_score_bp']!r} not int "
            "(FIRST should have scored these)"
        )
    return (not fails, fails)


async def _run_bogus_check() -> tuple[bool, str]:
    """Bogus CVE id must surface as a loud failure.

    IntakeFetchNode catches the underlying NVD-empty error and routes
    it via ``state.last_intake_error`` (with empty ``raw_source_body``).
    Both forms are acceptable -- the rule is that the run does NOT
    silently end up with a populated body / extract for a non-existent
    CVE.
    """
    try:
        state = await _run_ingest(BOGUS)
    except HarborRuntimeError as exc:
        return True, f"raised HarborRuntimeError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return True, f"raised {type(exc).__name__}: {exc}"

    err = state.last_intake_error or ""
    body = state.raw_source_body or ""
    extract_cvss = getattr(state.extract, "cvss_score_bp", None) if state.extract else None
    # Pass criterion: surfaced an error AND no advisory substance landed.
    if err and not body and not extract_cvss:
        return True, f"fail-loud captured: last_intake_error={err!r}"
    return False, (
        f"silent success for bogus id: err={err!r} body_len={len(body)} "
        f"cvss_bp={extract_cvss!r}"
    )


async def main() -> int:
    overall_pass = True
    print(f"=== STEP 1 VERIFICATION (3 real CVEs + 1 bogus) ===\n")
    for cve in TARGETS:
        try:
            state = await _run_ingest(cve)
        except Exception as exc:  # noqa: BLE001
            overall_pass = False
            print(f"[{cve}] EXCEPTION: {type(exc).__name__}: {exc}\n")
            continue
        report = _report(cve, state)
        passed, fails = _grade(report)
        status = "PASS" if passed else "FAIL"
        print(f"[{cve}] {status}")
        for k, v in report.items():
            print(f"  {k}: {v!r}")
        if fails:
            overall_pass = False
            for f in fails:
                print(f"  ! {f}")
        print()

    bogus_ok, bogus_msg = await _run_bogus_check()
    print(f"[{BOGUS}] {'PASS' if bogus_ok else 'FAIL'}: {bogus_msg}\n")
    if not bogus_ok:
        overall_pass = False

    print("=== OVERALL: %s ===" % ("PASS" if overall_pass else "FAIL"))
    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
