# SPDX-License-Identifier: Apache-2.0
"""Phase-3 task 3.21: tampered-manifest verify outcomes per profile.

Locks the design §17 Decision #4 profile-conditional failure surface:

* **Cleared profile** (``signature_verify_mandatory=True``) — any
  tamper raises :class:`PackSignatureError` (load-fail).
* **OSS-default profile** (``signature_verify_mandatory=False``) — the
  same tamper logs a WARNING and returns
  :class:`VerifyResult(verified=False)` (warn, don't fail).

Cases covered (FR-41, FR-42, FR-65, AC-3.3, AC-8.5):

1. Tree byte-flip under cleared → ``PackSignatureError`` with
   ``reason="tree-hash-mismatch"``.
2. Tree byte-flip under oss-default → WARNING + ``verified=False``;
   no raise.
3. JWT-header byte-flip under cleared → ``PackSignatureError``
   (signature/header parse failure surfaces as a load-fail).

Tree-hash skips ``manifest.jwt`` and ``*.pub.pem`` per
``stargraph.bosun.signing._tree_hash``, so we mutate ``manifest.yaml``
(included in the tree-hash) for cases 1+2 and the JWT itself for
case 3.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
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
from stargraph.serve.profiles import ClearedProfile, OssDefaultProfile

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = [pytest.mark.serve, pytest.mark.integration]


def _write_pack(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _eddsa_keypair() -> tuple[bytes, bytes, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(pub_der).hexdigest()[:16]
    return priv_pem, pub_pem, key_id


def _sign_a_pack(tmp_path: Path) -> tuple[Path, str, bytes, str]:
    """Build + sign a small pack and return ``(pack_dir, token, pub_pem, key_id)``."""
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(
        pack,
        {
            "manifest.yaml": b"id: tamper-test\nversion: 1.0\n",
            "rules/a.clp": b"; rule a\n",
            "rules/b.clp": b"; rule b\n",
        },
    )
    priv_pem, pub_pem, key_id = _eddsa_keypair()
    token = sign_pack(pack, priv_pem, key_id)
    return pack, token, pub_pem, key_id


@pytest.mark.serve
def test_tampered_tree_under_cleared_load_fails(tmp_path: Path) -> None:
    """One-byte tree mutation under cleared profile -> ``PackSignatureError``.

    Surfaces with ``reason="tree-hash-mismatch"`` since the JWT
    signature still verifies (the JWT is unchanged) but the recorded
    ``tree_hash`` in the payload no longer matches the live tree.
    """
    pack, token, pub_pem, key_id = _sign_a_pack(tmp_path)

    # One-byte mutation in a tree-hashed file (manifest.yaml is included).
    (pack / "manifest.yaml").write_bytes(b"id: tamper-test\nversion: 9.9\n")

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError) as excinfo:
        verify_pack(pack, token, trust, ClearedProfile())
    assert excinfo.value.context["reason"] == "tree-hash-mismatch"


@pytest.mark.serve
def test_tampered_tree_under_oss_default_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Same tamper under oss-default -> WARNING + ``verified=False`` (no raise)."""
    pack, token, pub_pem, key_id = _sign_a_pack(tmp_path)
    (pack / "manifest.yaml").write_bytes(b"id: tamper-test\nversion: 9.9\n")

    trust = StaticTrustStore({key_id: pub_pem})
    with caplog.at_level(logging.WARNING, logger="stargraph.bosun.signing"):
        result = verify_pack(pack, token, trust, OssDefaultProfile())

    assert result.verified is False
    assert result.reason == "tree-hash-mismatch"
    assert any(
        rec.name == "stargraph.bosun.signing" and rec.levelno == logging.WARNING
        for rec in caplog.records
    ), (
        "expected at least one WARNING on stargraph.bosun.signing logger; "
        f"got records={[(r.name, r.levelname) for r in caplog.records]!r}"
    )


@pytest.mark.serve
def test_tampered_jwt_header_under_cleared_load_fails(tmp_path: Path) -> None:
    """Flip a base64 char in the JWT header -> ``PackSignatureError`` under cleared.

    JWT header decoders reject malformed base64/JSON before alg
    inspection; the resulting :class:`PackSignatureError` carries
    ``reason="header-unparseable"`` (or a downstream signature-invalid
    when the byte-flip lands inside the alg/kid keys but still parses).
    Either reason proves the cleared-profile gate refused the load.
    """
    pack, token, pub_pem, key_id = _sign_a_pack(tmp_path)

    # Flip a single character early in the header segment. JWTs are
    # ``<header>.<payload>.<sig>`` base64url-encoded; mutating the very
    # first character changes the decoded JSON without losing
    # delimiter integrity.
    parts = token.split(".")
    assert len(parts) == 3, f"expected 3-part JWT; got {len(parts)} parts"
    header = parts[0]
    # Pick a flip that produces a different base64-url-safe character.
    new_char = "Z" if header[0] != "Z" else "Y"
    forged_header = new_char + header[1:]
    forged = ".".join([forged_header, parts[1], parts[2]])

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError) as excinfo:
        verify_pack(pack, forged, trust, ClearedProfile())
    # Allow either header-parse-fail or downstream alg/sig fail; both
    # are valid load-fail surfaces. Pin the set explicitly.
    assert excinfo.value.context["reason"] in {
        "header-unparseable",
        "alg-not-eddsa",
        "kid-missing",
        "signature-invalid",
    }, f"unexpected reason for forged-header rejection: {excinfo.value.context.get('reason')!r}"
