# SPDX-License-Identifier: Apache-2.0
"""Pack-signing round-trip + tamper + algorithm-confusion tests (Task 2.26).

Mirrors fathom.attestation primitives: Ed25519 keypair, EdDSA-JWT compact
form, BLAKE3 tree-hash (SHA-256 in FIPS mode). The verifier must:

* round-trip a sign/verify on an unmodified pack tree,
* refuse a single-byte tampered tree under cleared profile,
* warn (not raise) under oss-default for the same tampered tree,
* refuse JWTs that declare ``alg=none`` or ``alg=HS256`` (algorithm-
  confusion attack defense — locked design §17 Decision #4).
"""

from __future__ import annotations

import logging
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

from harbor.bosun.signing import (
    PackSignatureError,
    StaticTrustStore,
    sign_pack,
    verify_pack,
)
from harbor.serve.profiles import ClearedProfile, OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path


def _write_pack(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _keypair() -> tuple[bytes, bytes, str]:
    """Return (private_pem, public_pem, key_id)."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    # key_id is sha256(DER-pub)[:16] hex = 16 chars; matches design §7.5.
    import hashlib

    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(pub_der).hexdigest()[:16]
    return priv_pem, pub_pem, key_id


@pytest.mark.unit
def test_sign_verify_roundtrip_succeeds(tmp_path: Path) -> None:
    """Sign + verify a pack tree under cleared profile; trust store has the pubkey."""
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n", "rules/a.clp": b"; rule\n"})

    priv_pem, pub_pem, key_id = _keypair()
    token = sign_pack(pack, priv_pem, key_id)

    # Static trust store with the matching public key (cleared mode).
    trust = StaticTrustStore({key_id: pub_pem})
    result = verify_pack(pack, token, trust, ClearedProfile())
    assert result.verified is True
    assert result.key_id == key_id


@pytest.mark.unit
def test_tampered_tree_fails_under_cleared(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A single-byte mutation in the pack tree must raise under cleared profile."""
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n"})

    priv_pem, pub_pem, key_id = _keypair()
    token = sign_pack(pack, priv_pem, key_id)

    # Tamper after signing.
    (pack / "manifest.yaml").write_bytes(b"id: x\nversion: 9.9\n")

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError):
        verify_pack(pack, token, trust, ClearedProfile())


@pytest.mark.unit
def test_tampered_tree_warns_under_oss_default(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Same tamper under oss-default profile: warns + returns verified=False, no raise."""
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n"})

    priv_pem, pub_pem, key_id = _keypair()
    token = sign_pack(pack, priv_pem, key_id)
    (pack / "manifest.yaml").write_bytes(b"tampered\n")

    trust = StaticTrustStore({key_id: pub_pem})
    with caplog.at_level(logging.WARNING, logger="harbor.bosun.signing"):
        result = verify_pack(pack, token, trust, OssDefaultProfile())

    assert result.verified is False
    # Some message logged at WARNING level on the bosun.signing logger.
    assert any(
        rec.name == "harbor.bosun.signing" and rec.levelno == logging.WARNING
        for rec in caplog.records
    )


@pytest.mark.unit
def test_alg_none_rejected(tmp_path: Path) -> None:
    """A JWT with ``alg=none`` is refused at decode time (algorithm-confusion defense)."""
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"x"})

    _priv_pem, pub_pem, key_id = _keypair()

    # Hand-craft an alg=none JWT carrying the correct payload but no signature.
    bad = jwt.encode(
        {"key_id": key_id, "tree_hash": "deadbeef", "alg": "BLAKE3"},
        key="",
        algorithm="none",
        headers={"kid": key_id},
    )

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError):
        verify_pack(pack, bad, trust, ClearedProfile())


@pytest.mark.unit
def test_alg_hs256_rejected(tmp_path: Path) -> None:
    """A JWT signed with HS256 (symmetric, not EdDSA) is refused even with a valid HMAC."""
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"x"})

    _priv_pem, pub_pem, key_id = _keypair()

    # HS256 with an arbitrary symmetric secret — algorithm-confusion
    # attacks rely on the verifier accepting alg=HS256 against an
    # asymmetric public key. STRICT whitelist must reject the token at
    # the header check, before the secret even matters.
    bad = jwt.encode(
        {"key_id": key_id, "tree_hash": "deadbeef", "alg": "BLAKE3"},
        key="symmetric-secret-attacker-controlled",
        algorithm="HS256",
        headers={"kid": key_id},
    )

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError):
        verify_pack(pack, bad, trust, ClearedProfile())
