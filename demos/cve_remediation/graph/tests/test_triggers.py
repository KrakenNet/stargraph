# SPDX-License-Identifier: Apache-2.0
"""Structural tests for the cve_remediation triggers.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml

TRIGGERS_YAML = Path(__file__).resolve().parent.parent / "triggers.yaml"

EXPECTED_GRAPH_IDS = {
    "graph:cve-rem-main",
    "graph:cve-rem-doctrine-ingest",
    "graph:cve-rem-offline-learning",
    "graph:cve-rem-drift-watch",
    "graph:cve-rem-tier-re-eval",
    "graph:cve-rem-audit-anchor",
    "graph:cve-rem-lab-leak-reaper",
    "graph:cve-rem-rolling-restart",
}


def _load() -> dict:
    return yaml.safe_load(TRIGGERS_YAML.read_text())


def test_triggers_yaml_loads() -> None:
    doc = _load()
    assert doc["version"] == "1.0"


def test_total_trigger_count() -> None:
    """Nine triggers across three kinds: 2 manual + 1 webhook + 6 cron.

    Main pipeline carries both manual.cve_replay and webhook.cve_feed_ingest
    (replay vs ingest paths); each of the 7 other graphs has exactly
    one trigger.
    """
    doc = _load()
    manual = len(doc.get("manual", []))
    cron = len(doc.get("cron", []))
    webhook = len(doc.get("webhook", []))
    assert manual == 2
    assert webhook == 1
    assert cron == 6
    assert manual + cron + webhook == 9


def test_all_three_kinds_present() -> None:
    doc = _load()
    assert doc.get("manual"), "missing manual triggers"
    assert doc.get("cron"), "missing cron triggers"
    assert doc.get("webhook"), "missing webhook triggers"


def test_eight_distinct_graph_ids_covered() -> None:
    """Each of the 8 demo graphs must have at least one trigger."""
    doc = _load()
    seen: set[str] = set()
    for kind in ("manual", "cron", "webhook"):
        for t in doc.get(kind, []):
            seen.add(t["graph_id"])
    missing = EXPECTED_GRAPH_IDS - seen
    assert not missing, f"graphs without triggers: {missing}"


def test_no_duplicate_trigger_ids() -> None:
    doc = _load()
    ids: list[str] = []
    for kind in ("manual", "cron", "webhook"):
        for t in doc.get(kind, []):
            ids.append(t["id"])
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate trigger ids: {duplicates}"


def test_cron_exprs_present_and_have_tz() -> None:
    doc = _load()
    for t in doc.get("cron", []):
        assert "expr" in t, f"cron {t['id']} missing expr"
        assert t.get("tz") == "UTC", f"cron {t['id']} tz must be UTC"
        assert "missed_fire_policy" in t, f"cron {t['id']} missing missed_fire_policy"


def test_webhook_secret_env_pinned() -> None:
    doc = _load()
    for t in doc.get("webhook", []):
        assert t.get("current_secret_env"), f"webhook {t['id']} missing current_secret_env"
        assert t.get("previous_secret_env"), f"webhook {t['id']} missing previous_secret_env"
        assert t.get("timestamp_window_seconds", 0) > 0, (
            f"webhook {t['id']} missing timestamp_window_seconds"
        )


def test_gepa_compile_is_manual_only() -> None:
    """Policy: cron-fired compiles must NOT reach the ship step."""
    doc = _load()
    cron_graph_ids = {t["graph_id"] for t in doc.get("cron", [])}
    assert "graph:cve-rem-offline-learning" not in cron_graph_ids, (
        "Phase 6 offline-learning must be manual-only (Shamir gate)"
    )
    manual_ids = {t["graph_id"] for t in doc.get("manual", [])}
    assert "graph:cve-rem-offline-learning" in manual_ids
