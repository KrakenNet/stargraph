# SPDX-License-Identifier: Apache-2.0
"""TOFU + static-allow-list pubkey distribution tests (Task 2.27).

Trust On First Use (TOFU) is the OSS-default mode: first sight of a
``key_id`` reads the sidecar ``<pack_dir>/<key_id>.pub.pem``, computes a
fingerprint over the DER-encoded public key bytes, and records it under
``<config>/trusted_keys/<key_id>.json``. Subsequent loads compare the
sidecar fingerprint against the stored record; mismatch is a security
boundary that fails under BOTH profiles (TOFU is not a profile knob).

Cleared profile MUST be backed by a :class:`StaticTrustStore`; first
sight of an unknown ``key_id`` is a load-fail (no TOFU). The OSS-default
profile uses the :class:`FilesystemTrustStore` so first sight pins.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from stargraph.bosun.signing import (
    FilesystemTrustStore,
    PackSignatureError,
    StaticTrustStore,
    sign_pack,
    verify_pack,
)
from stargraph.serve.profiles import ClearedProfile, OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path


def _keypair() -> tuple[bytes, bytes, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(pub_der).hexdigest()[:16]
    return priv_pem, pub_pem, key_id


def _write_pack_with_sidecar(
    pack: Path, files: dict[str, bytes], key_id: str, pub_pem: bytes
) -> None:
    for rel, content in files.items():
        path = pack / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    (pack / f"{key_id}.pub.pem").write_bytes(pub_pem)


@pytest.mark.unit
def test_tofu_first_sight_records_fingerprint(tmp_path: Path) -> None:
    """First verify under oss-default + FilesystemTrustStore records fingerprint to disk."""
    pack = tmp_path / "pack"
    pack.mkdir()
    config = tmp_path / "config"

    priv_pem, pub_pem, key_id = _keypair()
    _write_pack_with_sidecar(pack, {"manifest.yaml": b"x"}, key_id, pub_pem)
    token = sign_pack(pack, priv_pem, key_id)

    trust = FilesystemTrustStore(config)
    result = verify_pack(pack, token, trust, OssDefaultProfile())
    assert result.verified is True

    record_path = config / "trusted_keys" / f"{key_id}.json"
    assert record_path.exists()
    record = json.loads(record_path.read_text())
    assert record["key_id"] == key_id
    assert "fingerprint" in record
    assert "first_seen" in record


@pytest.mark.unit
def test_tofu_second_load_passes(tmp_path: Path) -> None:
    """After first sight, second verify against same key_id passes silently."""
    pack = tmp_path / "pack"
    pack.mkdir()
    config = tmp_path / "config"

    priv_pem, pub_pem, key_id = _keypair()
    _write_pack_with_sidecar(pack, {"manifest.yaml": b"x"}, key_id, pub_pem)
    token = sign_pack(pack, priv_pem, key_id)

    trust = FilesystemTrustStore(config)
    verify_pack(pack, token, trust, OssDefaultProfile())  # first sight, records
    result2 = verify_pack(pack, token, trust, OssDefaultProfile())
    assert result2.verified is True


@pytest.mark.unit
def test_tofu_fingerprint_mismatch_fails_oss(tmp_path: Path) -> None:
    """If sidecar pub.pem changes for the same key_id, oss-default load fails."""
    pack = tmp_path / "pack"
    pack.mkdir()
    config = tmp_path / "config"

    priv_pem, pub_pem, key_id = _keypair()
    _write_pack_with_sidecar(pack, {"manifest.yaml": b"x"}, key_id, pub_pem)
    token = sign_pack(pack, priv_pem, key_id)

    trust = FilesystemTrustStore(config)
    verify_pack(pack, token, trust, OssDefaultProfile())  # records original fp

    # Generate a NEW keypair, sign new token, but reuse the SAME key_id.
    priv2_pem, pub2_pem, _ = _keypair()
    (pack / f"{key_id}.pub.pem").write_bytes(pub2_pem)  # tamper sidecar
    token2 = sign_pack(pack, priv2_pem, key_id)

    with pytest.raises(PackSignatureError):
        verify_pack(pack, token2, trust, OssDefaultProfile())


@pytest.mark.unit
def test_tofu_fingerprint_mismatch_fails_cleared(tmp_path: Path) -> None:
    """Mismatch under cleared profile + filesystem store fails (TOFU is a security boundary)."""
    pack = tmp_path / "pack"
    pack.mkdir()
    config = tmp_path / "config"

    priv_pem, pub_pem, key_id = _keypair()
    _write_pack_with_sidecar(pack, {"manifest.yaml": b"x"}, key_id, pub_pem)
    token = sign_pack(pack, priv_pem, key_id)

    # Pre-record a different fingerprint so the cleared verify hits a mismatch.
    fake_fp = "0" * 64
    (config / "trusted_keys").mkdir(parents=True, exist_ok=True)
    (config / "trusted_keys" / f"{key_id}.json").write_text(
        json.dumps({"key_id": key_id, "fingerprint": fake_fp, "first_seen": "2026-01-01T00:00:00Z"})
    )

    trust = FilesystemTrustStore(config)
    with pytest.raises(PackSignatureError):
        verify_pack(pack, token, trust, ClearedProfile())


@pytest.mark.unit
def test_static_trust_store_unlisted_cleared_fails(tmp_path: Path) -> None:
    """StaticTrustStore: unlisted key_id under cleared profile must load-fail."""
    pack = tmp_path / "pack"
    pack.mkdir()

    priv_pem, _pub_pem, key_id = _keypair()
    (pack / "manifest.yaml").write_bytes(b"x")
    token = sign_pack(pack, priv_pem, key_id)

    other = StaticTrustStore({"unrelated_key": b"-----BEGIN PUBLIC KEY-----\n..."})
    with pytest.raises(PackSignatureError):
        verify_pack(pack, token, other, ClearedProfile())


@pytest.mark.unit
def test_static_trust_store_unlisted_oss_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """StaticTrustStore: unlisted key_id under oss-default warns, returns verified=False."""
    import logging

    pack = tmp_path / "pack"
    pack.mkdir()

    priv_pem, pub_pem, key_id = _keypair()
    _write_pack_with_sidecar(pack, {"manifest.yaml": b"x"}, key_id, pub_pem)
    token = sign_pack(pack, priv_pem, key_id)

    other = StaticTrustStore({"unrelated_key": b"-----BEGIN PUBLIC KEY-----\n..."})
    with caplog.at_level(logging.WARNING, logger="stargraph.bosun.signing"):
        result = verify_pack(pack, token, other, OssDefaultProfile())

    assert result.verified is False


@pytest.mark.unit
def test_jwt_with_x5c_header_rejected(tmp_path: Path) -> None:
    """JWT carrying ``x5c`` header is rejected at decode (untrusted certificate-in-JWT)."""
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "manifest.yaml").write_bytes(b"x")

    priv_pem, pub_pem, key_id = _keypair()

    # Sign normally then forge a header with x5c.
    bad = jwt.encode(
        {"key_id": key_id, "tree_hash": "deadbeef", "alg": "BLAKE3"},
        key=priv_pem,
        algorithm="EdDSA",
        headers={"kid": key_id, "x5c": ["MIIBkTCB-fake-cert-base64=="]},
    )

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError):
        verify_pack(pack, bad, trust, ClearedProfile())


@pytest.mark.unit
def test_cleared_profile_first_sight_static_unlisted_fails(tmp_path: Path) -> None:
    """Cleared profile first-sight on a key_id not in static allow-list must load-fail."""
    pack = tmp_path / "pack"
    pack.mkdir()

    priv_pem, pub_pem, key_id = _keypair()
    _write_pack_with_sidecar(pack, {"manifest.yaml": b"x"}, key_id, pub_pem)
    token = sign_pack(pack, priv_pem, key_id)

    # Static trust store empty: cleared profile must refuse.
    empty = StaticTrustStore({})
    with pytest.raises(PackSignatureError):
        verify_pack(pack, token, empty, ClearedProfile())
