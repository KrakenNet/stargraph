# SPDX-License-Identifier: Apache-2.0
"""Tests for Phase 0 doctrine-ingest real node bodies (S3.0)."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from demos.cve_remediation.graph.real_nodes import (
    BootgateAllowlistUpdateNode,
    CanonicalizeDoctrineNode,
    DoctrineExtractorNode,
    DoctrineLoaderNode,
    IdempotencyCheckNode,
    KgLoaderNode,
)
from demos.cve_remediation.graph.state import CveRemState


@pytest.fixture
def isolated_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "doctrine_allowlist.json"
    monkeypatch.setenv("CVE_REM_DOCTRINE_ALLOWLIST", str(target))
    monkeypatch.setenv("HARBOR_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    return target


def _ctx() -> object:
    """Minimal ExecutionContext stand-in — Phase 0 nodes ignore ctx."""
    return object()


def test_idempotency_check_fresh_returns_false(isolated_allowlist: Path) -> None:
    state = CveRemState(corpus_sha256="abc123")
    node = IdempotencyCheckNode()
    out = asyncio.run(node.execute(state, _ctx()))
    assert out == {"corpus_already_allowlisted": False}


def test_idempotency_check_present_returns_true(isolated_allowlist: Path) -> None:
    isolated_allowlist.parent.mkdir(parents=True, exist_ok=True)
    isolated_allowlist.write_text(
        json.dumps({"abc123": "manifest-hash-1"}), encoding="utf-8"
    )
    state = CveRemState(corpus_sha256="abc123")
    node = IdempotencyCheckNode()
    out = asyncio.run(node.execute(state, _ctx()))
    assert out == {"corpus_already_allowlisted": True}


def test_idempotency_check_empty_corpus(isolated_allowlist: Path) -> None:
    state = CveRemState(corpus_sha256="")
    out = asyncio.run(IdempotencyCheckNode().execute(state, _ctx()))
    assert out == {"corpus_already_allowlisted": False}


def test_doctrine_loader_emits_corpus_sha(isolated_allowlist: Path) -> None:
    state = CveRemState()
    out = asyncio.run(DoctrineLoaderNode().execute(state, _ctx()))
    assert "corpus_sha256" in out
    assert len(out["corpus_sha256"]) == 64  # sha256 hex
    pin = out["corpus_version_pin"]
    assert pin.startswith("upstream-real:")
    assert len(pin.split(":", 1)[1]) == 12
    env = out["broker_request_envelope"]
    assert env["doctrine_file_count"] >= 3
    assert "doctrine_bundle_bytes" in env


def test_doctrine_loader_deterministic(isolated_allowlist: Path) -> None:
    state = CveRemState()
    a = asyncio.run(DoctrineLoaderNode().execute(state, _ctx()))
    b = asyncio.run(DoctrineLoaderNode().execute(state, _ctx()))
    assert a["corpus_sha256"] == b["corpus_sha256"]


def test_doctrine_loader_upstream_failure_raises(
    monkeypatch: pytest.MonkeyPatch, isolated_allowlist: Path
) -> None:
    """Loader is fail-loud: an upstream-build error surfaces, not silently emits empty doctrine."""
    from demos.cve_remediation.graph import real_nodes as rn_mod

    async def _boom() -> None:
        raise RuntimeError("upstream-unreachable")

    monkeypatch.setattr(
        "demos.cve_remediation.tools.doctrine_corpus.build_doctrine_kg", _boom
    )
    # Re-import binding inside DoctrineLoaderNode is local to execute(), so
    # the monkeypatch on the module-level symbol is what gets picked up.
    del rn_mod  # silence unused
    with pytest.raises(RuntimeError, match="upstream-unreachable"):
        asyncio.run(DoctrineLoaderNode().execute(CveRemState(), _ctx()))


def test_canonicalize_doctrine_splits_sections(
    isolated_allowlist: Path,
) -> None:
    loaded = asyncio.run(DoctrineLoaderNode().execute(CveRemState(), _ctx()))
    state = CveRemState(broker_request_envelope=loaded["broker_request_envelope"])
    out = asyncio.run(CanonicalizeDoctrineNode().execute(state, _ctx()))
    sections = out["broker_request_envelope"]["doctrine_sections"]
    assert len(sections) >= 1
    assert all("title" in s and "body" in s for s in sections)


def test_doctrine_extractor_projects_upstream_corpus(
    isolated_allowlist: Path,
) -> None:
    """Extractor projects the structured upstream corpus into legacy
    ``label:id`` strings — NIST controls, MITRE ATT&CK, CAPEC, CWE.
    The upstream corpora are doctrine-only (no individual CVE entries),
    so we assert against the authoritative classes that DO appear.
    """
    loaded = asyncio.run(DoctrineLoaderNode().execute(CveRemState(), _ctx()))
    state = CveRemState(broker_request_envelope=loaded["broker_request_envelope"])
    canon = asyncio.run(CanonicalizeDoctrineNode().execute(state, _ctx()))
    state = CveRemState(broker_request_envelope=canon["broker_request_envelope"])
    out = asyncio.run(DoctrineExtractorNode().execute(state, _ctx()))
    assert out["doctrine_node_count"] > 1000  # ~3.4k upstream entities
    assert out["doctrine_edge_count"] > 1000
    nodes = out["broker_request_envelope"]["doctrine_kg_nodes"]
    edges = out["broker_request_envelope"]["doctrine_kg_edges"]
    # CWE-502 (Deserialization of Untrusted Data) is in MITRE CWE.
    assert any(n == "cwe:CWE-502" for n in nodes)
    # NIST 800-53 AC-1 (Policy and Procedures) is always present.
    assert any(n == "control:AC-1" for n in nodes)
    # Authoritative Control -> CWE materialized edges exist.
    assert any(
        e.startswith("control:") and "->cwe:" in e for e in edges
    )


def test_kg_loader_writes_json(isolated_allowlist: Path) -> None:
    state = CveRemState(
        broker_request_envelope={
            "doctrine_kg_nodes": ["cve:CVE-X", "cwe:CWE-1"],
            "doctrine_kg_edges": ["cve:CVE-X->cwe:CWE-1"],
        }
    )
    asyncio.run(KgLoaderNode().execute(state, _ctx()))
    target = isolated_allowlist.parent / "doctrine_kg.json"
    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["nodes"] == ["cve:CVE-X", "cwe:CWE-1"]
    assert payload["edges"] == ["cve:CVE-X->cwe:CWE-1"]


def test_bootgate_allowlist_update_appends(isolated_allowlist: Path) -> None:
    state = CveRemState(
        corpus_sha256="sha-A",
        doctrine_manifest_hash="manifest-A",
    )
    asyncio.run(BootgateAllowlistUpdateNode().execute(state, _ctx()))
    payload = json.loads(isolated_allowlist.read_text(encoding="utf-8"))
    assert payload == {"sha-A": "manifest-A"}


def test_bootgate_allowlist_update_idempotent(
    isolated_allowlist: Path,
) -> None:
    state = CveRemState(
        corpus_sha256="sha-A", doctrine_manifest_hash="manifest-A"
    )
    asyncio.run(BootgateAllowlistUpdateNode().execute(state, _ctx()))
    asyncio.run(BootgateAllowlistUpdateNode().execute(state, _ctx()))
    payload = json.loads(isolated_allowlist.read_text(encoding="utf-8"))
    assert payload == {"sha-A": "manifest-A"}


def test_bootgate_allowlist_update_skips_when_missing_inputs(
    isolated_allowlist: Path,
) -> None:
    state = CveRemState(corpus_sha256="", doctrine_manifest_hash="")
    asyncio.run(BootgateAllowlistUpdateNode().execute(state, _ctx()))
    assert not isolated_allowlist.is_file()


def test_phase0_pipeline_chain(isolated_allowlist: Path) -> None:
    """End-to-end: D0→D1→D2→D3→D4 + D6 update; verify counts and allowlist."""
    state = CveRemState()
    # D0 (fresh)
    state = state.model_copy(
        update=asyncio.run(IdempotencyCheckNode().execute(state, _ctx()))
    )
    assert state.corpus_already_allowlisted is False
    # D1
    state = state.model_copy(
        update=asyncio.run(DoctrineLoaderNode().execute(state, _ctx()))
    )
    assert state.corpus_sha256
    # D2
    state = state.model_copy(
        update=asyncio.run(CanonicalizeDoctrineNode().execute(state, _ctx()))
    )
    # D3
    state = state.model_copy(
        update=asyncio.run(DoctrineExtractorNode().execute(state, _ctx()))
    )
    assert state.doctrine_node_count > 0
    # D4
    asyncio.run(KgLoaderNode().execute(state, _ctx()))
    # D6 — needs manifest_hash, simulate
    state = state.model_copy(update={"doctrine_manifest_hash": "test-hash"})
    asyncio.run(BootgateAllowlistUpdateNode().execute(state, _ctx()))
    # Verify D0 now sees it
    state2 = CveRemState(corpus_sha256=state.corpus_sha256)
    out = asyncio.run(IdempotencyCheckNode().execute(state2, _ctx()))
    assert out["corpus_already_allowlisted"] is True
