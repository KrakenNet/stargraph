# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the CVE-rem Bosun evaluator (_bosun.py).

Verifies that all 4 CLIPS rule packs (kill_switches, doctrine_trust,
offline_isolation, gepa_score_policy) load, compile, and evaluate
correctly through the CveRemBosunEvaluator API.
"""

from __future__ import annotations

import pytest

from demos.cve_remediation.graph._bosun import CveRemBosunEvaluator


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    CveRemBosunEvaluator._compiled_constructs = None


class TestGepaScorePolicy:
    def test_accept_when_strictly_better(self) -> None:
        r = CveRemBosunEvaluator.evaluate_gepa(
            artifact_hash="test-accept",
            components={
                "validation": 1.0, "sandbox": 1.0,
                "cr_approved": 1.0, "no_drift_7d": 1.0,
                "no_rollback_30d": 1.0,
            },
            current_score=0.5,
            epsilon=0.02,
        )
        assert r["decision"] == "accept"
        assert r["candidate_score"] == pytest.approx(1.0, abs=0.01)
        assert r["bosun_evaluated"] is True

    def test_reject_when_worse(self) -> None:
        r = CveRemBosunEvaluator.evaluate_gepa(
            artifact_hash="test-reject",
            components={
                "validation": 0.2, "sandbox": 0.1,
                "cr_approved": 0.1, "no_drift_7d": 0.1,
                "no_rollback_30d": 0.1,
            },
            current_score=0.8,
            epsilon=0.02,
        )
        assert r["decision"] == "reject"

    def test_out_of_range_violation(self) -> None:
        r = CveRemBosunEvaluator.evaluate_gepa(
            artifact_hash="test-oor",
            components={
                "validation": 1.5, "sandbox": 0.5,
                "cr_approved": 0.5, "no_drift_7d": 0.5,
                "no_rollback_30d": 0.5,
            },
            current_score=0.5,
            epsilon=0.02,
        )
        kinds = [v["kind"] for v in r["violations"]]
        assert "score-component-out-of-range" in kinds

    def test_weighted_formula_matches_spec(self) -> None:
        r = CveRemBosunEvaluator.evaluate_gepa(
            artifact_hash="test-formula",
            components={
                "validation": 0.8, "sandbox": 0.6,
                "cr_approved": 1.0, "no_drift_7d": 0.4,
                "no_rollback_30d": 1.0,
            },
            current_score=0.0,
            epsilon=0.01,
        )
        expected = 0.35*0.8 + 0.25*0.6 + 0.15*1.0 + 0.15*0.4 + 0.10*1.0
        assert r["candidate_score"] == pytest.approx(expected, abs=0.001)


class TestKillSwitches:
    def test_rollback_rate_exceeded_halts(self) -> None:
        r = CveRemBosunEvaluator.evaluate_kill_switches(
            metrics=[{
                "kind": "rollback-rate",
                "window_hours": 24,
                "value": 10.0,
                "threshold": 5.0,
                "run_id": "fleet",
            }],
        )
        assert r["halt"] is True
        kinds = [v["kind"] for v in r["violations"]]
        assert "rollback-rate-exceeded" in kinds

    def test_below_threshold_no_halt(self) -> None:
        r = CveRemBosunEvaluator.evaluate_kill_switches(
            metrics=[{
                "kind": "rollback-rate",
                "window_hours": 24,
                "value": 2.0,
                "threshold": 5.0,
                "run_id": "fleet",
            }],
        )
        assert r["halt"] is False

    def test_quorum_2_of_3(self) -> None:
        r = CveRemBosunEvaluator.evaluate_kill_switches(
            kill_signals=[
                {"kind": "halt-rollback-in-flight", "role": "security-eng", "run_id": "r1"},
                {"kind": "halt-rollback-in-flight", "role": "pipeline-owner", "run_id": "r1"},
            ],
        )
        assert r["halt"] is True
        assert len(r["quorum_requests"]) >= 1

    def test_single_signer_no_quorum(self) -> None:
        r = CveRemBosunEvaluator.evaluate_kill_switches(
            kill_signals=[
                {"kind": "halt-rollback-in-flight", "role": "security-eng", "run_id": "r2"},
            ],
        )
        assert r["halt"] is False
        assert len(r["quorum_requests"]) == 0


class TestDoctrineTrust:
    def test_trusted_source_passes(self) -> None:
        r = CveRemBosunEvaluator.evaluate_doctrine_trust(
            sources=[{
                "id": "mitre-attack",
                "source_class": "trusted-doctrine",
                "corpus_version_pin": "v15",
                "corpus_sha256": "abc123",
            }],
            manifest_hash="mh-ok",
            allowlist_entries=[{"manifest_hash": "mh-ok", "active": "true"}],
        )
        assert r["halt"] is False

    def test_untrusted_source_halts(self) -> None:
        r = CveRemBosunEvaluator.evaluate_doctrine_trust(
            sources=[{"id": "random-blog", "source_class": "untrusted"}],
        )
        assert r["halt"] is True
        kinds = [v["kind"] for v in r["violations"]]
        assert "doctrine-source-class-mismatch" in kinds

    def test_unallowlisted_manifest_halts(self) -> None:
        r = CveRemBosunEvaluator.evaluate_doctrine_trust(
            sources=[{
                "id": "nist", "source_class": "trusted-doctrine",
                "corpus_version_pin": "v1", "corpus_sha256": "x",
            }],
            manifest_hash="not-in-list",
            allowlist_entries=[{"manifest_hash": "other", "active": "true"}],
        )
        assert r["halt"] is True
        kinds = [v["kind"] for v in r["violations"]]
        assert "doctrine-manifest-unallowlisted" in kinds


class TestOfflineIsolation:
    def test_inbound_from_production_halts(self) -> None:
        r = CveRemBosunEvaluator.evaluate_isolation(
            network_edges=[{
                "edge_id": "e1",
                "direction": "inbound",
                "source_zone": "production",
                "dest_zone": "eval",
            }],
        )
        assert r["halt"] is True
        kinds = [v["kind"] for v in r["violations"]]
        assert "isolation-inbound-from-production" in kinds

    def test_egress_to_approved_drop_ok(self) -> None:
        r = CveRemBosunEvaluator.evaluate_isolation(
            network_edges=[{
                "edge_id": "e2",
                "direction": "outbound",
                "source_zone": "eval",
                "dest_zone": "approved-drop",
            }],
        )
        assert r["halt"] is False

    def test_egress_to_other_zone_halts(self) -> None:
        r = CveRemBosunEvaluator.evaluate_isolation(
            network_edges=[{
                "edge_id": "e3",
                "direction": "outbound",
                "source_zone": "eval",
                "dest_zone": "corporate-lan",
            }],
        )
        assert r["halt"] is True
        kinds = [v["kind"] for v in r["violations"]]
        assert "isolation-egress-unauthorized" in kinds


class TestSsvcPolicy:
    def test_kev_routes_act_auto(self) -> None:
        r = CveRemBosunEvaluator.evaluate_ssvc(
            cvss_bp=100, epss_bp=0, kev_listed=True, blast_radius=0,
        )
        assert r["tier"] == "act_auto"
        assert r["rule_id"] == "ssvc-act-auto-kev"

    def test_high_cvss_blast_routes_act_auto(self) -> None:
        r = CveRemBosunEvaluator.evaluate_ssvc(
            cvss_bp=950, epss_bp=0, kev_listed=False, blast_radius=150,
        )
        assert r["tier"] == "act_auto"

    def test_hitl_required(self) -> None:
        r = CveRemBosunEvaluator.evaluate_ssvc(
            cvss_bp=750, epss_bp=600, kev_listed=False, blast_radius=0,
        )
        assert r["tier"] == "act_hitl_required"

    def test_attend(self) -> None:
        r = CveRemBosunEvaluator.evaluate_ssvc(
            cvss_bp=500, epss_bp=100, kev_listed=False, blast_radius=10,
        )
        assert r["tier"] == "attend"

    def test_defer(self) -> None:
        r = CveRemBosunEvaluator.evaluate_ssvc(
            cvss_bp=200, epss_bp=50, kev_listed=False, blast_radius=0,
        )
        assert r["tier"] == "defer"

    def test_track_default(self) -> None:
        r = CveRemBosunEvaluator.evaluate_ssvc(
            cvss_bp=200, epss_bp=50, kev_listed=False, blast_radius=5,
        )
        assert r["tier"] == "track"


class TestQuarantinePolicy:
    def test_version_mismatch_quarantines(self) -> None:
        r = CveRemBosunEvaluator.evaluate_quarantine(divergences=[
            {"phase": "apply", "field_class": "version", "observed": "1.0", "expected": "2.0"},
        ])
        assert r["quarantine"] is True

    def test_status_mismatch_quarantines(self) -> None:
        r = CveRemBosunEvaluator.evaluate_quarantine(divergences=[
            {"phase": "apply", "field_class": "status", "observed": "vulnerable", "expected": "patched"},
        ])
        assert r["quarantine"] is True

    def test_config_divergence_warns_only(self) -> None:
        r = CveRemBosunEvaluator.evaluate_quarantine(divergences=[
            {"phase": "apply", "field_class": "config", "observed": "old", "expected": "new"},
        ])
        assert r["quarantine"] is False

    def test_timestamp_info_only(self) -> None:
        r = CveRemBosunEvaluator.evaluate_quarantine(divergences=[
            {"phase": "apply", "field_class": "timestamp", "observed": "t1", "expected": "t2"},
        ])
        assert r["quarantine"] is False

    def test_unknown_field_defaults_critical(self) -> None:
        r = CveRemBosunEvaluator.evaluate_quarantine(divergences=[
            {"phase": "apply", "field_class": "exotic_thing", "observed": "a", "expected": "b"},
        ])
        assert r["quarantine"] is True

    def test_no_divergences_no_quarantine(self) -> None:
        r = CveRemBosunEvaluator.evaluate_quarantine(divergences=[])
        assert r["quarantine"] is False


class TestDispositionPolicy:
    def test_kev_disables(self) -> None:
        r = CveRemBosunEvaluator.evaluate_disposition(
            cve_id="CVE-2024-0001", kev_listed=True,
            cvss_bp=300, vulnerability_status="no_fix",
        )
        assert r["disposition"] == "disable_recommended"

    def test_high_cvss_disables(self) -> None:
        r = CveRemBosunEvaluator.evaluate_disposition(
            cve_id="CVE-2024-0002", kev_listed=False,
            cvss_bp=850, vulnerability_status="no_fix",
        )
        assert r["disposition"] == "disable_recommended"

    def test_low_severity_isolates(self) -> None:
        r = CveRemBosunEvaluator.evaluate_disposition(
            cve_id="CVE-2024-0003", kev_listed=False,
            cvss_bp=300, vulnerability_status="no_fix",
        )
        assert r["disposition"] == "isolate_recommended"


class TestCriticPolicy:
    def test_approved_clean_extraction(self) -> None:
        r = CveRemBosunEvaluator.evaluate_critic(
            cve_id="CVE-2024-0001", cwe_class="CWE-79",
            injection_class="clean", attempt=1,
        )
        assert r["verdict"] == "approved"

    def test_veto_missing_cve(self) -> None:
        r = CveRemBosunEvaluator.evaluate_critic(
            cve_id="", cwe_class="CWE-79",
            injection_class="clean", attempt=1,
        )
        assert r["verdict"] == "veto"

    def test_feedback_on_injection(self) -> None:
        r = CveRemBosunEvaluator.evaluate_critic(
            cve_id="CVE-2024-0001", cwe_class="CWE-79",
            injection_class="suspicious", attempt=1,
        )
        assert r["verdict"] == "feedback"

    def test_feedback_on_missing_cwe(self) -> None:
        r = CveRemBosunEvaluator.evaluate_critic(
            cve_id="CVE-2024-0001", cwe_class="",
            injection_class="clean", attempt=1,
        )
        assert r["verdict"] == "feedback"
