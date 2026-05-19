# SPDX-License-Identifier: Apache-2.0
"""Tests for E1 real node implementations.

Each real node is exercised directly: construct, call ``execute``,
assert the deterministic state-delta. No external services, no
LMs, no brokers — pure-Python verifiable behavior.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Any

import pytest
from pydantic import BaseModel

from demos.cve_remediation.graph.real_nodes import (
    CorrelateAssetsBrokerNode,
    GepaScoreComputerNode,
    ManifestSignNode,
    SourceTrustGateNode,
    SsvcTierEvaluatorNode,
    WriteArtifactRealNode,
    _classify_source_url,
)
from demos.cve_remediation.graph.state import CorrelatedAssets, CveExtract, CveRemState


class _Ctx(BaseModel):
    run_id: str = "e1-test"


# ---------------------------------------------------------------------------
# Source-trust gate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://nvd.nist.gov/vuln/detail/CVE-2026-0001", "trusted"),
        ("https://psirt.cisco.com/advisory-2026-001", "trusted"),
        ("https://github.com/advisories/GHSA-aaaa", "semi"),
        ("https://twitter.com/0day_news", "untrusted"),
        ("https://random-blog.example.com/post", "untrusted"),
        ("", "untrusted"),
    ],
)
def test_source_trust_classification(url: str, expected: str) -> None:
    assert _classify_source_url(url) == expected


def test_source_trust_gate_node_writes_state() -> None:
    node = SourceTrustGateNode()
    state = CveRemState(raw_source_url="https://nvd.nist.gov/vuln/detail/CVE-X")
    out = asyncio.run(node.execute(state, _Ctx()))
    assert out == {"source_trust": "trusted"}


# ---------------------------------------------------------------------------
# SSVC tier evaluator
# ---------------------------------------------------------------------------


def _state_with_extract(
    *,
    cvss_bp: int = 0,
    epss_bp: int = 0,
    kev: bool = False,
    blast: int = 0,
) -> CveRemState:
    return CveRemState(
        extract=CveExtract(
            cvss_score_bp=cvss_bp,
            epss_score_bp=epss_bp,
            kev_listed=kev,
        ),
        correlated=CorrelatedAssets(blast_radius_node_count=blast),
    )


@pytest.mark.parametrize(
    "kwargs,expected_tier",
    [
        # KEV listed → ACT_AUTO regardless of score
        ({"kev": True, "cvss_bp": 100, "blast": 0}, "act_auto"),
        # cvss=9.0 + blast >= 100 → ACT_AUTO
        ({"cvss_bp": 900, "blast": 100}, "act_auto"),
        # cvss=7.5 + epss=0.10 → ACT_HITL_REQUIRED
        ({"cvss_bp": 750, "epss_bp": 1000}, "act_hitl_required"),
        # cvss=5.0 → ATTEND
        ({"cvss_bp": 500}, "attend"),
        # cvss=3.0 + blast=0 → DEFER
        ({"cvss_bp": 300, "blast": 0}, "defer"),
        # cvss=2.0 + blast=5 → TRACK (low cvss but has blast)
        ({"cvss_bp": 200, "blast": 5}, "track"),
    ],
)
def test_ssvc_tier_evaluator(kwargs: dict[str, Any], expected_tier: str) -> None:
    node = SsvcTierEvaluatorNode()
    state = _state_with_extract(**kwargs)
    out = asyncio.run(node.execute(state, _Ctx()))
    assert out == {"ssvc_tier": expected_tier}


# ---------------------------------------------------------------------------
# GEPA score computer
# ---------------------------------------------------------------------------


def test_gepa_score_full_marks_yields_10000_bp() -> None:
    """All components at 10000 → weighted sum 10000 (full)."""
    node = GepaScoreComputerNode()
    state = CveRemState(
        gepa_components={
            "validation": 10000,
            "sandbox": 10000,
            "cr_approved": 10000,
            "no_drift_7d": 10000,
            "no_rollback_30d": 10000,
        },
        current_score_bp=0,
        epsilon_margin_bp=200,
    )
    out = asyncio.run(node.execute(state, _Ctx()))
    assert out["candidate_score_bp"] == 10000  # 100% weighted score
    assert out["strictly_better"] is True


def test_gepa_score_at_floor_yields_zero() -> None:
    node = GepaScoreComputerNode()
    state = CveRemState(
        gepa_components={k: 0 for k in (
            "validation", "sandbox", "cr_approved", "no_drift_7d", "no_rollback_30d"
        )},
        current_score_bp=8000,
    )
    out = asyncio.run(node.execute(state, _Ctx()))
    assert out["candidate_score_bp"] == 0
    assert out["strictly_better"] is False


def test_gepa_score_strictly_better_respects_epsilon() -> None:
    """Candidate beats current by exactly epsilon → strictly_better."""
    node = GepaScoreComputerNode()
    # validation=8000, others=8000 → score = 8000
    state = CveRemState(
        gepa_components={k: 8000 for k in (
            "validation", "sandbox", "cr_approved", "no_drift_7d", "no_rollback_30d"
        )},
        current_score_bp=7800,
        epsilon_margin_bp=200,  # delta = 200, exactly epsilon → True
    )
    out = asyncio.run(node.execute(state, _Ctx()))
    assert out["candidate_score_bp"] == 8000
    assert out["strictly_better"] is True


# ---------------------------------------------------------------------------
# Doctrine manifest sign
# ---------------------------------------------------------------------------


def test_manifest_sign_deterministic() -> None:
    node = ManifestSignNode()
    state = CveRemState(
        doctrine_node_count=42,
        doctrine_edge_count=100,
        corpus_sha256="abc123",
    )
    out_a = asyncio.run(node.execute(state, _Ctx()))
    out_b = asyncio.run(node.execute(state, _Ctx()))
    assert out_a == out_b  # idempotent
    assert len(out_a["doctrine_manifest_hash"]) == 64
    assert len(out_a["manifest_signature"]) == 64


def test_manifest_sign_changes_on_corpus_change() -> None:
    node = ManifestSignNode()
    state_1 = CveRemState(corpus_sha256="abc")
    state_2 = CveRemState(corpus_sha256="xyz")
    out_1 = asyncio.run(node.execute(state_1, _Ctx()))
    out_2 = asyncio.run(node.execute(state_2, _Ctx()))
    assert out_1["doctrine_manifest_hash"] != out_2["doctrine_manifest_hash"]


# ---------------------------------------------------------------------------
# WriteArtifact real node
# ---------------------------------------------------------------------------


def test_write_artifact_writes_content_addressed_file(tmp_path) -> None:
    os.environ["HARBOR_ARTIFACTS_ROOT"] = str(tmp_path)
    try:
        # Re-import to pick up env override
        import importlib

        import demos.cve_remediation.graph.real_nodes as rn

        importlib.reload(rn)

        node = rn.WriteArtifactRealNode()
        state = CveRemState(cve_id="CVE-2026-9999")
        out = asyncio.run(node.execute(state, _Ctx()))

        assert out["last_artifact_uri"].startswith("file://")
        assert len(out["last_artifact_hash"]) == 64
        # File exists at expected path
        assert (tmp_path / f"{out['last_artifact_hash']}.json").is_file()
    finally:
        os.environ.pop("HARBOR_ARTIFACTS_ROOT", None)


# ---------------------------------------------------------------------------
# E3: CorrelateAssets broker node (typed-intent envelope)
# ---------------------------------------------------------------------------


def test_correlate_assets_builds_typed_envelope() -> None:
    node = CorrelateAssetsBrokerNode()
    state = CveRemState(
        cve_id="CVE-2026-1",
        extract=CveExtract(
            cve_id="CVE-2026-1",
            affected_products=["nginx", "apache"],
            affected_versions=["1.2", "2.4"],
        ),
    )
    out = asyncio.run(node.execute(state, _Ctx()))
    env = out["broker_request_envelope"]
    assert env["agent_id"] == "cve-rem-pipeline"
    assert env["intent"] == "cve_rem.correlate_assets"
    ctx = env["context"]
    assert ctx["cve_id"] == "CVE-2026-1"
    assert ctx["affected_products"] == ["nginx", "apache"]
    assert ctx["affected_versions"] == ["1.2", "2.4"]
    assert "intent_name" not in ctx  # excluded by build_intent_context
    assert out["last_broker_intent"] == "cve_rem.correlate_assets"


def test_correlate_assets_handles_missing_extract() -> None:
    """Empty extract → empty product/version lists, no crash."""
    node = CorrelateAssetsBrokerNode()
    state = CveRemState(cve_id="CVE-2026-2")
    out = asyncio.run(node.execute(state, _Ctx()))
    ctx = out["broker_request_envelope"]["context"]
    assert ctx["cve_id"] == "CVE-2026-2"
    assert ctx["affected_products"] == []
    assert ctx["affected_versions"] == []


def test_write_artifact_idempotent_on_same_state(tmp_path) -> None:
    os.environ["HARBOR_ARTIFACTS_ROOT"] = str(tmp_path)
    try:
        import importlib

        import demos.cve_remediation.graph.real_nodes as rn

        importlib.reload(rn)

        node = rn.WriteArtifactRealNode()
        state = CveRemState(cve_id="CVE-2026-1111")
        out_a = asyncio.run(node.execute(state, _Ctx()))
        out_b = asyncio.run(node.execute(state, _Ctx()))
        assert out_a["last_artifact_hash"] == out_b["last_artifact_hash"]
    finally:
        os.environ.pop("HARBOR_ARTIFACTS_ROOT", None)
