# SPDX-License-Identifier: Apache-2.0
"""Krakntrust attestation primitives for the CVE remediation demo.

Provides a stable Ed25519 signing identity (``krakntrust-cve-rem-<fp>``)
with public key committed to ``dev-keys/krakntrust-cve-rem.pub.pem`` and
private key generated on first use under
``dev-keys/krakntrust-cve-rem.priv.pem`` (gitignored).

This is the "krakntrust boot session + root key" surrogate referenced in
CRITERIA fancy #1. Real production would have a Go boot binary verifying
Ed25519 sigs over validator config plus a Shamir 2-of-3 ceremony for the
root key. The demo runs in **single-key dev mode**: one stable Ed25519
identity on disk, fingerprinted, with provenance back to the
``dev-keys/`` directory's mtime as the surrogate "boot session
timestamp". This is documented in the verifier output so an auditor sees
which links are real vs. dev-only.

Functions:

* :func:`load_or_create_keypair` — return ``(priv_pem, pub_pem, key_id,
  boot_session_id)``; generates the keypair on first call.
* :func:`sign_attestation` — sign a canonical JSON payload, return a
  compact EdDSA JWS.
* :func:`verify_attestation` — verify a compact EdDSA JWS against the
  pinned pubkey on disk; returns the decoded payload or raises.
* :func:`compute_prompt_artifact_id` — BLAKE3 over (plan_rationale +
  RAG citations + agent trace) so the run's "prompt artifact" is
  content-addressable and reproducible.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

try:
    import blake3 as _blake3_mod  # type: ignore[import-not-found]

    def _blake3_hex(b: bytes) -> str:
        return _blake3_mod.blake3(b).hexdigest()
except ImportError:  # pragma: no cover — fallback
    def _blake3_hex(b: bytes) -> str:
        return hashlib.sha256(b).hexdigest()


_DEMO_DIR = Path(__file__).resolve().parent
_DEV_KEYS_DIR = _DEMO_DIR / "dev-keys"
_PRIV_KEY_PATH = _DEV_KEYS_DIR / "krakntrust-cve-rem.priv.pem"
_PUB_KEY_PATH = _DEV_KEYS_DIR / "krakntrust-cve-rem.pub.pem"


@dataclass(frozen=True)
class KrakntrustIdentity:
    """Stable signing identity loaded from ``dev-keys/``.

    ``boot_session_id`` is the BLAKE3 of the public-key PEM bytes.
    Acts as the root-of-trust anchor — anyone with the pubkey can
    derive it deterministically; rotating the keypair changes the id.
    """

    priv_pem: bytes
    pub_pem: bytes
    key_id: str
    boot_session_id: str
    pub_key_path: Path
    priv_key_path: Path


def _key_id(pub_pem: bytes) -> str:
    pub = load_pem_public_key(pub_pem)
    der = pub.public_bytes(
        Encoding.DER, PublicFormat.SubjectPublicKeyInfo
    )
    fp = hashlib.sha256(der).hexdigest()[:8]
    return f"krakntrust-cve-rem-{fp}"


def load_or_create_keypair() -> KrakntrustIdentity:
    """Return the demo's stable krakntrust identity, generating on first use.

    Idempotent across runs once the privkey lands on disk. Both PEM
    files live under ``demos/cve_remediation/dev-keys/`` and the
    privkey is gitignored.
    """
    _DEV_KEYS_DIR.mkdir(parents=True, exist_ok=True)

    if _PRIV_KEY_PATH.exists() and _PUB_KEY_PATH.exists():
        priv_pem = _PRIV_KEY_PATH.read_bytes()
        pub_pem = _PUB_KEY_PATH.read_bytes()
        # Guard: priv ↔ pub must match (catches a stale committed
        # pubkey when privkey was regenerated locally without
        # rewriting the pubkey).
        priv = load_pem_private_key(priv_pem, password=None)
        if not isinstance(priv, Ed25519PrivateKey):
            raise RuntimeError(
                f"krakntrust priv key is not Ed25519: {_PRIV_KEY_PATH}"
            )
        derived_pub = priv.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        )
        if derived_pub.strip() != pub_pem.strip():
            raise RuntimeError(
                f"krakntrust priv/pub mismatch: regenerate by deleting "
                f"both {_PRIV_KEY_PATH} and {_PUB_KEY_PATH}"
            )
    else:
        # Generate fresh keypair. Persist priv (gitignored) + pub.
        priv = Ed25519PrivateKey.generate()
        priv_pem = priv.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        )
        pub_pem = priv.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        )
        _PRIV_KEY_PATH.write_bytes(priv_pem)
        _PRIV_KEY_PATH.chmod(0o600)
        _PUB_KEY_PATH.write_bytes(pub_pem)

    key_id = _key_id(pub_pem)
    boot_session_id = _blake3_hex(pub_pem)
    return KrakntrustIdentity(
        priv_pem=priv_pem,
        pub_pem=pub_pem,
        key_id=key_id,
        boot_session_id=boot_session_id,
        pub_key_path=_PUB_KEY_PATH,
        priv_key_path=_PRIV_KEY_PATH,
    )


def sign_attestation(
    payload: dict[str, Any], identity: KrakntrustIdentity
) -> str:
    """Sign ``payload`` with the krakntrust identity, return compact EdDSA JWS.

    The payload is encoded as JWT with ``alg=EdDSA`` and ``kid=key_id``.
    Use :func:`verify_attestation` to round-trip.
    """
    return jwt.encode(
        payload,
        identity.priv_pem,
        algorithm="EdDSA",
        headers={"kid": identity.key_id},
    )


def verify_attestation(
    token: str, pub_key_path: Path | str | None = None
) -> dict[str, Any]:
    """Verify a compact EdDSA JWS against the on-disk krakntrust pubkey.

    Raises :class:`jwt.exceptions.InvalidSignatureError` (or related
    PyJWT exception) when the signature does not verify.
    """
    path = Path(pub_key_path) if pub_key_path else _PUB_KEY_PATH
    pub_pem = path.read_bytes()
    pub = load_pem_public_key(pub_pem)
    if not isinstance(pub, Ed25519PublicKey):
        raise RuntimeError(f"pinned pubkey is not Ed25519: {path}")
    decoded = jwt.decode(
        token,
        pub_pem,
        algorithms=["EdDSA"],
        options={"verify_aud": False},
    )
    return dict(decoded)


def compute_prompt_artifact_id(
    *,
    plan_rationale: str,
    rag_sources: list[dict[str, Any]] | None,
    agent_trace: list[dict[str, Any]] | None,
) -> str:
    """Content-address the LM "prompt artifact" for a run.

    Stable across re-runs of the same plan: the rationale text, the
    list of injected RAG source URLs, and the agent's tool-call trace
    are hashed in canonical JSON form. Empty trace / sources still
    yield a stable id.
    """
    payload = {
        "plan_rationale": str(plan_rationale or ""),
        "rag_sources": [
            {
                "index": str(s.get("index", "")),
                "url": str(s.get("url", "")),
            }
            for s in (rag_sources or [])
        ],
        "agent_trace": [
            {
                "role": str(s.get("role", "")),
                "content_sha256": hashlib.sha256(
                    str(s.get("content", "")).encode("utf-8")
                ).hexdigest()[:16],
            }
            for s in (agent_trace or [])
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _blake3_hex(canonical)


def boot_session_metadata() -> dict[str, str]:
    """Return the boot-session anchor descriptor for the CLI walker.

    Surfaces: pubkey path, mtime as the surrogate boot timestamp,
    and a sha256 of the on-disk pubkey bytes. The verifier prints
    this so the auditor can see which on-disk identity is being
    treated as root-of-trust for the current verification.
    """
    if not _PUB_KEY_PATH.exists():
        return {}
    stat = _PUB_KEY_PATH.stat()
    return {
        "pub_key_path": str(_PUB_KEY_PATH),
        "boot_session_mtime": (
            os.environ.get("HARBOR_FAKE_NOW", "")
            or str(stat.st_mtime)
        ),
        "pub_key_sha256": hashlib.sha256(
            _PUB_KEY_PATH.read_bytes()
        ).hexdigest(),
    }
