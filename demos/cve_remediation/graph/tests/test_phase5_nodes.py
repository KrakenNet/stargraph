# SPDX-License-Identifier: Apache-2.0
"""Tests for Phase 5 retro + learn real node bodies (S3.5)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from demos.cve_remediation.graph.real_nodes import (
    CargoNetWritebackNode,
    EmitDocxArchiveNode,
    EmitRetroPayloadNode,
    HitlRetrospectiveReviewNode,
    PlanKgWritebackNode,
    PublishDocPlusNode,
    RenderDocxNode,
    WriteRetrospectiveNode,
)
from demos.cve_remediation.graph.state import CveRemState


def _ctx() -> object:
    return object()


@pytest.fixture
def isolated_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HARBOR_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv("CVE_REM_DOCTRINE_ALLOWLIST", str(tmp_path / "allow.json"))
    import importlib

    import demos.cve_remediation.graph.real_nodes as rn_mod

    importlib.reload(rn_mod)
    return tmp_path / "artifacts"


# ---------------------------------------------------------------------------
# write_retrospective
# ---------------------------------------------------------------------------


def test_write_retrospective_patched() -> None:
    state = CveRemState(verify_outcome="patched", cve_id="CVE-X", plan_hash="abc")
    out = asyncio.run(WriteRetrospectiveNode().execute(state, _ctx()))
    assert out["retro_outcome"] == "patched"
    assert len(out["retro_id"]) == 16


def test_write_retrospective_rollback() -> None:
    state = CveRemState(rollback_triggered=True, cve_id="CVE-Y")
    out = asyncio.run(WriteRetrospectiveNode().execute(state, _ctx()))
    assert out["retro_outcome"] == "rollback"


def test_write_retrospective_divergence() -> None:
    state = CveRemState(verify_outcome="divergence")
    out = asyncio.run(WriteRetrospectiveNode().execute(state, _ctx()))
    assert out["retro_outcome"] == "divergence"


def test_write_retrospective_deterministic() -> None:
    state = CveRemState(verify_outcome="patched", cve_id="CVE-X", plan_hash="abc")
    a = asyncio.run(WriteRetrospectiveNode().execute(state, _ctx()))
    b = asyncio.run(WriteRetrospectiveNode().execute(state, _ctx()))
    assert a == b


# ---------------------------------------------------------------------------
# emit_retro_payload
# ---------------------------------------------------------------------------


def test_emit_retro_payload_writes_file(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import EmitRetroPayloadNode

    state = CveRemState(
        retro_id="r-1",
        retro_outcome="patched",
        cve_id="CVE-X",
        execution_ledger=["canary:ok", "stage:ok"],
    )
    out = asyncio.run(EmitRetroPayloadNode().execute(state, _ctx()))
    assert out["retro_payload_artifact_ref"].startswith("file://")
    files = list((isolated_artifacts / "retro").glob("*.json"))
    assert len(files) >= 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["retro_id"] == "r-1"


# ---------------------------------------------------------------------------
# render_docx + emit_docx_archive
# ---------------------------------------------------------------------------


def test_render_docx_writes_md(isolated_artifacts: Path) -> None:
    from demos.cve_remediation.graph.real_nodes import RenderDocxNode

    state = CveRemState(
        cve_id="CVE-X",
        retro_outcome="patched",
        cr_correlation_id="CR-Y",
        verify_outcome="patched",
    )
    out = asyncio.run(RenderDocxNode().execute(state, _ctx()))
    md_path = Path(out["broker_request_envelope"]["docx_source_md"])
    assert md_path.is_file()
    assert "CVE-X" in md_path.read_text(encoding="utf-8")


def test_emit_docx_archive_wraps_md(isolated_artifacts: Path) -> None:
    """EmitDocxArchiveNode writes a real .docx (OOXML ZIP container) now,
    not a JSON wrapper. Validate the file shape (PK ZIP magic) and that
    the docx unzips to OOXML parts containing the narrative text."""
    import zipfile

    from demos.cve_remediation.graph.real_nodes import (
        EmitDocxArchiveNode,
        RenderDocxNode,
    )

    state = CveRemState(cve_id="CVE-X", retro_outcome="patched")
    rendered = asyncio.run(RenderDocxNode().execute(state, _ctx()))
    state = state.model_copy(update=rendered)
    out = asyncio.run(EmitDocxArchiveNode().execute(state, _ctx()))
    assert out["docx_artifact_ref"].startswith("file://")
    assert out["docplus_staging_ref"] == out["docx_artifact_ref"]
    archive_path = Path(out["docx_artifact_ref"][len("file://"):])
    assert archive_path.is_file()
    # OOXML containers are ZIP files starting with PK signature.
    assert archive_path.read_bytes()[:2] == b"PK"
    with zipfile.ZipFile(archive_path) as zf:
        # word/document.xml is the canonical body part of a .docx
        assert "word/document.xml" in zf.namelist()
        doc_xml = zf.read("word/document.xml").decode("utf-8")
        assert "CVE-X" in doc_xml


# ---------------------------------------------------------------------------
# publish_docplus
# ---------------------------------------------------------------------------


def test_publish_docplus_emits_envelope() -> None:
    state = CveRemState(docx_artifact_ref="file:///tmp/docx.json")
    out = asyncio.run(PublishDocPlusNode().execute(state, _ctx()))
    assert out["docplus_published"] is True
    assert out["last_broker_intent"] == "cve_rem.publish_docplus"


def test_publish_docplus_no_ref_skips() -> None:
    state = CveRemState(docx_artifact_ref="")
    out = asyncio.run(PublishDocPlusNode().execute(state, _ctx()))
    assert out == {"docplus_published": False}


# ---------------------------------------------------------------------------
# cargonet_writeback
# ---------------------------------------------------------------------------


def test_cargonet_writeback_emits_envelope() -> None:
    state = CveRemState(retro_id="r-abc", retro_outcome="patched")
    out = asyncio.run(CargoNetWritebackNode().execute(state, _ctx()))
    assert out["cargonet_writeback_done"] is True
    assert out["last_broker_intent"] == "cve_rem.cargonet_writeback"
    payload = out["broker_request_envelope"]["context"]
    assert payload["success"] is True


def test_cargonet_writeback_rollback_marks_failure() -> None:
    state = CveRemState(retro_id="r-abc", retro_outcome="rollback")
    out = asyncio.run(CargoNetWritebackNode().execute(state, _ctx()))
    payload = out["broker_request_envelope"]["context"]
    assert payload["success"] is False


# ---------------------------------------------------------------------------
# plan_kg_writeback
# ---------------------------------------------------------------------------


def test_plan_kg_writeback_appends_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use the env-overridable allowlist path so the KG file lands in tmp_path."""
    monkeypatch.setenv("CVE_REM_DOCTRINE_ALLOWLIST", str(tmp_path / "allow.json"))
    import importlib

    import demos.cve_remediation.graph.real_nodes as rn_mod

    importlib.reload(rn_mod)

    state = CveRemState(
        cve_id="CVE-Z", plan_hash="abc", retro_outcome="patched"
    )
    asyncio.run(rn_mod.PlanKgWritebackNode().execute(state, _ctx()))
    kg_path = tmp_path / "doctrine_kg.json"
    assert kg_path.is_file()
    payload = json.loads(kg_path.read_text(encoding="utf-8"))
    assert any("VERIFIED_ON(patched)" in e for e in payload["edges"])


def test_plan_kg_writeback_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CVE_REM_DOCTRINE_ALLOWLIST", str(tmp_path / "allow.json"))
    import importlib

    import demos.cve_remediation.graph.real_nodes as rn_mod

    importlib.reload(rn_mod)
    state = CveRemState(cve_id="CVE-Z", plan_hash="abc", retro_outcome="patched")
    asyncio.run(rn_mod.PlanKgWritebackNode().execute(state, _ctx()))
    asyncio.run(rn_mod.PlanKgWritebackNode().execute(state, _ctx()))
    payload = json.loads(
        (tmp_path / "doctrine_kg.json").read_text(encoding="utf-8")
    )
    matches = [e for e in payload["edges"] if "VERIFIED_ON" in e]
    assert len(matches) == 1


# ---------------------------------------------------------------------------
# hitl_retrospective_review
# ---------------------------------------------------------------------------


def test_hitl_retrospective_synthesizes_approve() -> None:
    state = CveRemState(retro_id="r-1")
    out = asyncio.run(HitlRetrospectiveReviewNode().execute(state, _ctx()))
    assert out["response"].decision == "approve"
    assert "retrospective" in out["hitl_gates"]
    assert out["cmdb_match_correct"] is True
