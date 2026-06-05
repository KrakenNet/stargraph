# SPDX-License-Identifier: Apache-2.0
"""DSPy signatures and modules for CVE remediation pipeline.

Replaces raw httpx ``/chat/completions`` calls with DSPy
Signatures + ChainOfThought / Predict / ReAct modules. Falls back
gracefully when DSPy or LLM is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

try:
    from harbor.logging import get_logger
    log = get_logger("cve_rem.dspy")
except Exception:
    import logging
    log = logging.getLogger("cve_rem.dspy")

# ---------------------------------------------------------------------------
# Lazy DSPy import + LM configuration
# ---------------------------------------------------------------------------

DSPY_AVAILABLE = False
_LM_CONFIGURED = False

try:
    import dspy
    DSPY_AVAILABLE = True
except ImportError:
    dspy = None  # type: ignore[assignment]

_VULN_CLASS_ENUM = frozenset({
    "web-framework", "library", "application", "host",
    "cipher-suite", "config-only", "acl-rule", "logic-flaw",
})


def _get_policy() -> dict[str, Any]:
    try:
        from demos.cve_remediation.graph.real_nodes import POLICY
        return POLICY
    except Exception:
        return {}


def _ensure_lm() -> bool:
    """Configure DSPy LM from env vars. Idempotent."""
    global _LM_CONFIGURED
    if _LM_CONFIGURED:
        return True
    if not DSPY_AVAILABLE:
        return False

    base_url = os.environ.get("LLM_BASE_URL", "").strip()
    model = os.environ.get("LLM_MODEL", "").strip()
    api_key = os.environ.get("LLM_API_KEY", "placeholder").strip() or "placeholder"
    if not base_url or not model:
        return False

    llm_cfg = _get_policy().get("llm", {})
    timeout = float(
        os.environ.get("LLM_TIMEOUT_SECONDS", str(llm_cfg.get("timeout_seconds", 30))) or "30"
    )

    try:
        dspy.configure(lm=dspy.LM(
            f"openai/{model}",
            api_base=base_url,
            api_key=api_key,
            timeout=timeout,
            temperature=0.0,
        ))
        _LM_CONFIGURED = True
        return True
    except Exception as exc:
        log.warning("dspy_lm_configure_failed", error=str(exc))
        return False


# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------

if DSPY_AVAILABLE:

    class VulnClassifySignature(dspy.Signature):
        """Classify a CVE into one vuln_class for sandbox dispatcher routing.

        Pick based on what KIND of static probe reveals whether a host is patched:
        web-framework (HTTP probe), library (dep-version check), application
        (app-behaviour/RCE probe), host (OS/kernel/binary version), cipher-suite
        (TLS handshake), config-only (config-file diff), acl-rule (permission check),
        logic-flaw (no static probe, HITL only).
        """

        cve_id: str = dspy.InputField(desc="CVE identifier, e.g. CVE-2021-44228")
        cwe_class: str = dspy.InputField(desc="CWE classification if known, else empty")
        description: str = dspy.InputField(desc="Advisory description text (truncated to 1800 chars)")
        vuln_class: str = dspy.OutputField(
            desc="Exactly one of: web-framework, library, application, host, "
                 "cipher-suite, config-only, acl-rule, logic-flaw"
        )

    class RationaleSignature(dspy.Signature):
        """Generate CVE remediation rationale for an automated change request.

        Be concrete and specific to THIS CVE. Never write generic boilerplate.
        Cover: (1) what specific behavior causes the vulnerability, (2) which
        version(s) close it, (3) rollout strategy, (4) sandbox probe assertion
        pre-vs-post, (5) rollback condition. Cite sources as [CITE: n].
        """

        cve_facts: str = dspy.InputField(desc="CVE ID, CWE, vuln_class, CVSS, KEV, products, versions")
        advisory_text: str = dspy.InputField(desc="Verbatim advisory text from NVD")
        references: str = dspy.InputField(desc="Reference URLs")
        discovered_hosts: str = dspy.InputField(desc="Affected host names from CMDB")
        prior_context: str = dspy.InputField(desc="Prior remediation stats and retro lessons")
        remediation_actions: str = dspy.InputField(desc="Discovered remediation actions with citations")
        rag_sources: str = dspy.InputField(desc="Authoritative sources for citation")
        rationale: str = dspy.OutputField(desc="4-6 sentence remediation rationale")

    class PlannerAgentSignature(dspy.Signature):
        """Draft CVE remediation rationale using tools to gather facts.

        You are a senior security engineer. Use the available tools to look up
        prior retrospectives, governance controls, advisory sections, and host
        topology. Then produce a 4-6 sentence rationale covering: vulnerability
        mechanism, fixed version, rollout strategy, sandbox assertion, rollback
        condition. Cite sources as [CITE: n] when available.
        """

        cve_facts: str = dspy.InputField(desc="CVE ID, CWE, vuln_class, CVSS, KEV, products, versions")
        advisory_text: str = dspy.InputField(desc="Verbatim advisory text from NVD")
        references: str = dspy.InputField(desc="Reference URLs")
        discovered_hosts: str = dspy.InputField(desc="Affected host names from CMDB")
        prior_context: str = dspy.InputField(desc="Prior remediation count and outcome distribution")
        remediation_actions: str = dspy.InputField(desc="Discovered remediation actions")
        rag_sources: str = dspy.InputField(desc="Authoritative sources for citation")
        rationale: str = dspy.OutputField(desc="4-6 sentence remediation rationale")

    class AnsibleSignature(dspy.Signature):
        """Generate a valid Ansible playbook YAML for CVE remediation.

        Output ONLY valid YAML (no markdown fences, no prose). The playbook
        must be a list with at least one play containing a 'tasks' key.
        Use only ansible.builtin modules. Target hosts: all. gather_facts: false.
        """

        cve_id: str = dspy.InputField(desc="CVE identifier")
        mode: str = dspy.InputField(desc="'apply' for remediation or 'rollback' for reversal")
        vuln_class: str = dspy.InputField(desc="Vulnerability class (host, library, application, etc.)")
        remediation_context: str = dspy.InputField(
            desc="Affected products, versions, hosts, fix version, runtime environment"
        )
        playbook_yaml: str = dspy.OutputField(
            desc="Complete valid Ansible playbook YAML (list of plays with tasks)"
        )

    class RetroSuggestionSignature(dspy.Signature):
        """Generate improvement suggestions for future CVE remediation runs."""

        cve_id: str = dspy.InputField(desc="CVE identifier")
        cwe: str = dspy.InputField(desc="CWE class")
        outcome: str = dspy.InputField(desc="Remediation outcome (patched, failed, skipped, etc.)")
        suggestions: str = dspy.OutputField(
            desc="Exactly 2 concise improvement suggestions, one per line, no numbering"
        )

    class CriticSignature(dspy.Signature):
        """Evaluate quality of CVE data extraction for correctness and completeness.

        Assess whether the extraction is ready for downstream processing.
        Structural failures (empty CVE ID) are always veto. Injection-flagged
        text or missing CWE warrants feedback. Otherwise approve if the data
        is coherent and complete enough for remediation planning.
        """

        cve_id: str = dspy.InputField(desc="Extracted CVE identifier (may be empty)")
        cwe_class: str = dspy.InputField(desc="Extracted CWE classification (may be empty)")
        injection_class: str = dspy.InputField(
            desc="Injection classification: clean, suspicious, or attack_pattern"
        )
        affected_products: str = dspy.InputField(desc="Comma-separated affected products")
        cvss_score_bp: str = dspy.InputField(desc="CVSS score in basis points (0-10000)")
        attempt: str = dspy.InputField(desc="Current critic attempt number")
        verdict: str = dspy.OutputField(desc="Exactly one of: approved, feedback, veto")
        feedback: str = dspy.OutputField(desc="Specific feedback explaining the verdict")


# ---------------------------------------------------------------------------
# Module wrappers
# ---------------------------------------------------------------------------

async def classify_vuln_class(
    cve_id: str, cwe_class: str, description: str,
) -> tuple[str, str]:
    """Classify CVE into vuln_class via DSPy. Returns (vuln_class, error)."""
    if not _ensure_lm():
        return "", "DSPy LM not configured"
    try:
        predictor = dspy.Predict(VulnClassifySignature)
        result = await asyncio.to_thread(
            predictor,
            cve_id=cve_id,
            cwe_class=cwe_class or "",
            description=description[:1800],
        )
        vc = str(result.vuln_class).strip().lower()
        if vc in _VULN_CLASS_ENUM:
            return vc, ""
        return "", f"out-of-enum: {vc!r}"
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"


async def generate_rationale(
    *,
    cve_id: str,
    cwe: str,
    vuln: str,
    code_runtime: str,
    sandbox_runtime: str,
    prior_count: int = 0,
    prior_outcomes: dict[str, int] | None = None,
    prior_suggestions: list[dict[str, str]] | None = None,
    advisory_body: str = "",
    affected_products: list[str] | None = None,
    affected_versions: list[str] | None = None,
    cvss_bp: int = 0,
    kev_listed: bool = False,
    references: list[str] | None = None,
    host_names: list[str] | None = None,
    rag_sources: list[dict[str, str]] | None = None,
    recommended_actions: list[Any] | None = None,
) -> tuple[str, int, str]:
    """Generate rationale via DSPy ChainOfThought. Returns (rationale, latency_ms, error)."""
    if not _ensure_lm():
        return "", 0, "DSPy LM not configured"

    cvss_str = f"{cvss_bp / 100:.1f}" if cvss_bp else "n/a"
    cve_facts = (
        f"cve_id: {cve_id}\ncwe: {cwe or 'unspecified'}\nvuln_class: {vuln or 'unknown'}\n"
        f"cvss: {cvss_str}{', KEV-listed' if kev_listed else ''}\n"
        f"affected_products: {', '.join(affected_products or []) or 'unknown'}\n"
        f"affected_versions: {', '.join(affected_versions or []) or 'unknown'}\n"
        f"remediation_runtime: {code_runtime}\nsandbox_runtime: {sandbox_runtime}"
    )
    advisory_text = (advisory_body or "")[:1500] or "(no advisory body)"
    ref_lines = "\n".join(f"  - {r}" for r in (references or [])[:6]) or "(none)"

    prior_context = ""
    if prior_count >= 1 and prior_outcomes:
        dist = ", ".join(f"{k}={v}" for k, v in sorted(prior_outcomes.items()))
        prior_context = f"Prior: {prior_count} run(s). Outcomes: {dist}."
    prior_suggestions = prior_suggestions or []
    if prior_suggestions:
        lines = [f"  - {str(s.get('suggestion_text', ''))[:240]}" for s in prior_suggestions if s.get("suggestion_text")]
        if lines:
            prior_context += f"\nLessons:\n" + "\n".join(lines[:5])

    recs_text = ""
    if recommended_actions:
        parts = []
        for i, a in enumerate(recommended_actions[:5], 1):
            parts.append(
                f"[{i}] kind={getattr(a, 'kind', '')} target={getattr(a, 'target', '')!r} "
                f"target_version={getattr(a, 'target_version', '')!r} "
                f"citation_url: {getattr(a, 'citation_url', '')}"
            )
        recs_text = "\n".join(parts)

    rag_text = ""
    if rag_sources:
        chunks = []
        for s in rag_sources:
            idx = s.get("index", "?")
            u = s.get("url", "")
            body = (s.get("body", "") or "")[:1200]
            chunks.append(f"[{idx}] {u}\n{body}")
        rag_text = "\n\n".join(chunks)

    t0 = time.monotonic()
    try:
        cot = dspy.ChainOfThought(RationaleSignature)
        result = await asyncio.to_thread(
            cot,
            cve_facts=cve_facts,
            advisory_text=advisory_text,
            references=ref_lines,
            discovered_hosts=", ".join(host_names or []) or "(none)",
            prior_context=prior_context or "(first run)",
            remediation_actions=recs_text or "(none)",
            rag_sources=rag_text or "(none)",
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        content = str(result.rationale).strip()
        return content, latency_ms, ""
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return "", latency_ms, f"{type(exc).__name__}: {exc}"


def _make_planner_tools(
    planner_node: Any, state: Any,
) -> list[Any]:
    """Create synchronous tool functions for DSPy ReAct with state bound."""

    def prior_retros(cwe: str) -> str:
        """Fetch recent retrospectives for this CWE class from Redis.

        Args:
            cwe: CWE identifier, e.g. "CWE-79"
        """
        result = asyncio.run(planner_node._tool_prior_retros(cwe))
        return json.dumps(result)

    def doctrine_controls(cwe: str) -> str:
        """Fetch mapped governance controls for this CWE from the knowledge graph.

        Args:
            cwe: CWE identifier, e.g. "CWE-79"
        """
        result = asyncio.run(planner_node._tool_doctrine_controls(cwe))
        return json.dumps(result)

    def advisory_section(query: str) -> str:
        """Search the advisory body for paragraphs matching a keyword.

        Args:
            query: keyword or phrase to search for in the advisory text
        """
        body = str(getattr(state, "raw_source_body", "") or "")
        result = planner_node._tool_advisory_section(body, query)
        return json.dumps(result)

    def host_topology() -> str:
        """Get CMDB host to CargoNet node pairings for the affected infrastructure."""
        return json.dumps({
            "host_names": list(getattr(state, "affected_host_names", []) or []),
            "cargonet_lab": str(getattr(state, "cargonet_lab_ref", "") or ""),
            "cargonet_correlation_map": dict(
                getattr(state, "cargonet_correlation_map", {}) or {}
            ),
        })

    return [prior_retros, doctrine_controls, advisory_section, host_topology]


async def run_planner_agent(
    *,
    planner_node: Any,
    state: Any,
    cve_id: str,
    cwe: str,
    vuln: str,
    code_runtime: str,
    sandbox_runtime: str,
    prior_count: int,
    prior_outcomes: dict[str, int] | None,
    advisory_body: str,
    affected_products: list[str],
    affected_versions: list[str],
    cvss_bp: int,
    kev_listed: bool,
    references: list[str],
    host_names: list[str],
    rag_sources: list[dict[str, str]] | None = None,
) -> tuple[str, int, str, list[dict[str, str]], int]:
    """Run planner as DSPy ReAct agent. Returns (rationale, latency_ms, error, trace, 0)."""
    if not _ensure_lm():
        return "", 0, "DSPy LM not configured", [], 0

    planner_cfg = _get_policy().get("planner", {})
    max_turns = int(
        os.environ.get("CVE_REM_PLANNER_MAX_TURNS", str(planner_cfg.get("max_turns", 4))) or "4"
    )

    cvss_str = f"{cvss_bp / 100:.1f}" if cvss_bp else "n/a"
    cve_facts = (
        f"cve_id: {cve_id}\ncwe: {cwe or 'unspecified'}\nvuln_class: {vuln or 'unknown'}\n"
        f"cvss: {cvss_str}{', KEV-listed' if kev_listed else ''}\n"
        f"affected_products: {', '.join(affected_products) or 'unknown'}\n"
        f"affected_versions: {', '.join(affected_versions) or 'unknown'}\n"
        f"remediation_runtime: {code_runtime}\nsandbox_runtime: {sandbox_runtime}"
    )

    ref_lines = "\n".join(f"  - {r}" for r in (references or [])[:6]) or "(none)"
    prior_text = f"count={prior_count}, outcomes={dict(prior_outcomes or {})}"

    recs = list(getattr(state, "recommended_actions", []) or [])
    recs_text = ""
    if recs:
        parts = []
        for i, a in enumerate(recs[:5], 1):
            parts.append(
                f"[{i}] kind={getattr(a, 'kind', '')} target={getattr(a, 'target', '')!r} "
                f"target_version={getattr(a, 'target_version', '')!r}"
            )
        recs_text = "\n".join(parts)

    rag_text = ""
    if rag_sources:
        chunks = []
        for s in rag_sources:
            chunks.append(f"[{s.get('index', '?')}] {s.get('url', '')}\n{(s.get('body', '') or '')[:1200]}")
        rag_text = "\n\n".join(chunks)

    tools = _make_planner_tools(planner_node, state)

    t0 = time.monotonic()
    try:
        react = dspy.ReAct(PlannerAgentSignature, tools=tools, max_iters=max_turns)
        result = await asyncio.to_thread(
            react,
            cve_facts=cve_facts,
            advisory_text=(advisory_body or "")[:1500] or "(no advisory body)",
            references=ref_lines,
            discovered_hosts=", ".join(host_names) or "(none)",
            prior_context=prior_text,
            remediation_actions=recs_text or "(none)",
            rag_sources=rag_text or "(none)",
        )
        latency_ms = int((time.monotonic() - t0) * 1000)
        rationale = str(result.rationale).strip()
        trace: list[dict[str, str]] = [{"role": "assistant", "content": rationale}]
        return rationale, latency_ms, "", trace, 0
    except Exception as exc:
        latency_ms = int((time.monotonic() - t0) * 1000)
        return "", latency_ms, f"{type(exc).__name__}: {exc}", [], 0


async def generate_ansible_yaml(
    *,
    cve_id: str,
    mode: str,
    vuln_class: str,
    remediation_context: str,
    plan_hash: str,
) -> str:
    """Generate Ansible playbook YAML via DSPy. Returns YAML string or stub on failure."""
    import yaml as _yaml

    if not _ensure_lm():
        return _ansible_stub(plan_hash, cve_id, mode)

    for _attempt in range(2):
        try:
            cot = dspy.ChainOfThought(AnsibleSignature)
            result = await asyncio.to_thread(
                cot,
                cve_id=cve_id,
                mode=mode,
                vuln_class=vuln_class or "unknown",
                remediation_context=remediation_context,
            )
            content = str(result.playbook_yaml).strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(l for l in lines if not l.startswith("```")).strip()
            try:
                parsed = _yaml.safe_load(content)
            except Exception:
                continue
            if (
                isinstance(parsed, list)
                and parsed
                and isinstance(parsed[0], dict)
                and parsed[0].get("tasks")
            ):
                return content
        except Exception:
            continue

    return _ansible_stub(plan_hash, cve_id, mode)


def _ansible_stub(plan_hash: str, cve_id: str, mode: str) -> str:
    return (
        f"---\n"
        f"- name: CVE {cve_id} remediation ({mode}) plan={plan_hash[:8]} [LM-UNAVAILABLE]\n"
        f"  hosts: all\n"
        f"  gather_facts: false\n"
        f"  tasks:\n"
        f"    - name: LM unavailable — cannot generate remediation for {cve_id}\n"
        f"      ansible.builtin.fail:\n"
        f'        msg: "No LM available to generate {mode} playbook for {cve_id}. Manual remediation required."\n'
    )


async def generate_suggestions(
    cve_id: str, cwe: str, outcome: str,
) -> list[str]:
    """Generate retro improvement suggestions via DSPy. Returns list of suggestion strings."""
    if not _ensure_lm():
        return [f"Consider automated regression testing for {cwe} class vulnerabilities."]
    try:
        predictor = dspy.Predict(RetroSuggestionSignature)
        result = await asyncio.to_thread(
            predictor,
            cve_id=cve_id,
            cwe=cwe or "unknown",
            outcome=outcome or "unknown",
        )
        content = str(result.suggestions).strip()
        return [line.strip() for line in content.splitlines() if line.strip()][:2]
    except Exception:
        return [f"Consider automated regression testing for {cwe} class vulnerabilities."]


async def evaluate_critic(
    *,
    cve_id: str,
    cwe_class: str,
    injection_class: str,
    affected_products: list[str] | None = None,
    cvss_score_bp: int = 0,
    attempt: int = 1,
) -> tuple[str, str]:
    """Evaluate extraction quality via DSPy LLM critic. Returns (verdict, feedback).

    Falls back to Bosun CLIPS rules when LLM unavailable.
    """
    if not _ensure_lm():
        from demos.cve_remediation.graph._bosun import CveRemBosunEvaluator
        result = CveRemBosunEvaluator.evaluate_critic(
            cve_id=cve_id, cwe_class=cwe_class,
            injection_class=injection_class or "clean", attempt=attempt,
        )
        return result["verdict"], result["feedback"]

    try:
        cot = dspy.ChainOfThought(CriticSignature)
        result = await asyncio.to_thread(
            cot,
            cve_id=cve_id or "",
            cwe_class=cwe_class or "",
            injection_class=injection_class or "clean",
            affected_products=", ".join(affected_products or []) or "(none)",
            cvss_score_bp=str(cvss_score_bp),
            attempt=str(attempt),
        )
        verdict = str(result.verdict).strip().lower()
        feedback = str(result.feedback).strip()
        if verdict not in ("approved", "feedback", "veto"):
            verdict = "feedback"
            feedback = f"LLM returned invalid verdict, treating as feedback: {result.verdict}"
        return verdict, feedback
    except Exception as exc:
        from demos.cve_remediation.graph._bosun import CveRemBosunEvaluator
        log.warning("critic_llm_failed_falling_back_to_bosun", error=str(exc))
        result = CveRemBosunEvaluator.evaluate_critic(
            cve_id=cve_id, cwe_class=cwe_class,
            injection_class=injection_class or "clean", attempt=attempt,
        )
        return result["verdict"], result["feedback"]
