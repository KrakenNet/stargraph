# SPDX-License-Identifier: Apache-2.0
"""Structural tests for the 5 cve_rem.* Fathom rule packs.

Verifies pack manifests, rule file presence, rule count floors, and
that every governance pack id referenced by the IRs has a real pack
directory backing it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

GRAPH_DIR = Path(__file__).resolve().parent.parent
RULES_DIR = GRAPH_DIR / "rules"
MAIN_IR = GRAPH_DIR / "harbor.yaml"
PHASE0_IR = GRAPH_DIR / "phase0" / "doctrine_ingest.yaml"
PHASE6_IR = GRAPH_DIR / "phase6" / "offline_learning.yaml"

GOVERNANCE_PACKS = {
    "cve_rem.kill_switches",
    "cve_rem.doctrine_trust",
    "cve_rem.offline_isolation",
    "cve_rem.gepa_score_policy",
}
ROUTING_PACKS = {"cve_rem.routing"}
ALL_PACKS = GOVERNANCE_PACKS | ROUTING_PACKS


def _load(p: Path) -> dict:
    return yaml.safe_load(p.read_text())


@pytest.mark.parametrize("pack_id", sorted(ALL_PACKS))
def test_pack_directory_exists(pack_id: str) -> None:
    assert (RULES_DIR / pack_id).is_dir(), f"missing pack dir for {pack_id}"


@pytest.mark.parametrize("pack_id", sorted(ALL_PACKS))
def test_pack_manifest_id_matches(pack_id: str) -> None:
    """Every pack — routing or governance — ships a top-level pack.yaml
    in the unified Bosun format (id/version/flavor/api_version/...).

    Governance packs additionally carry a ``ruleset_file`` reference
    pointing at the CLIPS source (rules.clp) so the engine can load
    the variable-binding-heavy rules unmodified.
    """
    pack_dir = RULES_DIR / pack_id
    manifest = pack_dir / "pack.yaml"
    assert manifest.exists(), f"missing pack.yaml at {manifest}"
    doc = _load(manifest)
    assert doc["id"] == pack_id
    assert "version" in doc
    api_version = doc.get("api_version") or doc.get("requires", {}).get("api_version")
    assert api_version == "1", f"{pack_id} api_version={api_version!r}"


@pytest.mark.parametrize("pack_id", sorted(GOVERNANCE_PACKS))
def test_governance_pack_yaml_references_clips_ruleset(pack_id: str) -> None:
    """Governance packs delegate rule body to rules.clp via ruleset_file."""
    doc = _load(RULES_DIR / pack_id / "pack.yaml")
    assert doc.get("ruleset_file") == "rules.clp"
    assert doc.get("flavor") == "governance"
    assert doc.get("rules") == []  # inline rules empty; CLIPS is the source


@pytest.mark.parametrize("pack_id", sorted(GOVERNANCE_PACKS))
def test_governance_pack_has_clips_rules(pack_id: str) -> None:
    rules_clp = RULES_DIR / pack_id / "rules.clp"
    assert rules_clp.exists(), f"missing rules.clp in {pack_id}"
    text = rules_clp.read_text()
    # Must declare at least one template + at least 2 rules
    assert "(deftemplate" in text, f"{pack_id} has no deftemplate"
    rule_count = len(re.findall(r"\(defrule\s+", text))
    assert rule_count >= 2, f"{pack_id} declares {rule_count} rules; expected >= 2"


# Lock in post-D2 floor counts so future regressions are caught loudly.
_RULE_FLOORS = {
    "cve_rem.kill_switches": 12,        # 9 base + 3 D2
    "cve_rem.doctrine_trust": 6,        # 4 base + 2 D2
    "cve_rem.offline_isolation": 6,     # 4 base + 2 D2
    "cve_rem.gepa_score_policy": 7,     # 4 base + 3 D2
}


@pytest.mark.parametrize("pack_id,floor", sorted(_RULE_FLOORS.items()))
def test_governance_pack_rule_floor(pack_id: str, floor: int) -> None:
    """D2 floor: each governance pack carries its post-expansion rule count."""
    text = (RULES_DIR / pack_id / "rules.clp").read_text()
    rule_count = len(re.findall(r"\(defrule\s+", text))
    assert rule_count >= floor, (
        f"{pack_id} declares {rule_count} rules; D2 floor is {floor}"
    )


def test_routing_pack_rule_floor() -> None:
    """Routing pack carries 12 rules after D2 (9 base + 3 D2)."""
    doc = _load(RULES_DIR / "cve_rem.routing" / "pack.yaml")
    assert len(doc["rules"]) >= 12, (
        f"routing pack has {len(doc['rules'])} rules; D2 floor is 12"
    )


@pytest.mark.parametrize("pack_id", sorted(GOVERNANCE_PACKS))
def test_governance_pack_emits_violations(pack_id: str) -> None:
    """Every governance pack must declare bosun.violation template + at
    least one rule that asserts a violation, otherwise it cannot fail-loud."""
    rules_clp = (RULES_DIR / pack_id / "rules.clp").read_text()
    assert "(deftemplate bosun.violation" in rules_clp, f"{pack_id} missing bosun.violation template"
    assert "(assert (bosun.violation" in rules_clp, f"{pack_id} never asserts a violation"


@pytest.mark.parametrize("pack_id", sorted(GOVERNANCE_PACKS))
def test_governance_pack_has_init(pack_id: str) -> None:
    init = RULES_DIR / pack_id / "__init__.py"
    assert init.exists(), f"missing __init__.py in {pack_id}"


def test_routing_pack_has_at_least_3_rules() -> None:
    doc = _load(RULES_DIR / "cve_rem.routing" / "pack.yaml")
    assert doc["flavor"] == "routing"
    assert isinstance(doc["rules"], list)
    assert len(doc["rules"]) >= 3, f"routing pack declares {len(doc['rules'])} rules; expected >= 3"


def test_routing_pack_rules_have_ids_and_targets() -> None:
    doc = _load(RULES_DIR / "cve_rem.routing" / "pack.yaml")
    for rule in doc["rules"]:
        assert "id" in rule and "when" in rule and "then" in rule
        for action in rule["then"]:
            assert action["kind"] in {"goto", "assert", "halt", "interrupt", "parallel"}


# --- IR <-> pack referential integrity ---------------------------------------


def _governance_ids_in(ir_path: Path) -> set[str]:
    doc = _load(ir_path)
    return {p["id"] for p in doc.get("governance", [])}


def test_main_ir_references_correct_packs() -> None:
    pack_ids = _governance_ids_in(MAIN_IR)
    assert "cve_rem.routing" in pack_ids
    assert "cve_rem.kill_switches" in pack_ids


def test_phase0_ir_references_doctrine_trust() -> None:
    pack_ids = _governance_ids_in(PHASE0_IR)
    assert "cve_rem.doctrine_trust" in pack_ids


def test_phase6_ir_references_isolation_and_score_policy() -> None:
    pack_ids = _governance_ids_in(PHASE6_IR)
    assert "cve_rem.offline_isolation" in pack_ids
    assert "cve_rem.gepa_score_policy" in pack_ids


def test_every_cve_rem_pack_referenced_somewhere() -> None:
    referenced = (
        _governance_ids_in(MAIN_IR)
        | _governance_ids_in(PHASE0_IR)
        | _governance_ids_in(PHASE6_IR)
    )
    cve_rem_referenced = {p for p in referenced if p.startswith("cve_rem.")}
    assert cve_rem_referenced == ALL_PACKS, (
        f"orphan packs (defined but not mounted): {ALL_PACKS - cve_rem_referenced}; "
        f"dangling refs (mounted but not defined): {cve_rem_referenced - ALL_PACKS}"
    )


# --- Specific content invariants ---------------------------------------------


def test_kill_switches_covers_all_metric_kinds() -> None:
    text = (RULES_DIR / "cve_rem.kill_switches" / "rules.clp").read_text()
    for kind in ("rollback-rate", "sandbox-mismatch", "cross-bucket", "stuck-state"):
        assert f'"{kind}"' in text, f"kill_switches missing rule for metric kind {kind}"


def test_kill_switches_quorum_covers_all_role_pairs() -> None:
    text = (RULES_DIR / "cve_rem.kill_switches" / "rules.clp").read_text()
    # 3-choose-2 = 3 quorum-collect rules
    quorum_rules = re.findall(r"\(defrule rollback-quorum-collect-[\w-]+", text)
    assert len(quorum_rules) == 3, f"expected 3 quorum-collect rules, found {len(quorum_rules)}"


def test_doctrine_trust_enforces_pin_immutability() -> None:
    text = (RULES_DIR / "cve_rem.doctrine_trust" / "rules.clp").read_text()
    assert "doctrine-pin-sha-divergence" in text


def test_offline_isolation_covers_three_invariants() -> None:
    text = (RULES_DIR / "cve_rem.offline_isolation" / "rules.clp").read_text()
    for rule_name in (
        "isolation-no-inbound-from-prod",
        "isolation-egress-only-to-approved-drop",
        "isolation-replica-load-without-redaction-pack",
    ):
        assert rule_name in text, f"offline_isolation missing rule {rule_name}"


def test_gepa_score_components_match_v6_locked_weights() -> None:
    text = (RULES_DIR / "cve_rem.gepa_score_policy" / "rules.clp").read_text()
    # v6-locked weights: 0.35 / 0.25 / 0.15 / 0.15 / 0.10
    assert "0.35" in text and "0.25" in text and "0.15" in text and "0.10" in text
    # all 5 component kinds named
    for kind in ("validation", "sandbox", "cr_approved", "no_drift_7d", "no_rollback_30d"):
        assert f'"{kind}"' in text, f"gepa_score_policy missing component {kind}"
