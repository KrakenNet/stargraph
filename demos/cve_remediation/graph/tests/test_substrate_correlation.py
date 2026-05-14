# SPDX-License-Identifier: Apache-2.0
"""Integration tests for substrate filtering in CorrelateAssetsBrokerNode.

The substrate filter unit tests (tools/tests/test_cmdb_substrate.py)
exercise the decision logic in isolation. These tests exercise the
broker-node integration: substrate audit lands in
broker_request_envelope, critic_deficits is populated on
substrate_denied, and cmdb_match_quality propagates through.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from demos.cve_remediation.graph.real_nodes import CorrelateAssetsBrokerNode
from demos.cve_remediation.graph.state import CorrelatedAssets, CveRemState


class _Ctx:
    pass


def _make_state(**kw) -> CveRemState:
    return CveRemState(
        cve_id=kw.get("cve_id", "CVE-X-1"),
        cve_vendor=kw.get("cve_vendor", ""),
        cve_product=kw.get("cve_product", ""),
        candidate_products=kw.get("candidate_products", []),
    )


def test_substrate_denied_surfaces_critic_deficit(monkeypatch):
    """When _cmdb_traverse returns substrate_denied, CorrelateAssetsBrokerNode
    appends a critic_deficits {kind:substrate_mismatch} entry and
    propagates the audit envelope."""
    fake_cmdb = {
        "correlated": CorrelatedAssets(
            affected_assets=[],
            cmdb_match_set=[],
            disposition="not_applicable",
        ),
        "cmdb_software_sys_id": "",
        "cmdb_software_name": "",
        "cmdb_query_count": 0,
        "affected_host_names": [],
        "disposition": "not_applicable",
        "cmdb_match_score": 80,
        "cmdb_match_quality": "substrate_denied",
        "substrate_filter": {
            "rule_id": "apache-log4j",
            "denied_role_prefixes": ["db", "storage"],
            "allowed_role_prefixes": [],
            "deny_unmatched": False,
            "cpe_part": "a",
            "reason": "Apache Log4j substrate excludes db/storage",
            "decisions": [
                {
                    "host_name": "h11-db-04",
                    "role_prefix": "db",
                    "allowed": False,
                    "reason": "role='db' denied by apache-log4j",
                },
            ],
            "dropped_count": 1,
            "kept_count": 0,
        },
    }

    async def _stub_traverse(self, vendor, product, **kw):
        return fake_cmdb

    async def _stub_cargonet(self, host_names):
        return {}

    monkeypatch.setattr(
        CorrelateAssetsBrokerNode, "_cmdb_traverse", _stub_traverse
    )
    monkeypatch.setattr(
        CorrelateAssetsBrokerNode, "_cargonet_match_by_name", _stub_cargonet
    )

    state = _make_state(cve_id="CVE-2021-44228", cve_vendor="apache",
                        cve_product="log4j", candidate_products=["log4j"])
    out = asyncio.run(CorrelateAssetsBrokerNode().execute(state, _Ctx()))

    assert out.get("cmdb_match_quality") == "substrate_denied"
    deficits = out.get("critic_deficits") or []
    assert len(deficits) == 1
    deficit = deficits[0]
    assert deficit["kind"] == "substrate_mismatch"
    assert deficit["slot"] == "correlate"
    # The deficit detail records the derived substrate audit
    # (rule_id + dropped count). The specific rule_id depends on the
    # state's cpe_uris; this test seeds none so the derived profile is
    # ``cpe_list_empty``. Shape is what matters for downstream consumers.
    assert "rule=" in deficit["detail"]
    assert "dropped=" in deficit["detail"]
    # Audit envelope carries the derived substrate payload. Production
    # overwrites any stub-provided substrate_filter with the derived
    # audit so downstream sees the authoritative rule_id.
    env = out["broker_request_envelope"]
    assert env.get("substrate_filter", {}).get("rule_id")


def test_substrate_pass_does_not_emit_deficit(monkeypatch):
    """Substrate audit present but every host kept → no critic_deficit."""
    fake_cmdb = {
        "correlated": CorrelatedAssets(
            affected_assets=["sys-1"],
            cmdb_match_set=["sys-1"],
            disposition="applicable",
        ),
        "cmdb_software_sys_id": "sw-1",
        "cmdb_software_name": "Apache HTTPD",
        "cmdb_query_count": 1,
        "affected_host_names": ["h11-web-01"],
        "disposition": "applicable",
        "cmdb_match_score": 90,
        "cmdb_match_quality": "high",
        "substrate_filter": {
            "rule_id": "apache-httpd",
            "denied_role_prefixes": ["db", "storage", "idp"],
            "allowed_role_prefixes": [],
            "deny_unmatched": False,
            "cpe_part": "a",
            "reason": "Apache HTTPD substrate excludes db/storage/idp",
            "decisions": [
                {
                    "host_name": "h11-web-01",
                    "role_prefix": "web",
                    "allowed": True,
                    "reason": "no_constraint",
                },
            ],
            "dropped_count": 0,
            "kept_count": 1,
        },
    }

    async def _stub_traverse(self, vendor, product, **kw):
        return fake_cmdb

    async def _stub_cargonet(self, host_names):
        return {}

    monkeypatch.setattr(
        CorrelateAssetsBrokerNode, "_cmdb_traverse", _stub_traverse
    )
    monkeypatch.setattr(
        CorrelateAssetsBrokerNode, "_cargonet_match_by_name", _stub_cargonet
    )

    state = _make_state(cve_id="CVE-X-2", cve_vendor="apache",
                        cve_product="http_server",
                        candidate_products=["http_server"])
    out = asyncio.run(CorrelateAssetsBrokerNode().execute(state, _Ctx()))

    assert out.get("cmdb_match_quality") == "high"
    assert "critic_deficits" not in out
    # Audit envelope still carries the (no-op) substrate audit
    env = out["broker_request_envelope"]
    assert env.get("substrate_filter", {}).get("kept_count") == 1
