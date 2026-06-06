# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.21: pack-signing EdDSA-strict algorithm whitelist.

Re-asserts the security boundary at integration scope (the unit-level
canary lives at ``tests/unit/bosun/test_signing_roundtrip.py`` and
``tests/unit/bosun/test_signing.py``). The contract under test is the
locked design §17 Decision #4 STRICT algorithm whitelist:
:func:`stargraph.bosun.signing.verify_pack` MUST refuse any JWT whose
header ``alg`` is not ``EdDSA`` (algorithm-confusion attack defense).

Cases covered (FR-41, FR-42, AC-3.3):

1. ``alg=none`` rejected at decode -- the unauthenticated-token attack.
2. ``alg=HS256`` rejected -- the asymmetric-public-key-as-HMAC-secret
   attack (algorithm confusion proper).
3. ``alg=RS256`` rejected -- same attack class with RSA.
4. ``alg=EdDSA`` accepted (positive canary -- proves the gate is not
   universally closed).

All four assertions surface ``PackSignatureError`` with the
``reason="alg-not-eddsa"`` context tag (per spec note: errors stuff
kwargs into ``.context`` not direct attributes).
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from stargraph.bosun.signing import (
    PackSignatureError,
    StaticTrustStore,
    sign_pack,
    verify_pack,
)
from stargraph.serve.profiles import ClearedProfile

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.serve, pytest.mark.integration]


def _write_pack(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _eddsa_keypair() -> tuple[bytes, bytes, str]:
    """Return ``(private_pem, public_pem, key_id)`` for an EdDSA pair."""
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(pub_der).hexdigest()[:16]
    return priv_pem, pub_pem, key_id


@pytest.mark.serve
def test_alg_none_rejected_at_decode(tmp_path: Path) -> None:
    """``alg=none`` JWT refused under cleared profile (FR-41).

    Hand-crafts an unsigned JWT with a correct-looking payload + ``kid``
    header but ``alg=none``. The STRICT whitelist must catch this at the
    header inspection step, before signature verification even matters
    -- a verifier that accepted this would treat the attacker-controlled
    payload as authoritative.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n"})

    _priv_pem, pub_pem, key_id = _eddsa_keypair()
    bad = jwt.encode(
        {"key_id": key_id, "tree_hash": "deadbeef", "alg": "BLAKE3"},
        key="",
        algorithm="none",
        headers={"kid": key_id},
    )

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError) as excinfo:
        verify_pack(pack, bad, trust, ClearedProfile())
    assert excinfo.value.context["reason"] == "alg-not-eddsa"
    assert excinfo.value.context["actual_alg"] == "none"


@pytest.mark.serve
def test_alg_hs256_rejected(tmp_path: Path) -> None:
    """HS256-signed JWT refused under cleared profile (FR-41 algorithm confusion).

    Algorithm-confusion attacks rely on the verifier accepting
    ``alg=HS256`` against an asymmetric public key (the public PEM
    bytes get used as the HMAC secret). The STRICT whitelist refuses
    the token at the header-inspection step before the secret is ever
    consulted.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n"})

    _priv_pem, pub_pem, key_id = _eddsa_keypair()
    bad = jwt.encode(
        {"key_id": key_id, "tree_hash": "deadbeef", "alg": "BLAKE3"},
        key="attacker-controlled-symmetric-secret",
        algorithm="HS256",
        headers={"kid": key_id},
    )

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError) as excinfo:
        verify_pack(pack, bad, trust, ClearedProfile())
    assert excinfo.value.context["reason"] == "alg-not-eddsa"
    assert excinfo.value.context["actual_alg"] == "HS256"


@pytest.mark.serve
def test_alg_rs256_rejected(tmp_path: Path) -> None:
    """RS256-signed JWT refused under cleared profile (FR-41 algorithm confusion).

    Same algorithm-confusion attack class as HS256 but with RSA. A
    legitimate-looking RS256 token from an attacker's RSA key must not
    be verified against the EdDSA public key -- the STRICT whitelist
    catches this at header inspection.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n"})

    _priv_pem, pub_pem, key_id = _eddsa_keypair()

    rsa_priv = generate_private_key(public_exponent=65537, key_size=2048)
    rsa_priv_pem = rsa_priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    bad = jwt.encode(
        {"key_id": key_id, "tree_hash": "deadbeef", "alg": "BLAKE3"},
        key=rsa_priv_pem,
        algorithm="RS256",
        headers={"kid": key_id},
    )

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError) as excinfo:
        verify_pack(pack, bad, trust, ClearedProfile())
    assert excinfo.value.context["reason"] == "alg-not-eddsa"
    assert excinfo.value.context["actual_alg"] == "RS256"


@pytest.mark.serve
def test_alg_eddsa_accepted(tmp_path: Path) -> None:
    """Positive canary: ``alg=EdDSA`` JWT verifies under cleared profile.

    Proves the strict gate is not universally closed -- the canonical
    algorithm round-trips end-to-end.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(
        pack,
        {"manifest.yaml": b"id: x\nversion: 1.0\n", "rules/a.clp": b"; rule\n"},
    )

    priv_pem, pub_pem, key_id = _eddsa_keypair()
    token = sign_pack(pack, priv_pem, key_id)

    trust = StaticTrustStore({key_id: pub_pem})
    result = verify_pack(pack, token, trust, ClearedProfile())
    assert result.verified is True
    assert result.key_id == key_id
    assert result.reason is None
