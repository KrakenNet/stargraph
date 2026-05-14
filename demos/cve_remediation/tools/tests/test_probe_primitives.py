# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tools/probe_primitives.py — Phase B."""
from __future__ import annotations

import yaml

from demos.cve_remediation.tools.probe_primitives import (
    IoCSet,
    block_port_spec,
    build_isolate_bundle,
    disable_service_spec,
    extract_iocs_from_advisory,
    hold_package_spec,
    quarantine_file_spec,
    spec_for_action,
    synthesize_isolate_actions_from_iocs,
)


class _MockAction:
    def __init__(self, kind: str, target: str) -> None:
        self.kind = kind
        self.target = target


def test_disable_service_spec_emits_apply_verify_rollback() -> None:
    spec = disable_service_spec("nginx")
    assert spec["apply"][0]["ansible.builtin.service"]["state"] == "stopped"
    assert any("verify" in t["name"].lower() for t in spec["verify"])
    assert any("rollback" in t["name"].lower() for t in spec["rollback"])


def test_block_port_spec_validates_port() -> None:
    assert block_port_spec(0) == {}
    assert block_port_spec(70000) == {}
    spec = block_port_spec(8080, "tcp")
    assert spec["apply"][0]["name"] == "block-tcp-8080"
    assert "verify" in spec["verify"][0]["name"]


def test_hold_package_spec_apt_channel() -> None:
    spec = hold_package_spec("log4j-core", channel="apt")
    cmd = spec["apply"][0]["ansible.builtin.shell"]
    assert "apt-mark hold log4j-core" in cmd


def test_quarantine_file_requires_abs_path() -> None:
    assert quarantine_file_spec("relative/path") == {}
    spec = quarantine_file_spec("/var/www/eval-stdin.php")
    assert "quarantine" in spec["apply"][0]["name"]
    assert "test ! -e" in spec["verify"][0]["ansible.builtin.shell"]


def test_extract_iocs_from_advisory_finds_ports_paths_services() -> None:
    body = (
        "The vulnerability is in port 8443 of the management service. "
        "An attacker can read /var/log/secret.log via a crafted request. "
        "Mitigation: disable the auth-proxy service until patched."
    )
    iocs = extract_iocs_from_advisory(body)
    assert 8443 in iocs.ports
    assert "/var/log/secret.log" in iocs.file_paths
    assert "auth-proxy" in iocs.services


def test_extract_iocs_empty_input_returns_empty() -> None:
    assert extract_iocs_from_advisory("").is_empty()
    assert extract_iocs_from_advisory("just prose, no IoCs").is_empty()


def test_synthesize_isolate_actions_grounds_in_excerpt() -> None:
    body = (
        "Vulnerable to RCE via port 9001 of the demo service. "
        "Recommended action: block port 9001 at the perimeter. "
        "Files at /opt/vendor/exploit-target.dat are at risk."
    )
    iocs = extract_iocs_from_advisory(body)
    actions = synthesize_isolate_actions_from_iocs(
        iocs,
        advisory_url="https://example.test/cve/2099",
        advisory_body=body,
    )
    assert actions, "expected at least one synthesized action"
    for a in actions:
        # Every action MUST cite a real excerpt — no fabrication.
        assert a["citation_url"] == "https://example.test/cve/2099"
        assert a["citation_excerpt"], "missing citation_excerpt"
        assert a["source"] == "advisory_iocs"
        assert a["kind"] in ("isolate", "disable", "quarantine")


def test_synthesize_returns_empty_on_no_advisory_url() -> None:
    iocs = IoCSet(ports=[80])
    assert synthesize_isolate_actions_from_iocs(
        iocs, advisory_url="", advisory_body="port 80"
    ) == []


def test_spec_for_action_dispatch() -> None:
    spec = spec_for_action(_MockAction("isolate", "tcp/9001"))
    assert spec["apply"][0]["name"] == "block-tcp-9001"

    spec = spec_for_action(_MockAction("disable", "nginx.service"))
    assert spec["apply"][0]["ansible.builtin.service"]["name"] == "nginx.service"

    spec = spec_for_action(_MockAction("quarantine", "/opt/bad.dat"))
    assert "quarantine" in spec["apply"][0]["name"]

    assert spec_for_action(_MockAction("upgrade", "pkg@1.0")) == {}


def test_build_isolate_bundle_yields_valid_playbook() -> None:
    actions = [
        _MockAction("isolate", "tcp/4444"),
        _MockAction("disable", "vuln-svc"),
    ]
    apply_yaml, rollback_yaml = build_isolate_bundle(
        actions, plan_hash="abcd1234", cve_id="CVE-2099-9999"
    )
    parsed = yaml.safe_load(apply_yaml)
    assert isinstance(parsed, list) and parsed
    play = parsed[0]
    assert play["hosts"] == "all"
    assert any("verify" in t["name"].lower() for t in play["tasks"])
    assert yaml.safe_load(rollback_yaml)[0]["tasks"]


def test_build_isolate_bundle_empty_on_no_primitive_kinds() -> None:
    actions = [_MockAction("upgrade", "pkg")]
    a, r = build_isolate_bundle(actions, plan_hash="x", cve_id="CVE-x")
    assert a == "" and r == ""
