# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the CMDB CorrelateAgent.

Covers:

* CPE 2.3 URI → (vendor, product) extraction (dedup, ``*``/``-`` skip,
  malformed-URI tolerance).
* Multi-CPE fan-out: agent should issue one ``cmdb_query_software``
  per (vendor, product) × variant and aggregate hosts across all leads.
* Vendor-narrowed pass + vendor-free retry when scores are weak.
* Honest empty envelope when no CPE pair is parseable AND no fallback
  ``candidate_products`` is given.

The harbor ``@tool`` callables are monkey-patched on
``demos.cve_remediation.graph.correlate_agent`` so the test runs offline
(no httpx, no SERVICENOW_BASE_URL).
"""

from __future__ import annotations

import pytest

from demos.cve_remediation.graph import correlate_agent as ca


def test_extract_pairs_dedupes_and_skips_wildcards():
    uris = [
        "cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*",
        "cpe:2.3:a:apache:log4j:2.16.0:*:*:*:*:*:*:*",  # dup pair
        "cpe:2.3:a:cisco:adaptive_security_appliance_software:9.6:*:*:*:*:*:*:*",
        "cpe:2.3:a:*:*:1.0:*:*:*:*:*:*:*",  # wildcard pair — skipped
        "not-a-cpe-uri",                    # malformed — skipped
        "cpe:2.3:a:-:log4j:1.0:*:*:*:*:*:*:*",  # `-` vendor — skipped
    ]
    pairs = ca.extract_vendor_product_pairs(uris)
    assert pairs == [
        ("apache", "log4j"),
        ("cisco", "adaptive_security_appliance_software"),
    ]


def test_extract_pairs_empty_and_none():
    assert ca.extract_vendor_product_pairs([]) == []
    assert ca.extract_vendor_product_pairs(None) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_no_cpes_and_no_fallback_returns_empty_envelope():
    out = await ca.correlate_hosts_from_cpes(
        cpe_uris=[],
        candidate_products=None,
        score_candidate=lambda *_a, **_k: (0, "reject"),
        derive_variants=lambda tok, vendor: [tok],
    )
    assert out["status"] == "no_cpes"
    assert out["host_sys_ids"] == []
    assert out["host_names"] == []
    assert out["traces"] == []


@pytest.mark.asyncio
async def test_multi_cpe_fanout_aggregates_hosts(monkeypatch):
    """Two CPE pairs → two Software CIs → host union batch-resolved."""

    query_calls: list[dict[str, str]] = []
    traverse_calls: list[str] = []

    async def fake_query(*, name_like, vendor, limit=25):
        query_calls.append({"name_like": name_like, "vendor": vendor})
        if name_like == "log4j" and vendor == "apache":
            return {"rows": [
                {"sys_id": "spkg-log4j", "name": "Apache Log4j",
                 "vendor": "Apache", "version": "2.14.1"},
            ]}
        if name_like == "asa" or "adaptive" in name_like:
            return {"rows": [
                {"sys_id": "spkg-asa", "name": "Cisco ASA",
                 "vendor": "Cisco", "version": "9.6"},
            ]}
        return {"rows": []}

    async def fake_traverse(*, parent_sys_id, limit=200):
        traverse_calls.append(parent_sys_id)
        if parent_sys_id == "spkg-log4j":
            return {"child_sys_ids": ["host-worker-01", "host-rtr-01"]}
        if parent_sys_id == "spkg-asa":
            return {"child_sys_ids": ["host-rtr-01", "host-fw-01"]}  # rtr overlap
        return {"child_sys_ids": []}

    async def fake_resolve(*, sys_ids):
        names = {
            "host-worker-01": "h11-worker-01",
            "host-rtr-01": "h11-rtr-01",
            "host-fw-01": "h11-fw-01",
        }
        name_by = {sid: names.get(sid, "") for sid in sys_ids}
        return {
            "name_by_sys_id": name_by,
            "host_names": sorted({v for v in name_by.values() if v}),
        }

    monkeypatch.setattr(ca, "cmdb_query_software", fake_query)
    monkeypatch.setattr(ca, "cmdb_traverse_runs_on", fake_traverse)
    monkeypatch.setattr(ca, "cmdb_resolve_hosts", fake_resolve)

    def fake_score(row, vendor, product, variants):
        # Award high score whenever vendor matches the row vendor field.
        rv = (row.get("vendor") or "").lower()
        if vendor and vendor in rv:
            return (90, "high")
        return (10, "reject")

    def fake_variants(token, vendor):
        # cisco's product variants: include "asa" so the fake_query
        # branch fires regardless of the verbose CPE product.
        if token.startswith("adaptive"):
            return [token, "asa"]
        return [token]

    out = await ca.correlate_hosts_from_cpes(
        cpe_uris=[
            "cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*",
            "cpe:2.3:a:cisco:adaptive_security_appliance_software:9.6:*:*:*:*:*:*:*",
        ],
        candidate_products=None,
        score_candidate=fake_score,
        derive_variants=fake_variants,
    )

    assert out["status"] == "ok"
    assert out["host_sys_ids"] == ["host-fw-01", "host-rtr-01", "host-worker-01"]
    assert out["host_names"] == ["h11-fw-01", "h11-rtr-01", "h11-worker-01"]
    assert {tr.product for tr in out["traces"]} == {
        "log4j", "adaptive_security_appliance_software",
    }
    assert len(traverse_calls) == 2  # one walk per matched Software CI


@pytest.mark.asyncio
async def test_vendor_free_retry_kicks_in_when_narrow_pass_is_weak(monkeypatch):
    """Vendor-narrowed pass scores < 60 → agent retries vendor=""."""

    seen: list[dict[str, str]] = []

    async def fake_query(*, name_like, vendor, limit=25):
        seen.append({"name_like": name_like, "vendor": vendor})
        if vendor:
            # Vendor-narrowed pass returns a low-score row.
            return {"rows": [
                {"sys_id": "weak", "name": "Random Pkg", "vendor": "OtherCorp"},
            ]}
        # Vendor-free retry returns the real high-scoring row.
        return {"rows": [
            {"sys_id": "real", "name": "Big-IP", "vendor": "F5"},
        ]}

    async def fake_traverse(*, parent_sys_id, limit=200):
        if parent_sys_id == "real":
            return {"child_sys_ids": ["h-lb-01"]}
        return {"child_sys_ids": []}

    async def fake_resolve(*, sys_ids):
        return {
            "name_by_sys_id": {"h-lb-01": "lb-01"},
            "host_names": ["lb-01"],
        }

    monkeypatch.setattr(ca, "cmdb_query_software", fake_query)
    monkeypatch.setattr(ca, "cmdb_traverse_runs_on", fake_traverse)
    monkeypatch.setattr(ca, "cmdb_resolve_hosts", fake_resolve)

    def fake_score(row, vendor, product, variants):
        return (95, "high") if row.get("sys_id") == "real" else (20, "low")

    out = await ca.correlate_hosts_from_cpes(
        cpe_uris=["cpe:2.3:a:f5:big_ip:14.0:*:*:*:*:*:*:*"],
        candidate_products=None,
        score_candidate=fake_score,
        derive_variants=lambda tok, vendor: [tok, "big-ip"],
    )

    assert out["software_sys_id"] == "real"
    assert out["host_names"] == ["lb-01"]
    # Saw both vendor-narrowed and vendor-free passes.
    assert any(q["vendor"] == "f5" for q in seen)
    assert any(q["vendor"] == "" for q in seen)


@pytest.mark.asyncio
async def test_fallback_to_candidate_products_when_no_cpes(monkeypatch):
    async def fake_query(*, name_like, vendor, limit=25):
        return {"rows": [
            {"sys_id": "spkg-x", "name": "Foo Pkg", "vendor": ""},
        ]}

    async def fake_traverse(*, parent_sys_id, limit=200):
        return {"child_sys_ids": ["h-1"]}

    async def fake_resolve(*, sys_ids):
        return {"name_by_sys_id": {"h-1": "host-1"}, "host_names": ["host-1"]}

    monkeypatch.setattr(ca, "cmdb_query_software", fake_query)
    monkeypatch.setattr(ca, "cmdb_traverse_runs_on", fake_traverse)
    monkeypatch.setattr(ca, "cmdb_resolve_hosts", fake_resolve)

    out = await ca.correlate_hosts_from_cpes(
        cpe_uris=[],
        candidate_products=["foo"],
        score_candidate=lambda *_a, **_k: (80, "high"),
        derive_variants=lambda tok, vendor: [tok],
    )
    assert out["status"] == "ok"
    assert out["host_sys_ids"] == ["h-1"]


def test_version_in_range_no_constraints_keeps_host():
    # No advisory constraints → cannot exclude → keep.
    assert ca.version_in_range("2.4.50") is True


def test_version_in_range_wildcard_install_keeps_host():
    # Host install version unknown → no info to exclude → keep.
    assert ca.version_in_range(
        "*",
        affected_version_ranges=[{"versionEndExcluding": "5.0"}],
    ) is True
    assert ca.version_in_range(
        "",
        affected_version_ranges=[{"versionEndExcluding": "5.0"}],
    ) is True


def test_version_in_range_semver_endexcluding():
    rng = [{"versionStartIncluding": "2.4.0", "versionEndExcluding": "2.4.60"}]
    assert ca.version_in_range("2.4.50", affected_version_ranges=rng) is True
    assert ca.version_in_range("2.4.60", affected_version_ranges=rng) is False
    assert ca.version_in_range("1.0.0",  affected_version_ranges=rng) is False


def test_version_in_range_cisco_ios_lex_compare():
    rng = [{"versionStartIncluding": "12.2s", "versionEndIncluding": "15.1(3)svs"}]
    assert ca.version_in_range("15.0", affected_version_ranges=rng) is True
    assert ca.version_in_range("15.6", affected_version_ranges=rng) is False


def test_version_in_range_exact_match():
    assert ca.version_in_range(
        "2.4.50",
        exact_affected_versions=["2.4.50", "3.0"],
    ) is True
    # Outside literal list AND no range/fixed_version → False.
    assert ca.version_in_range(
        "2.4.51",
        exact_affected_versions=["2.4.50", "3.0"],
    ) is False


def test_version_in_range_fixed_version_fallback():
    # Only fixed_version supplied → install < fixed → True.
    assert ca.version_in_range("1.0", fixed_version="2.0") is True
    assert ca.version_in_range("3.0", fixed_version="2.0") is False
