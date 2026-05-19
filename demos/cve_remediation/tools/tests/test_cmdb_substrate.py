# SPDX-License-Identifier: Apache-2.0
"""Tests for cmdb_substrate CPE-driven substrate guard.

Decisions are derived mechanically from CPE 2.3 URIs against a
``SubstrateSpec`` (h11 = linux-x86_64 by default). No hand-authored
vendor/product table — these tests assert the URI parser + aggregation
behaviour generalize across vendors.
"""

from __future__ import annotations

from demos.cve_remediation.tools.cmdb_substrate import (
    DEFAULT_SUBSTRATE_SPEC,
    SubstrateSpec,
    apply_substrate_filter,
    classify_cpe,
    derive_substrate_profile_from_cpes,
    envelope_payload,
    extract_role_prefix,
    extract_role_tokens,
)


# ---------------------------------------------------------------------------
# Host-token helpers (unchanged behaviour)
# ---------------------------------------------------------------------------


def test_extract_role_prefix_standard_pattern():
    assert extract_role_prefix("h11-db-04") == "db"
    assert extract_role_prefix("h11-web-01") == "web"
    assert extract_role_prefix("h11-rtr-02") == "rtr"
    assert extract_role_prefix("h11-jump-01") == "jump"


def test_extract_role_prefix_no_num_suffix():
    assert extract_role_prefix("h11-api") == "api"


def test_extract_role_prefix_garbage_returns_empty():
    assert extract_role_prefix("") == ""


def test_extract_role_tokens_multi_segment():
    assert extract_role_tokens("h11-db-04") == ["h11", "db"]
    assert extract_role_tokens("laptop-nlp-dev-01") == ["laptop", "nlp", "dev"]
    assert extract_role_tokens("") == []
    assert extract_role_tokens("cmdb_ci.database_42") == ["cmdb", "ci", "database"]


# ---------------------------------------------------------------------------
# classify_cpe — single-URI decision
# ---------------------------------------------------------------------------


def _cpe(part, vendor, product, *, sw_edition="*", target_sw="*", target_hw="*"):
    return (
        f"cpe:2.3:{part}:{vendor}:{product}:1.0.0:*:*:*:{sw_edition}:"
        f"{target_sw}:{target_hw}:*"
    )


def test_classify_application_linux_target_applicable():
    d = classify_cpe(_cpe("a", "apache", "log4j", target_sw="*"))
    assert d.applicable
    assert d.part == "a"
    assert d.vendor == "apache"
    assert d.reason == "cpe_compatible_with_substrate"


def test_classify_windows_target_sw_denied():
    d = classify_cpe(_cpe("a", "microsoft", "ie", target_sw="windows_10"))
    assert not d.applicable
    assert "windows_10" in d.reason


def test_classify_apple_ios_denied():
    d = classify_cpe(_cpe("o", "apple", "iphone_os", target_sw="iphone_os"))
    # iphone_os target_sw is not in default linux allow-list; either denied
    # explicitly or by allow-list miss.
    assert not d.applicable


def test_classify_hardware_part_denied():
    d = classify_cpe(_cpe("h", "cisco", "router_x", target_sw="*"))
    assert not d.applicable
    assert "cpe_part='h'" in d.reason


def test_classify_unknown_target_sw_denied_by_allow_miss():
    d = classify_cpe(_cpe("a", "vendor", "prod", target_sw="esxi"))
    assert not d.applicable


def test_classify_arch_mismatch_denied():
    d = classify_cpe(_cpe("a", "apache", "log4j", target_sw="linux", target_hw="arm64"))
    assert not d.applicable
    assert "target_hw='arm64'" in d.reason


def test_classify_malformed_fails_open():
    d = classify_cpe("not-a-cpe-uri")
    assert d.applicable
    assert d.reason == "malformed_cpe_failopen"


def test_classify_custom_spec_allows_arm64():
    spec = SubstrateSpec(
        name="linux-arm64",
        allowed_target_hw=frozenset({"arm64", "*", "-", ""}),
    )
    d = classify_cpe(
        _cpe("a", "vendor", "prod", target_sw="linux", target_hw="arm64"), spec
    )
    assert d.applicable


# ---------------------------------------------------------------------------
# derive_substrate_profile_from_cpes — aggregation
# ---------------------------------------------------------------------------


def test_empty_cpe_list_failopen():
    profile, decisions = derive_substrate_profile_from_cpes([])
    assert profile.is_open()
    assert profile.rule_id == "cpe_list_empty"
    assert decisions == []


def test_all_incompatible_yields_denied_profile():
    profile, decisions = derive_substrate_profile_from_cpes([
        _cpe("a", "microsoft", "ie", target_sw="windows_10"),
        _cpe("a", "apple", "safari", target_sw="macos"),
        _cpe("o", "apple", "iphone_os", target_sw="iphone_os"),
    ])
    assert profile.rule_id == "cpe_substrate_denied"
    assert profile.deny_unmatched
    assert len(decisions) == 3
    assert not any(d.applicable for d in decisions)


def test_any_applicable_wins():
    """One linux row + many denied rows → applicable profile (open-ish)."""
    profile, decisions = derive_substrate_profile_from_cpes([
        _cpe("a", "microsoft", "ie", target_sw="windows_10"),
        _cpe("a", "apache", "log4j", target_sw="linux"),
        _cpe("o", "apple", "iphone_os", target_sw="iphone_os"),
    ])
    assert profile.rule_id == "cpe_substrate_applicable"
    assert profile.is_open()
    applicable_count = sum(1 for d in decisions if d.applicable)
    assert applicable_count == 1


def test_filter_drops_all_hosts_for_denied_profile():
    profile, _ = derive_substrate_profile_from_cpes([
        _cpe("a", "microsoft", "ie", target_sw="windows_10"),
    ])
    kept, decisions = apply_substrate_filter(
        ["h11-web-01", "h11-db-04", "h11-rtr-02"], profile
    )
    assert kept == []
    assert all(not d.allowed for d in decisions)


def test_filter_keeps_all_for_applicable_profile():
    profile, _ = derive_substrate_profile_from_cpes([
        _cpe("a", "apache", "log4j", target_sw="linux"),
    ])
    hosts = ["h11-web-01", "h11-db-04", "h11-rtr-02"]
    kept, _ = apply_substrate_filter(hosts, profile)
    assert kept == hosts


def test_filter_keeps_all_for_empty_cpe_failopen():
    profile, _ = derive_substrate_profile_from_cpes([])
    hosts = ["h11-web-01", "h11-db-04"]
    kept, _ = apply_substrate_filter(hosts, profile)
    assert kept == hosts


def test_empty_hostname_failsafe_allow_even_when_denied():
    profile, _ = derive_substrate_profile_from_cpes([
        _cpe("a", "microsoft", "ie", target_sw="windows_10"),
    ])
    kept, decisions = apply_substrate_filter(["", "---"], profile)
    assert kept == ["", "---"]
    assert all(
        d.reason == "unclassified_hostname_failsafe_allow" for d in decisions
    )


# ---------------------------------------------------------------------------
# Audit envelope
# ---------------------------------------------------------------------------


def test_envelope_payload_includes_cpe_decisions():
    cpe_uris = [
        _cpe("a", "microsoft", "ie", target_sw="windows_10"),
        _cpe("a", "apache", "log4j", target_sw="linux"),
    ]
    profile, cpe_decisions = derive_substrate_profile_from_cpes(cpe_uris)
    _, host_decisions = apply_substrate_filter(["h11-web-01"], profile)
    payload = envelope_payload(profile, host_decisions, cpe_decisions=cpe_decisions)
    assert payload["rule_id"] == "cpe_substrate_applicable"
    assert payload["cpe_applicable_count"] == 1
    assert payload["cpe_not_applicable_count"] == 1
    assert len(payload["cpe_decisions"]) == 2
    assert payload["kept_count"] == 1


def test_envelope_payload_without_cpe_decisions_omits_cpe_keys():
    profile, _ = derive_substrate_profile_from_cpes([])
    _, host_decisions = apply_substrate_filter(["h11-web-01"], profile)
    payload = envelope_payload(profile, host_decisions)
    assert "cpe_decisions" not in payload
    assert payload["rule_id"] == "cpe_list_empty"


def test_default_spec_is_linux_x86_64():
    assert DEFAULT_SUBSTRATE_SPEC.name == "linux-x86_64"
    assert "h" in DEFAULT_SUBSTRATE_SPEC.denied_cpe_parts
    assert "windows_10" in DEFAULT_SUBSTRATE_SPEC.denied_target_sw
