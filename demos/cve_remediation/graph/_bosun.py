# SPDX-License-Identifier: Apache-2.0
"""Bosun pack loader + evaluator for the CVE remediation pipeline.

Loads the 4 CLIPS rule packs from ``rules/`` on first use (singleton),
asserts domain facts, fires rules, and returns structured decisions.
Replaces the hardcoded Python if/elif chains with real Fathom evaluation.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harbor.logging import get_logger

log = get_logger("cve_rem.bosun")

_RULES_ROOT = Path(__file__).parent / "rules"

_PACKS = [
    "cve_rem.kill_switches",
    "cve_rem.doctrine_trust",
    "cve_rem.offline_isolation",
    "cve_rem.gepa_score_policy",
    "cve_rem.ssvc_policy",
    "cve_rem.quarantine_policy",
    "cve_rem.disposition_policy",
    "cve_rem.critic_policy",
]


def _strip_comments(src: str) -> str:
    """Strip CLIPS line comments (;) while preserving semicolons inside strings."""
    out: list[str] = []
    in_string = False
    for line in src.splitlines():
        result: list[str] = []
        for ch in line:
            if ch == '"':
                in_string = not in_string
            elif ch == ';' and not in_string:
                break
            result.append(ch)
        out.append("".join(result))
    return "\n".join(out)


def _split_constructs(src: str) -> list[str]:
    src = _strip_comments(src)
    constructs: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in src:
        if depth == 0 and ch.isspace():
            continue
        cur.append(ch)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                constructs.append("".join(cur))
                cur = []
    return [c for c in constructs if c.strip()]


def _load_pack(engine: Any, pack_name: str) -> int:
    rules_path = _RULES_ROOT / pack_name / "rules.clp"
    if not rules_path.exists():
        log.warning("bosun_pack_missing", pack=pack_name, path=str(rules_path))
        return 0
    src = rules_path.read_text(encoding="utf-8")
    count = 0
    for construct in _split_constructs(src):
        engine._env.build(construct)
        count += 1
    log.info("bosun_pack_loaded", pack=pack_name, constructs=count)
    return count


class CveRemBosunEvaluator:
    """Singleton Bosun evaluator for cve-rem pipeline.

    Creates a fresh Fathom Engine per evaluation (stateless between runs)
    with all 4 packs pre-compiled. Call domain-specific methods to assert
    facts and evaluate rules.
    """

    _compiled_constructs: list[str] | None = None

    @classmethod
    def _ensure_compiled(cls) -> list[str]:
        if cls._compiled_constructs is not None:
            return cls._compiled_constructs
        import re as _re
        seen_templates: set[str] = set()
        all_constructs: list[str] = []
        for pack_name in _PACKS:
            rules_path = _RULES_ROOT / pack_name / "rules.clp"
            if not rules_path.exists():
                log.warning("bosun_pack_missing", pack=pack_name)
                continue
            src = rules_path.read_text(encoding="utf-8")
            for construct in _split_constructs(src):
                m = _re.match(r"\(deftemplate\s+([\w.]+)", construct)
                if m:
                    tpl_name = m.group(1)
                    if tpl_name in seen_templates:
                        continue
                    seen_templates.add(tpl_name)
                all_constructs.append(construct)
        cls._compiled_constructs = all_constructs
        log.info("bosun_constructs_cached", count=len(all_constructs), templates=len(seen_templates))
        return all_constructs

    @classmethod
    def _fresh_engine(cls) -> Any:
        from fathom import Engine
        eng = Engine(default_decision="deny")
        for construct in cls._ensure_compiled():
            eng._env.build(construct)
        return eng

    @classmethod
    def evaluate_gepa(
        cls,
        *,
        artifact_hash: str,
        components: dict[str, float],
        current_score: float,
        epsilon: float,
    ) -> dict[str, Any]:
        """Evaluate GEPA score via the cve_rem.gepa_score_policy pack.

        Returns dict with 'decision' (accept/reject), 'candidate_score',
        'delta', and any violations.
        """
        eng = cls._fresh_engine()

        for kind, value in components.items():
            eng._env.assert_string(
                f'(cve_rem.score_component '
                f'(artifact_hash "{artifact_hash}") '
                f'(kind "{kind}") '
                f'(value {value}))'
            )

        eng._env.assert_string(
            f'(cve_rem.gepa_inputs '
            f'(artifact_hash "{artifact_hash}") '
            f'(current_score {current_score}) '
            f'(epsilon {epsilon}))'
        )

        eng._env.run()

        decisions = [
            dict(f) for f in eng._env.find_template("cve_rem.gepa_decision").facts()
        ]
        violations = [
            dict(f) for f in eng._env.find_template("bosun.violation").facts()
        ]
        scores = [
            dict(f) for f in eng._env.find_template("cve_rem.gepa_score").facts()
        ]

        decision = decisions[0] if decisions else None
        score = scores[0]["value"] if scores else None

        result: dict[str, Any] = {
            "decision": decision["decision"] if decision else "no_decision",
            "candidate_score": score,
            "current_score": current_score,
            "epsilon": epsilon,
            "delta": decision["delta"] if decision else 0.0,
            "violations": violations,
            "bosun_evaluated": True,
        }
        log.info("bosun_gepa_evaluated", **result)
        return result

    @classmethod
    def evaluate_kill_switches(
        cls,
        *,
        metrics: list[dict[str, Any]] | None = None,
        kill_signals: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate kill-switch rules against metric + signal facts.

        Returns dict with 'halt' (bool), 'violations' list, 'quorum_requests'.
        """
        eng = cls._fresh_engine()

        for m in (metrics or []):
            eng._env.assert_string(
                f'(cve_rem.metric '
                f'(kind "{m["kind"]}") '
                f'(window_hours {m.get("window_hours", 24)}) '
                f'(value {m["value"]}) '
                f'(threshold {m.get("threshold", 0)}) '
                f'(run_id "{m.get("run_id", "fleet")}") '
                f'(computed_at "{m.get("computed_at", datetime.now(UTC).isoformat())}"))'
            )

        for s in (kill_signals or []):
            eng._env.assert_string(
                f'(cve_rem.kill_signal '
                f'(kind "{s["kind"]}") '
                f'(actor "{s.get("actor", "system")}") '
                f'(role "{s["role"]}") '
                f'(run_id "{s.get("run_id", "fleet")}") '
                f'(signature_id "{s.get("signature_id", "")}"))'
            )

        eng._env.run()

        violations = [
            dict(f) for f in eng._env.find_template("bosun.violation").facts()
        ]
        quorum_requests = [
            dict(f)
            for f in eng._env.find_template("cve_rem.quorum_request").facts()
        ]

        halt = any(v.get("severity") == "halt" for v in violations)

        result = {
            "halt": halt,
            "violations": violations,
            "quorum_requests": quorum_requests,
            "bosun_evaluated": True,
        }
        log.info("bosun_kill_switches_evaluated", halt=halt, violation_count=len(violations))
        return result

    @classmethod
    def evaluate_doctrine_trust(
        cls,
        *,
        sources: list[dict[str, Any]] | None = None,
        manifest_hash: str = "",
        allowlist_entries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate doctrine trust rules.

        Returns dict with 'halt' (bool), 'violations' list.
        """
        eng = cls._fresh_engine()

        for src in (sources or []):
            eng._env.assert_string(
                f'(cve_rem.doctrine_source '
                f'(id "{src["id"]}") '
                f'(source_class "{src["source_class"]}") '
                f'(corpus_version_pin "{src.get("corpus_version_pin", "")}") '
                f'(corpus_sha256 "{src.get("corpus_sha256", "")}"))'
            )

        if manifest_hash:
            eng._env.assert_string(
                f'(cve_rem.doctrine_manifest '
                f'(manifest_hash "{manifest_hash}") '
                f'(signed_at "{datetime.now(UTC).isoformat()}") '
                f'(signed_by "system"))'
            )

        for entry in (allowlist_entries or []):
            eng._env.assert_string(
                f'(cve_rem.allowlist_entry '
                f'(manifest_hash "{entry["manifest_hash"]}") '
                f'(active "{entry.get("active", "true")}"))'
            )

        eng._env.run()

        violations = [
            dict(f) for f in eng._env.find_template("bosun.violation").facts()
        ]
        halt = any(v.get("severity") == "halt" for v in violations)

        result = {
            "halt": halt,
            "violations": violations,
            "bosun_evaluated": True,
        }
        log.info("bosun_doctrine_trust_evaluated", halt=halt, violation_count=len(violations))
        return result

    @classmethod
    def evaluate_isolation(
        cls,
        *,
        network_edges: list[dict[str, Any]] | None = None,
        replica_loads: list[dict[str, Any]] | None = None,
        redaction_packs: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Evaluate offline isolation rules.

        Returns dict with 'halt' (bool), 'violations' list.
        """
        eng = cls._fresh_engine()

        for edge in (network_edges or []):
            eng._env.assert_string(
                f'(cve_rem.network_edge '
                f'(edge_id "{edge["edge_id"]}") '
                f'(direction "{edge["direction"]}") '
                f'(source_zone "{edge["source_zone"]}") '
                f'(dest_zone "{edge.get("dest_zone", "")}") '
                f'(port "{edge.get("port", "")}") '
                f'(opened_at "{edge.get("opened_at", "")}"))'
            )

        for load in (replica_loads or []):
            eng._env.assert_string(
                f'(cve_rem.replica_load '
                f'(load_id "{load["load_id"]}") '
                f'(replica_schema "{load.get("replica_schema", "eval")}") '
                f'(redaction_pack_hash "{load.get("redaction_pack_hash", "")}") '
                f'(loaded_at "{load.get("loaded_at", "")}"))'
            )

        for rp in (redaction_packs or []):
            eng._env.assert_string(
                f'(cve_rem.redaction_pack '
                f'(pack_hash "{rp["pack_hash"]}") '
                f'(signed_by "{rp.get("signed_by", "system")}") '
                f'(active "{rp.get("active", "true")}"))'
            )

        eng._env.run()

        violations = [
            dict(f) for f in eng._env.find_template("bosun.violation").facts()
        ]
        halt = any(v.get("severity") == "halt" for v in violations)

        result = {
            "halt": halt,
            "violations": violations,
            "bosun_evaluated": True,
        }
        log.info("bosun_isolation_evaluated", halt=halt, violation_count=len(violations))
        return result

    @classmethod
    def evaluate_ssvc(
        cls,
        *,
        cvss_bp: int,
        epss_bp: int,
        kev_listed: bool,
        blast_radius: int,
    ) -> dict[str, Any]:
        """Evaluate SSVC tier via CLIPS rules instead of hardcoded thresholds."""
        eng = cls._fresh_engine()

        eng._env.assert_string(
            f'(cve_rem.ssvc_input '
            f'(cvss_bp {cvss_bp}) '
            f'(epss_bp {epss_bp}) '
            f'(kev_listed "{"TRUE" if kev_listed else "FALSE"}") '
            f'(blast_radius {blast_radius}))'
        )

        eng._env.run()

        decisions = [
            dict(f) for f in eng._env.find_template("cve_rem.ssvc_decision").facts()
        ]
        decision = decisions[0] if decisions else None

        result = {
            "tier": decision["tier"] if decision else "track",
            "rule_id": decision["rule_id"] if decision else "fallback",
            "reason": decision["reason"] if decision else "no rule matched",
            "bosun_evaluated": True,
        }
        log.info("bosun_ssvc_evaluated", **result)
        return result

    @classmethod
    def evaluate_quarantine(
        cls,
        *,
        divergences: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Evaluate quarantine via CLIPS severity rules.

        Each divergence has: phase, field_class, observed, expected.
        Returns dict with 'quarantine' (bool), 'decisions' list.
        """
        eng = cls._fresh_engine()

        for d in divergences:
            eng._env.assert_string(
                f'(cve_rem.probe_divergence '
                f'(phase "{d["phase"]}") '
                f'(field_class "{d.get("field_class", "unknown")}") '
                f'(observed "{d["observed"]}") '
                f'(expected "{d["expected"]}"))'
            )

        eng._env.run()

        decisions = [
            dict(f) for f in eng._env.find_template("cve_rem.quarantine_decision").facts()
        ]
        quarantine = any(d.get("quarantine") == "TRUE" for d in decisions)

        result = {
            "quarantine": quarantine,
            "decisions": decisions,
            "bosun_evaluated": True,
        }
        log.info("bosun_quarantine_evaluated", quarantine=quarantine, decision_count=len(decisions))
        return result

    @classmethod
    def evaluate_disposition(
        cls,
        *,
        cve_id: str,
        kev_listed: bool,
        cvss_bp: int,
        vulnerability_status: str,
    ) -> dict[str, Any]:
        """Evaluate unpatchable CVE disposition via CLIPS rules."""
        eng = cls._fresh_engine()

        eng._env.assert_string(
            f'(cve_rem.unpatchable_input '
            f'(cve_id "{cve_id}") '
            f'(kev_listed "{"TRUE" if kev_listed else "FALSE"}") '
            f'(cvss_bp {cvss_bp}) '
            f'(vulnerability_status "{vulnerability_status}"))'
        )

        eng._env.run()

        decisions = [
            dict(f) for f in eng._env.find_template("cve_rem.disposition_decision").facts()
        ]
        decision = decisions[0] if decisions else None

        result = {
            "disposition": decision["disposition"] if decision else "isolate_recommended",
            "reason": decision["reason"] if decision else "no rule matched",
            "bosun_evaluated": True,
        }
        log.info("bosun_disposition_evaluated", **result)
        return result

    @classmethod
    def evaluate_critic(
        cls,
        *,
        cve_id: str,
        cwe_class: str,
        injection_class: str,
        attempt: int,
    ) -> dict[str, Any]:
        """Evaluate critic verdict via CLIPS rules."""
        eng = cls._fresh_engine()

        eng._env.assert_string(
            f'(cve_rem.critic_input '
            f'(cve_id "{cve_id}") '
            f'(cwe_class "{cwe_class}") '
            f'(injection_class "{injection_class}") '
            f'(attempt {attempt}))'
        )

        eng._env.run()

        decisions = [
            dict(f) for f in eng._env.find_template("cve_rem.critic_decision").facts()
        ]
        decision = decisions[0] if decisions else None

        result = {
            "verdict": decision["verdict"] if decision else "veto",
            "feedback": decision["feedback"] if decision else "no rule matched",
            "rule_id": decision["rule_id"] if decision else "fallback",
            "bosun_evaluated": True,
        }
        log.info("bosun_critic_evaluated", **result)
        return result
