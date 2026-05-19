# SPDX-License-Identifier: Apache-2.0
"""E2 contract tests for cve_rem.* pack JWT signing.

Each pack must:

  - Carry a ``manifest.jwt`` produced by ``demos.cve_remediation.sign_packs``.
  - Have a ``<key_id>.pub.pem`` TOFU sidecar matching the JWT ``kid``.
  - Verify cleanly via ``harbor.bosun.signing.verify_pack`` under
    OSS-default profile (TOFU pin of the krakntrust-cve-rem dev key).
  - Reject any tamper to the rule body (tree-hash mismatch).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import jwt
import pytest

from harbor.bosun.signing import (
    FilesystemTrustStore,
    verify_pack,
)
from harbor.serve.profiles import OssDefaultProfile

GRAPH_DIR = Path(__file__).resolve().parent.parent
RULES_DIR = GRAPH_DIR / "rules"
DEV_KEYS = GRAPH_DIR.parent / "dev-keys"

PACK_NAMES = (
    "cve_rem.routing",
    "cve_rem.kill_switches",
    "cve_rem.doctrine_trust",
    "cve_rem.offline_isolation",
    "cve_rem.gepa_score_policy",
)


@pytest.fixture(autouse=True)
def _ensure_signed() -> None:
    """Run sign_packs.py if any pack lacks a manifest.jwt — keeps tests
    self-bootstrapping without committing freshly-rotated keys per CI run.
    """
    missing = [
        name
        for name in PACK_NAMES
        if not (RULES_DIR / name / "manifest.jwt").is_file()
    ]
    if missing:
        from demos.cve_remediation.sign_packs import main as sign_main

        sign_main()


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_pack_has_manifest_jwt(pack_name: str) -> None:
    pack_dir = RULES_DIR / pack_name
    jwt_path = pack_dir / "manifest.jwt"
    assert jwt_path.is_file(), f"{pack_name} missing manifest.jwt"
    token = jwt_path.read_text(encoding="utf-8")
    # Compact JWT shape: 3 dot-separated base64 segments.
    assert token.count(".") == 2


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_pack_has_pubkey_sidecar(pack_name: str) -> None:
    pack_dir = RULES_DIR / pack_name
    sidecars = list(pack_dir.glob("krakntrust-cve-rem-*.pub.pem"))
    assert len(sidecars) == 1, (
        f"{pack_name} expected 1 pubkey sidecar, found {len(sidecars)}"
    )


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_jwt_kid_matches_sidecar(pack_name: str) -> None:
    pack_dir = RULES_DIR / pack_name
    token = (pack_dir / "manifest.jwt").read_text(encoding="utf-8")
    header = jwt.get_unverified_header(token)
    kid = header["kid"]
    assert (pack_dir / f"{kid}.pub.pem").is_file(), (
        f"{pack_name} JWT kid={kid} but no matching sidecar"
    )


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_jwt_alg_is_eddsa(pack_name: str) -> None:
    """Strict alg whitelist: EdDSA only — never HS*, never none."""
    token = (RULES_DIR / pack_name / "manifest.jwt").read_text()
    header = jwt.get_unverified_header(token)
    assert header.get("alg") == "EdDSA"
    # The PAYLOAD's alg field is the tree-hash algo, not JWT signing
    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["alg"] in ("BLAKE3", "SHA-256")


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_pack_verifies_under_oss_default(pack_name: str, tmp_path: Path) -> None:
    """Full verify_pack round-trip against an isolated TOFU trust store."""
    pack_dir = RULES_DIR / pack_name
    token = (pack_dir / "manifest.jwt").read_text(encoding="utf-8")
    trust_store = FilesystemTrustStore(tmp_path)
    profile = OssDefaultProfile()
    result = verify_pack(pack_dir, token, trust_store, profile)
    assert result.verified, f"{pack_name} did not verify: {result}"


@pytest.mark.parametrize("pack_name", PACK_NAMES)
def test_pack_tamper_detected(pack_name: str, tmp_path: Path) -> None:
    """Tampering with rules.clp / pack.yaml must fail verification."""
    src = RULES_DIR / pack_name
    work = tmp_path / pack_name
    shutil.copytree(src, work)

    # Tamper a rule file — append a comment that changes the tree hash.
    target = work / "rules.clp"
    if not target.is_file():
        target = work / "pack.yaml"
    target.write_text(target.read_text() + "\n; tampered\n", encoding="utf-8")

    token = (work / "manifest.jwt").read_text(encoding="utf-8")
    trust_store = FilesystemTrustStore(tmp_path / "trusted")
    profile = OssDefaultProfile()
    result = verify_pack(work, token, trust_store, profile)
    assert not result.verified, (
        f"{pack_name}: tampering not detected — verify returned True"
    )


def test_project_pubkey_committed() -> None:
    """The project-scoped pubkey copy is committed to dev-keys/."""
    assert (DEV_KEYS / "krakntrust-cve-rem.pub.pem").is_file()
