# SPDX-License-Identifier: Apache-2.0
"""Retro failure-signal detection + LM-driven analysis.

For any CVE remediation run that didn't cleanly reach
``verify_outcome=patched`` with no upstream errors, this module
synthesizes a structured failure analysis: what failed, why, and
what concrete change reduces the likelihood of similar failures next
time.

Two-step:

  1. ``detect_failure_signals(state)`` -- pure state read.  Walks
     observable fields and emits zero or more :class:`RetroFailureSignal`
     entries.  No LM, no fabrication; every signal carries the field
     names + values that fired.

  2. ``lm_analyze_failures(...)`` -- LM call producing
     ``failure_analysis`` narrative + ``prevention_suggestions`` list.
     Strict citation guard: every suggestion must list the
     failure-signal kinds it grounds on; suggestions citing kinds not
     in the input are dropped.

Output mirrors the shape RemediationDiscoveryNode established
(structured JSON, citation-required, kind-bounded) so the same
no-cheats discipline applies to retrospective output as to discovery
output.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

# Allowed kinds (kept in sync with state.RetroFailureSignal docstring).
_FAILURE_KINDS = {
    "verify_unpatched",
    "sandbox_quarantined",
    "sandbox_skipped",
    "rollback",
    "no_fix_published",
    "planner_error",
    "sandbox_error",
    "correlation_error",
    "host_verify_failed",
    "verifier_finding",
    "capability_violation",
    "divergence",
    "intake_error",
    "cmdb_lookup_miss",
    "no_recommended_actions",
    "mitigation_invalid",
}

_PREVENTION_CATEGORIES = {
    "pipeline",
    "advisory_data",
    "sandbox",
    "planner",
    "dispatch",
    "hitl",
    "infrastructure",
    "retrospective_data",
}


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


def _str(state: Any, name: str, default: str = "") -> str:
    return str(getattr(state, name, default) or default)


def detect_failure_signals(state: Any) -> list[dict[str, Any]]:
    """Walk observable state and emit structured failure signals.

    Returns a list of ``{kind, detail, evidence}`` dicts (the LM
    pass converts these to ``RetroFailureSignal`` records via the
    state schema).  Empty list = clean run; caller skips LM analysis.
    """

    signals: list[dict[str, Any]] = []

    verify = _str(state, "verify_outcome")
    if verify and verify not in ("patched", ""):
        signals.append({
            "kind": (
                "divergence" if verify == "divergence"
                else "verify_unpatched"
            ),
            "detail": (
                f"verify_outcome={verify!r} -- post-deploy probe did "
                "not return 'patched'"
            ),
            "evidence": {"verify_outcome": verify},
        })

    sandbox_status = _str(state, "sandbox_status")
    sandbox = getattr(state, "sandbox", None)
    force_hitl = bool(getattr(sandbox, "force_hitl", False)) if sandbox else False
    skip_reason = (
        str(getattr(sandbox, "skip_reason", "") or "") if sandbox else ""
    )
    if sandbox_status == "quarantined":
        signals.append({
            "kind": "sandbox_quarantined",
            "detail": "sandbox 4-step probe disagreed with planner expectation",
            "evidence": {
                "sandbox_status": sandbox_status,
                "sandbox_quarantine_reason": _str(
                    state, "sandbox_quarantine_reason"
                ),
                "sandbox.force_hitl": force_hitl,
            },
        })
    elif sandbox_status == "skipped":
        signals.append({
            "kind": "sandbox_skipped",
            "detail": "sandbox probe did not run; HITL guidance only",
            "evidence": {
                "sandbox_status": sandbox_status,
                "sandbox.skip_reason": skip_reason,
                "sandbox.force_hitl": force_hitl,
            },
        })

    if bool(getattr(state, "rollback_triggered", False)):
        signals.append({
            "kind": "rollback",
            "detail": "rollback bundle was applied during execution",
            "evidence": {
                "rollback_triggered": True,
                "rollback_reason": _str(state, "rollback_reason"),
            },
        })

    vstatus = _str(state, "vulnerability_status")
    if vstatus in ("no_fix_published", "withdrawn"):
        signals.append({
            "kind": "no_fix_published",
            "detail": (
                f"advisory feeds reported no upstream fix "
                f"(vulnerability_status={vstatus!r})"
            ),
            "evidence": {
                "vulnerability_status": vstatus,
                "fixed_version": _str(state, "fixed_version"),
                "exact_affected_versions": list(
                    getattr(state, "exact_affected_versions", []) or []
                ),
            },
        })

    last_planner_err = _str(state, "last_planner_error")
    if last_planner_err:
        signals.append({
            "kind": "planner_error",
            "detail": f"planner LM path emitted error: {last_planner_err[:120]}",
            "evidence": {"last_planner_error": last_planner_err},
        })

    last_sandbox_err = _str(state, "last_sandbox_error")
    if last_sandbox_err:
        signals.append({
            "kind": "sandbox_error",
            "detail": f"sandbox harness emitted error: {last_sandbox_err[:120]}",
            "evidence": {"last_sandbox_error": last_sandbox_err},
        })

    last_intake_err = _str(state, "last_intake_error")
    if last_intake_err:
        signals.append({
            "kind": "intake_error",
            "detail": f"intake / enrichment error: {last_intake_err[:120]}",
            "evidence": {"last_intake_error": last_intake_err},
        })

    last_corr_err = _str(state, "last_correlation_error") or _str(
        state, "last_cmdb_error"
    )
    if last_corr_err:
        signals.append({
            "kind": "correlation_error",
            "detail": f"correlation/CMDB error: {last_corr_err[:120]}",
            "evidence": {"last_correlation_error": last_corr_err},
        })

    cmdb_match = _str(state, "cmdb_software_sys_id")
    if not cmdb_match:
        software_name = _str(state, "cmdb_software_name")
        signals.append({
            "kind": "cmdb_lookup_miss",
            "detail": (
                "no CMDB software CI matched the advisory product"
                + (
                    f" (cmdb_software_name={software_name!r})"
                    if software_name else ""
                )
            ),
            "evidence": {
                "cmdb_software_sys_id": cmdb_match,
                "cmdb_software_name": software_name,
                "cve_product": _str(state, "cve_product"),
                "candidate_products": list(
                    getattr(state, "candidate_products", []) or []
                ),
            },
        })

    per_host = list(getattr(state, "per_host_verify_results", []) or [])
    failed_hosts = [
        r for r in per_host
        if isinstance(r, dict) and not r.get("ok")
    ]
    if failed_hosts:
        signals.append({
            "kind": "host_verify_failed",
            "detail": (
                f"{len(failed_hosts)}/{len(per_host)} hosts did not "
                "report patched in post-deploy probe"
            ),
            "evidence": {
                "failed_host_count": len(failed_hosts),
                "total_hosts": len(per_host),
                "first_failure": failed_hosts[0],
            },
        })

    findings = list(
        getattr(state, "planner_verifier_findings", []) or []
    )
    if findings:
        signals.append({
            "kind": "verifier_finding",
            "detail": (
                f"planner rationale verifier raised "
                f"{len(findings)} finding(s)"
            ),
            "evidence": {
                "planner_verifier_findings": findings[:5],
                "planner_verifier_passed": bool(
                    getattr(state, "planner_verifier_passed", False)
                ),
            },
        })

    caps_err = _str(state, "last_capability_error")
    if caps_err:
        signals.append({
            "kind": "capability_violation",
            "detail": f"capability gate fired: {caps_err[:120]}",
            "evidence": {"last_capability_error": caps_err},
        })

    mitigation_only = bool(getattr(state, "mitigation_only", False))
    mitigation_probe_passed = bool(
        getattr(state, "mitigation_probe_passed", False)
    )
    if mitigation_only and not mitigation_probe_passed:
        signals.append({
            "kind": "mitigation_invalid",
            "detail": (
                "mitigation_only run failed structural validation probe; "
                "recommended mitigations are missing target / change / "
                "citation / confidence fields"
            ),
            "evidence": {
                "mitigation_only": mitigation_only,
                "mitigation_probe_passed": mitigation_probe_passed,
                "mitigation_probe_issues": list(
                    getattr(state, "mitigation_probe_issues", []) or []
                )[:10],
            },
        })

    recs = list(getattr(state, "recommended_actions", []) or [])
    if vstatus in ("no_fix_published", "withdrawn") and not recs:
        # Compounded signal: the upstream feed gave no fix AND the
        # discovery layer didn't surface anything actionable. Useful
        # for prevention because it points at either the discovery
        # source coverage or the LM extraction prompt.
        signals.append({
            "kind": "no_recommended_actions",
            "detail": (
                "RemediationDiscoveryNode produced zero "
                "recommended_actions despite upstream advisory "
                "indicating no published fix"
            ),
            "evidence": {
                "recommendation_provenance": (
                    getattr(state, "recommendation_provenance", None)
                    .model_dump()
                    if getattr(state, "recommendation_provenance", None)
                    is not None
                    else {}
                ),
            },
        })

    return signals


# ---------------------------------------------------------------------------
# LM analyzer
# ---------------------------------------------------------------------------


_ANALYZER_SYSTEM = (
    "You are the senior incident-response engineer authoring a CVE "
    "remediation retrospective. The pipeline has finished; you have "
    "ONE job: explain WHY the run did not cleanly reach 'patched', "
    "and propose CONCRETE pipeline / advisory / process changes that "
    "reduce the likelihood of similar failures next time. "
    "Output ONLY valid JSON matching the schema below. Do NOT add "
    "prose outside the JSON. Do NOT invent failure signals -- every "
    "suggestion MUST cite at least one signal kind from the provided "
    "failure_signals list (cited_signals[*]).\n\n"
    "Schema:\n"
    "{\n"
    '  "failure_analysis": "<150-500 word narrative explaining what '
    'failed and why, grounded on the observed signals>",\n'
    '  "prevention_suggestions": [\n'
    "    {\n"
    '      "category": "pipeline|advisory_data|sandbox|planner|'
    'dispatch|hitl|infrastructure|retrospective_data",\n'
    '      "suggestion": "<concrete actionable change, '
    'e.g. \\"add CWE-X to _CWE_TO_VULN_CLASS map\\" or \\"raise '
    'CVE_REM_AUTO_APPLY_BP from 7000 to 8000 for cipher-suite vuln '
    'class\\">",\n'
    '      "rationale": "<1-2 sentences why this prevents recurrence>",\n'
    '      "cited_signals": ["<signal.kind>", ...],\n'
    '      "confidence_bp": <int 0-10000>,\n'
    '      "citation_url": "<optional external citation -- '
    'advisory URL, file:line, doc URL>"\n'
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Quality bar:\n"
    "  - Each suggestion must be SPECIFIC enough that a human "
    "engineer could implement it without further ambiguity. "
    "Generic 'improve the planner' = bad. "
    "'Threshold the planner ReAct max_turns to 6 when KEV-listed' = good.\n"
    "  - Suggestions that just restate the failure ('don't fail next "
    "time') are not allowed.\n"
    "  - When proposing a pipeline change, point at the file/node "
    "you'd touch when known (e.g. real_nodes.py PlannerNode, "
    "fetch_advisory.py).\n"
    "  - confidence_bp scoring: 9000+ = action directly addresses "
    "the cited signal's root cause; 7000+ = reasonable hypothesis; "
    "<5000 = speculative."
)


async def lm_analyze_failures(
    *,
    cve_id: str,
    cwe: str,
    failure_signals: list[dict[str, Any]],
    state_excerpt: dict[str, Any],
    lm_url: str = "",
    lm_model: str = "",
    lm_api_key: str = "",
    timeout_s: float = 45.0,
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Returns ``(narrative, suggestions_list, diagnostics)``."""

    diag: dict[str, Any] = {
        "lm_suggestions_emitted": 0,
        "lm_suggestions_dropped_no_citation": 0,
        "last_error": "",
    }
    if not failure_signals:
        return "", [], diag

    lm_url = (lm_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
    lm_model = lm_model or os.environ.get("LLM_MODEL", "")
    lm_api_key = (
        lm_api_key
        or os.environ.get("LLM_API_KEY", "placeholder")
        or "placeholder"
    )
    if not lm_url or not lm_model:
        diag["last_error"] = "LLM_BASE_URL or LLM_MODEL unset"
        return "", [], diag

    valid_kinds = {s.get("kind", "") for s in failure_signals if s.get("kind")}

    user_brief = json.dumps(
        {
            "cve_id": cve_id,
            "cwe": cwe,
            "failure_signals": failure_signals,
            "state_excerpt": state_excerpt,
        },
        indent=2,
        default=str,
    )[:9000]

    body = {
        "model": lm_model,
        "messages": [
            {"role": "system", "content": _ANALYZER_SYSTEM},
            {
                "role": "user",
                "content": (
                    "Analyze the following retrospective input and "
                    "return the JSON envelope:\n\n" + user_brief
                ),
            },
        ],
        "temperature": 0.0,
        "max_tokens": 2200,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {lm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(
                f"{lm_url}/chat/completions", json=body, headers=headers
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        diag["last_error"] = f"http: {type(exc).__name__}: {exc}"
        return "", [], diag

    content = ""
    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        diag["last_error"] = "lm response missing choices[0].message.content"
        return "", [], diag
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        envelope = json.loads(content)
    except ValueError as exc:
        diag["last_error"] = f"json parse: {exc}"
        return "", [], diag

    narrative = str(envelope.get("failure_analysis", "") or "").strip()[:6000]
    raw_suggestions = envelope.get("prevention_suggestions") or []
    if not isinstance(raw_suggestions, list):
        diag["last_error"] = "prevention_suggestions not a list"
        return narrative, [], diag

    cleaned: list[dict[str, Any]] = []
    for s in raw_suggestions:
        if not isinstance(s, dict):
            continue
        category = str(s.get("category", "") or "").strip().lower()
        cited = s.get("cited_signals") or []
        if not isinstance(cited, list):
            cited = []
        cited = [str(c) for c in cited if isinstance(c, str)]
        # Drop suggestions citing nothing or citing kinds we didn't observe.
        if not cited or not all(c in valid_kinds for c in cited):
            diag["lm_suggestions_dropped_no_citation"] += 1
            continue
        if category not in _PREVENTION_CATEGORIES:
            diag["lm_suggestions_dropped_no_citation"] += 1
            continue
        confidence = s.get("confidence_bp", 0)
        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            confidence = 0
        confidence = max(0, min(10000, confidence))
        cleaned.append({
            "category": category,
            "suggestion": str(s.get("suggestion", "") or "").strip()[:480],
            "rationale": str(s.get("rationale", "") or "").strip()[:480],
            "cited_signals": cited,
            "confidence_bp": confidence,
            "citation_url": str(s.get("citation_url", "") or "").strip()[:480],
        })
    diag["lm_suggestions_emitted"] = len(cleaned)
    return narrative, cleaned, diag


__all__ = [
    "detect_failure_signals",
    "lm_analyze_failures",
]
