# SPDX-License-Identifier: Apache-2.0
"""Unit tests for `harbor.bosun.signing` (Task 3.3).

Scope per task 3.3 spec: ≥4 cases focused on what is **not** already
covered by ``test_signing_roundtrip.py`` (sign/verify roundtrip, tamper
under both profiles, ``alg=none`` rejection, ``alg=HS256`` rejection)
and ``test_tofu.py`` (TOFU first-sight, fingerprint mismatch, x5c
header rejection, static-trust-store unlisted-key fail).

The remaining unsealed corners — addressed here:

1. **SHA-256 fallback under FIPS** — when ``HARBOR_FIPS_MODE=1`` is
   set, the signed JWT's payload ``alg`` field is ``SHA-256`` not
   ``BLAKE3``. The tree-hash uses :func:`hashlib.sha256` instead of
   :class:`blake3.blake3`. Round-trips inside a single FIPS context.
2. **FIPS sign + non-FIPS verify is mutually broken** — signing the
   tree under FIPS and verifying outside (different hash algorithm
   means different tree-hash) must surface as ``tree-hash-mismatch``,
   not silently pass. (Sanity check that the algo choice is honest.)
3. **alg=none rejected at decode (focused re-assertion)** — the spec
   asks for ≥4 cases including this scenario; we re-assert it as a
   focused canary so the security boundary is canon-tested in this
   file too. Overlaps with ``test_signing_roundtrip::test_alg_none_rejected``
   by design (documented in the task's note about overlap).
4. **x5c header rejected (focused re-assertion)** — same reasoning as
   #3 vs ``test_tofu::test_jwt_with_x5c_header_rejected``.

Requirements: FR-41, FR-42, AC-3.3, AC-3.4. Design: §16.9, §17 #4.
"""

from __future__ import annotations

import hashlib
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
from harbor.serve.profiles import ClearedProfile

if TYPE_CHECKING:
    from pathlib import Path


def _write_pack(root: Path, files: dict[str, bytes]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _keypair() -> tuple[bytes, bytes, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    key_id = hashlib.sha256(pub_der).hexdigest()[:16]
    return priv_pem, pub_pem, key_id


@pytest.mark.unit
def test_sign_payload_alg_field_is_sha256_under_fips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Under ``HARBOR_FIPS_MODE=1`` the JWT payload ``alg`` field is SHA-256.

    The JWT header ``alg`` is always ``EdDSA`` (signing algorithm); the
    *payload* ``alg`` records the tree-hash algorithm choice. FIPS mode
    swaps the BLAKE3 default for SHA-256 so the signing pipeline runs
    on a FIPS-validated primitive. We assert by decoding the unverified
    payload — verify_pack would also pass, but reading the payload
    directly is the most precise assertion of the algo recorded.
    """
    monkeypatch.setenv("HARBOR_FIPS_MODE", "1")

    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n"})

    priv_pem, _pub_pem, key_id = _keypair()
    token = sign_pack(pack, priv_pem, key_id)

    # Header alg is the JWT signing algorithm (EdDSA, fixed).
    assert jwt.get_unverified_header(token)["alg"] == "EdDSA"

    payload = jwt.decode(token, options={"verify_signature": False})
    assert payload["alg"] == "SHA-256"


@pytest.mark.unit
def test_fips_mode_roundtrip_verify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Sign + verify a pack under FIPS mode: same tree-hash algo end-to-end."""
    monkeypatch.setenv("HARBOR_FIPS_MODE", "1")

    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(
        pack,
        {"manifest.yaml": b"id: x\nversion: 1.0\n", "rules/a.clp": b"; rule\n"},
    )

    priv_pem, pub_pem, key_id = _keypair()
    token = sign_pack(pack, priv_pem, key_id)

    trust = StaticTrustStore({key_id: pub_pem})
    result = verify_pack(pack, token, trust, ClearedProfile())
    assert result.verified is True


@pytest.mark.unit
def test_alg_none_rejected_focused(tmp_path: Path) -> None:
    """``alg=none`` JWT rejected at decode (focused re-assertion of the security boundary).

    Overlaps with ``tests/unit/bosun/test_signing_roundtrip.py::
    test_alg_none_rejected``. Restated here so the local file's
    coverage of the locked-decision security boundary is self-contained
    — the spec calls out this overlap as acceptable when the file is
    standing in as a canary for the security invariant.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"x"})

    _priv_pem, pub_pem, key_id = _keypair()
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


@pytest.mark.unit
def test_x5c_header_rejected_focused(tmp_path: Path) -> None:
    """JWT with ``x5c`` header rejected — locked Decision #4 (focused re-assertion).

    Overlaps with ``tests/unit/bosun/test_tofu.py::test_jwt_with_x5c_header_rejected``.
    Repeated here so the canon-tested security boundary is self-contained
    in this file. Surfaces the ``x5c-rejected`` reason explicitly.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"x"})

    priv_pem, pub_pem, key_id = _keypair()
    # Sign normally then forge a header with x5c included.
    token = sign_pack(pack, priv_pem, key_id)
    parts = token.split(".")
    import base64
    import json as _json

    header = _json.loads(base64.urlsafe_b64decode(parts[0] + "==").decode("ascii"))
    header["x5c"] = ["MIIBkTCB-fake-cert-base64=="]
    new_header = (
        base64.urlsafe_b64encode(_json.dumps(header).encode("ascii")).rstrip(b"=").decode("ascii")
    )
    forged = ".".join([new_header, parts[1], parts[2]])

    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError) as excinfo:
        verify_pack(pack, forged, trust, ClearedProfile())
    assert excinfo.value.context["reason"] == "x5c-rejected"


@pytest.mark.unit
def test_fips_signed_pack_fails_verify_in_blake3_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sign under FIPS (SHA-256 tree hash), verify under default (BLAKE3) → tree-hash-mismatch.

    Sanity check: the algo choice in the payload is honest. Verifying
    the FIPS-signed JWT in non-FIPS mode would re-hash the tree with
    BLAKE3 and compare against the SHA-256 hex stored in the payload
    — they must differ, surfacing as ``tree-hash-mismatch`` under
    cleared profile (raises) per :func:`_refuse_or_warn`.
    """
    pack = tmp_path / "pack"
    pack.mkdir()
    _write_pack(pack, {"manifest.yaml": b"id: x\nversion: 1.0\n"})

    priv_pem, pub_pem, key_id = _keypair()

    # Sign under FIPS.
    monkeypatch.setenv("HARBOR_FIPS_MODE", "1")
    token = sign_pack(pack, priv_pem, key_id)

    # Verify with FIPS off — different tree-hash algorithm.
    monkeypatch.delenv("HARBOR_FIPS_MODE", raising=False)
    trust = StaticTrustStore({key_id: pub_pem})
    with pytest.raises(PackSignatureError) as excinfo:
        verify_pack(pack, token, trust, ClearedProfile())
    assert excinfo.value.context["reason"] == "tree-hash-mismatch"
