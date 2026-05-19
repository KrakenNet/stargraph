# SPDX-License-Identifier: Apache-2.0
"""Tests for planner_schema PlanSpec derivation + validation."""

from __future__ import annotations

from types import SimpleNamespace

from demos.cve_remediation.tools.planner_schema import (
    PlanSpec,
    PlanStep,
    derive_plan_spec,
    validate_plan_spec,
)


def _action(**kw):
    """Lightweight stand-in for RemediationAction (duck-typed)."""
    defaults = {
        "kind": "",
        "target": "",
        "target_version": "",
        "change": "",
        "rationale": "",
        "citation_url": "",
        "citation_excerpt": "",
        "source": "",
        "confidence_bp": 0,
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def test_derive_honest_skip_when_no_actions():
    spec = derive_plan_spec(
        cve_id="CVE-X", cwe="CWE-1", vuln_class="library",
        fixed_version="1.2.3", recommended_actions=[],
    )
    assert spec.honest_skip is True
    assert "no_recommended_actions" in spec.deficit_reasons
    assert not spec.is_complete()


def test_derive_honest_skip_when_only_non_invertible():
    actions = [
        _action(kind="mitigation_only", target="webapp", confidence_bp=5000,
                citation_url="https://cve.org/x"),
    ]
    spec = derive_plan_spec(
        cve_id="CVE-X", cwe="CWE-1", vuln_class="library",
        fixed_version="", recommended_actions=actions,
    )
    assert spec.honest_skip is True
    assert any("no_invertible_primitive" in r for r in spec.deficit_reasons)


def test_derive_upgrade_complete_spec():
    actions = [
        _action(
            kind="upgrade", target="openssl", target_version="3.0.10",
            confidence_bp=8000, citation_url="https://nvd.nist.gov/X",
        ),
    ]
    spec = derive_plan_spec(
        cve_id="CVE-X", cwe="CWE-787", vuln_class="library",
        fixed_version="3.0.10", recommended_actions=actions,
    )
    assert spec.honest_skip is False
    assert spec.apply.primitive == "upgrade"
    assert spec.apply.target == "openssl"
    assert spec.apply.target_version == "3.0.10"
    assert spec.verify.primitive == "probe"
    assert spec.rollback.primitive == "downgrade"
    assert spec.regression.primitive == "healthcheck"
    # upgrade has no prior version → rollback target_version empty
    assert spec.rollback.target_version == ""
    assert "non_invertible_rollback" in spec.deficit_reasons
    assert spec.is_complete()


def test_derive_downgrade_invertible():
    actions = [
        _action(
            kind="downgrade", target="xz-utils", target_version="5.4.5-1",
            confidence_bp=8000, citation_url="https://debian.org/X",
        ),
    ]
    spec = derive_plan_spec(
        cve_id="CVE-X", cwe="CWE-506", vuln_class="library",
        fixed_version="5.6.2", recommended_actions=actions,
    )
    assert spec.apply.primitive == "downgrade"
    assert spec.rollback.primitive == "upgrade"
    # rollback target_version = fixed_version (re-upgrade to known fix)
    assert spec.rollback.target_version == "5.6.2"
    assert "non_invertible_rollback" not in spec.deficit_reasons


def test_derive_disable_invertible():
    actions = [
        _action(
            kind="disable", target="vulnerable-svc",
            confidence_bp=7000, citation_url="https://example.org/Y",
        ),
    ]
    spec = derive_plan_spec(
        cve_id="CVE-X", cwe="CWE-1", vuln_class="business-rule",
        fixed_version="", recommended_actions=actions,
    )
    assert spec.apply.primitive == "disable"
    assert spec.verify.intent == "probe service vulnerable-svc is not active"
    assert spec.rollback.primitive == "enable"


def test_derive_highest_confidence_wins():
    actions = [
        _action(kind="upgrade", target="pkg-a", target_version="1",
                confidence_bp=4000, citation_url="x"),
        _action(kind="upgrade", target="pkg-b", target_version="2",
                confidence_bp=9000, citation_url="y"),
    ]
    spec = derive_plan_spec(
        cve_id="X", cwe="", vuln_class="", fixed_version="",
        recommended_actions=actions,
    )
    assert spec.apply.target == "pkg-b"


def test_validate_clean_spec_returns_empty():
    spec = derive_plan_spec(
        cve_id="X", cwe="", vuln_class="",
        fixed_version="3.0.0",
        recommended_actions=[
            _action(kind="downgrade", target="lib", target_version="2.9.9",
                    confidence_bp=8000, citation_url="https://src/A"),
        ],
    )
    deficits = validate_plan_spec(
        spec, allowed_citations=["https://src/A"]
    )
    # downgrade has valid target_version + cite — clean
    assert deficits == []


def test_validate_fabricated_citation():
    spec = PlanSpec(
        apply=PlanStep(
            intent="upgrade foo", primitive="upgrade", target="foo",
            target_version="2.0", cite_url="https://fake/bad",
        ),
        verify=PlanStep(intent="probe", primitive="probe", target="foo"),
        rollback=PlanStep(intent="downgrade", primitive="downgrade",
                          target="foo", target_version="1.0"),
        regression=PlanStep(intent="check", primitive="healthcheck",
                            target="foo"),
    )
    deficits = validate_plan_spec(
        spec, allowed_citations=["https://real/A"]
    )
    kinds = [d["kind"] for d in deficits]
    assert "fabricated_citation" in kinds


def test_validate_version_unspecified():
    spec = PlanSpec(
        apply=PlanStep(intent="upgrade", primitive="upgrade", target="foo",
                       target_version=""),
        verify=PlanStep(intent="probe", primitive="probe", target="foo"),
        rollback=PlanStep(intent="hold", primitive="hold_package",
                          target="foo"),
        regression=PlanStep(intent="check", primitive="healthcheck",
                            target="foo"),
    )
    deficits = validate_plan_spec(spec, allowed_citations=[])
    kinds = [d["kind"] for d in deficits]
    assert "version_unspecified" in kinds


def test_validate_unsafe_primitive_empty_rollback_target():
    spec = PlanSpec(
        apply=PlanStep(intent="block port", primitive="block_port",
                       target="tcp/8080"),
        verify=PlanStep(intent="probe", primitive="probe", target="tcp/8080"),
        rollback=PlanStep(intent="unblock", primitive="block_port",
                          target=""),
        regression=PlanStep(intent="check", primitive="healthcheck",
                            target="tcp/8080"),
    )
    deficits = validate_plan_spec(spec, allowed_citations=[])
    kinds = [d["kind"] for d in deficits]
    assert "unsafe_primitive" in kinds


def test_deficits_from_incomplete_spec():
    spec = PlanSpec(
        apply=PlanStep(intent="upgrade", primitive="upgrade", target="x",
                       target_version="1.0"),
        # verify + rollback + regression empty
    )
    out = spec.deficits()
    kinds = [d["kind"] for d in out]
    assert "missing_verify_probe" in kinds
    assert "missing_rollback" in kinds
    assert "missing_regression" in kinds
    assert "missing_apply" not in kinds
