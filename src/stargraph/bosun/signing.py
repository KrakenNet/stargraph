# SPDX-License-Identifier: Apache-2.0
"""Bosun pack signing + verification (FR-41, FR-42, FR-43).

Mirrors :class:`fathom.attestation.AttestationService`: Ed25519 keypair,
EdDSA-JWT compact form (PyJWT[crypto]), STRICT algorithm whitelist at
decode (``algorithms=["EdDSA"]`` — never ``none``, never ``HS*``).
Pack-tree integrity uses BLAKE3 by default, with a SHA-256 fallback
when ``STARGRAPH_FIPS_MODE=1`` is set in the environment.

The two trust-store implementations cover both deployment profiles:

* :class:`FilesystemTrustStore` — the OSS-default backing. First sight
  of a ``key_id`` (TOFU) reads the sidecar
  ``<pack_dir>/<key_id>.pub.pem``, fingerprints the DER-encoded public
  key, and writes
  ``<config_dir>/trusted_keys/<key_id>.json`` with
  ``{"key_id", "fingerprint", "first_seen"}``. Subsequent verifies
  re-read the sidecar, recompute the fingerprint, and compare. A
  mismatch is a security boundary: load-fail under BOTH profiles
  (TOFU drift is never a profile preference).
* :class:`StaticTrustStore` — the cleared-profile backing. An explicit
  ``{key_id: pub_pem_bytes}`` allow-list. First sight of a ``key_id``
  not in the dict is a load-fail; no TOFU.

Operator override pathway (FilesystemTrustStore):

  ``<config_dir>/trusted_keys/<key_id>.json``::

      {
        "key_id": "abcd1234abcd1234",
        "fingerprint": "<sha256-hex of DER-encoded public key>",
        "first_seen": "2026-04-30T12:34:56Z"
      }

  ``<config_dir>/trusted_keys/<key_id>.pub.pem`` is OPTIONAL — the
  per-pack sidecar at ``<pack_dir>/<key_id>.pub.pem`` is the canonical
  source. To revoke: delete the JSON record (the next verify pins the
  current sidecar fresh) or delete the sidecar (next verify load-fails
  for "missing public key").

Hardening (locked design §17 Decision #4):

* Embedded ``x5c`` JWT headers are refused at decode time
  (untrusted-certificate-in-JWT attack class — never trust certs
  shipped inside a token you have not yet authenticated).
* Hardcoded URL fields in the manifest payload are not honored (key
  fetch from URLs is out of scope for v1).
* ``algorithms=["EdDSA"]`` is the entire whitelist.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import jwt
from blake3 import blake3
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)

from stargraph.errors import PackSignatureError
from stargraph.serve.profiles import (
    Profile,  # noqa: TC001 -- runtime use in isinstance/Protocol args
)

__all__ = [
    "FilesystemTrustStore",
    "PackSignatureError",
    "StaticTrustStore",
    "TrustStore",
    "VerifyResult",
    "sign_pack",
    "verify_pack",
]


_LOG = logging.getLogger("stargraph.bosun.signing")


def _fips_mode() -> bool:
    """Return True if the runtime is in FIPS-compatible hash mode.

    Detected via ``STARGRAPH_FIPS_MODE=1`` in the environment. When True,
    SHA-256 replaces BLAKE3 for tree-hash + fingerprint computation.
    The choice is recorded in the JWT payload (``"alg"`` field), so
    verify can match.
    """
    return os.environ.get("STARGRAPH_FIPS_MODE") == "1"


def _hash_algo_name() -> str:
    return "SHA-256" if _fips_mode() else "BLAKE3"


def _tree_hash(tree: Path) -> str:
    """Deterministic hash over the pack tree.

    Walk every regular file under ``tree`` in sorted-by-relative-path
    order. Hash absorbs each file's relative path (UTF-8 + NUL
    terminator) and content (length-prefixed) into a single digest.
    Directory entries themselves are not hashed — only file contents.
    Skip the per-pack pubkey sidecar(s) and any ``manifest.jwt`` so
    the JWT does not have to re-hash itself.
    """
    h: Any = hashlib.sha256() if _fips_mode() else blake3()
    for path in sorted(tree.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(tree).as_posix()
        # Skip sidecar pubkey + manifest.jwt — these are signing artifacts,
        # not pack content.
        if rel.endswith(".pub.pem") or rel == "manifest.jwt":
            continue
        content = path.read_bytes()
        h.update(rel.encode("utf-8"))
        h.update(b"\x00")
        h.update(len(content).to_bytes(8, "big"))
        h.update(content)
    return h.hexdigest()


def _fingerprint(pub_pem_bytes: bytes) -> str:
    """SHA-256 hex of the DER-encoded public key.

    DER-encode normalizes whitespace + line-ending differences in PEM
    so the fingerprint is stable across encoders.
    """
    pub = load_pem_public_key(pub_pem_bytes)
    if not isinstance(pub, Ed25519PublicKey):
        raise PackSignatureError(
            "trust-store public key is not Ed25519",
            reason="not-ed25519",
        )
    der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    return hashlib.sha256(der).hexdigest()


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of :func:`verify_pack`.

    * ``verified`` — True iff the JWT signature, algorithm whitelist,
      tree-hash, and trust-store check all passed.
    * ``key_id`` — the ``kid`` extracted from the JWT header (always
      populated when decode succeeded).
    * ``reason`` — short tag explaining a False outcome
      (``"alg-not-eddsa"``, ``"tree-hash-mismatch"``, etc.). ``None``
      on success.
    """

    verified: bool
    key_id: str | None
    reason: str | None = None


class TrustStore(Protocol):
    """Trust-store interface for :func:`verify_pack`.

    Implementations decide policy (TOFU vs explicit allow-list) and
    storage (filesystem vs in-memory). The two methods cover the two
    paths verify_pack needs:

    * :meth:`get_pubkey_pem` — return the PEM-encoded public key bytes
      for ``key_id`` (None if unknown).
    * :meth:`pin` — record a fingerprint for ``key_id`` (TOFU first
      sight); raise on mismatch.
    """

    def get_pubkey_pem(self, key_id: str) -> bytes | None: ...

    def pin(self, key_id: str, pub_pem_bytes: bytes) -> None: ...


class StaticTrustStore:
    """Explicit ``{key_id: pub_pem_bytes}`` allow-list — cleared default.

    No TOFU. Unknown ``key_id`` → :meth:`get_pubkey_pem` returns None.
    :meth:`pin` is a no-op (the store is read-only by design).
    """

    def __init__(self, allowed_keys: dict[str, bytes]) -> None:
        self._allowed: dict[str, bytes] = dict(allowed_keys)

    def get_pubkey_pem(self, key_id: str) -> bytes | None:
        return self._allowed.get(key_id)

    def pin(self, key_id: str, pub_pem_bytes: bytes) -> None:
        # Static stores never pin — first-sight under cleared is a
        # load-fail enforced upstream by verify_pack. Args ignored on
        # purpose to satisfy the TrustStore Protocol.
        del key_id, pub_pem_bytes
        return None


class FilesystemTrustStore:
    """TOFU-backed trust store at ``<config_dir>/trusted_keys/``.

    First sight of a ``key_id``: caller reads the per-pack sidecar
    ``<pack_dir>/<key_id>.pub.pem``, calls :meth:`pin`, which
    fingerprints + writes ``<key_id>.json``. Subsequent verifies call
    :meth:`get_pubkey_pem` to fetch the recorded fingerprint, then
    :meth:`pin` again to assert the live sidecar still matches.
    """

    def __init__(self, config_dir: Path) -> None:
        self._dir = Path(config_dir) / "trusted_keys"

    def _record_path(self, key_id: str) -> Path:
        return self._dir / f"{key_id}.json"

    def get_pubkey_pem(self, key_id: str) -> bytes | None:
        # Filesystem store does not cache PEM bytes — sidecar is
        # canonical. Returning None here forces verify_pack down the
        # TOFU pin() path. The fingerprint comparison happens in pin().
        return None

    def get_recorded_fingerprint(self, key_id: str) -> str | None:
        path = self._record_path(key_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PackSignatureError(
                "trusted-keys record is unreadable",
                key_id=key_id,
                reason="record-corrupt",
            ) from exc
        fp = data.get("fingerprint")
        if not isinstance(fp, str):
            raise PackSignatureError(
                "trusted-keys record is missing fingerprint",
                key_id=key_id,
                reason="record-corrupt",
            )
        return fp

    def pin(self, key_id: str, pub_pem_bytes: bytes) -> None:
        live_fp = _fingerprint(pub_pem_bytes)
        recorded = self.get_recorded_fingerprint(key_id)
        if recorded is None:
            # First sight — write the record.
            self._dir.mkdir(parents=True, exist_ok=True)
            now = datetime.datetime.now(datetime.UTC).isoformat()
            self._record_path(key_id).write_text(
                json.dumps(
                    {
                        "key_id": key_id,
                        "fingerprint": live_fp,
                        "first_seen": now,
                    },
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            _LOG.warning(
                "TOFU pin for new key_id=%s fingerprint=%s — accept on first use",
                key_id,
                live_fp,
            )
            return
        if recorded != live_fp:
            raise PackSignatureError(
                "key_id fingerprint changed since first use",
                key_id=key_id,
                expected_fingerprint=recorded,
                actual_fingerprint=live_fp,
                reason="fingerprint-mismatch",
            )


def sign_pack(tree: os.PathLike[str] | str, signing_key: bytes, key_id: str) -> str:
    """Sign a pack tree and return a compact EdDSA-JWT.

    Args:
        tree: directory containing the pack (manifest + rules + ...).
              Sidecar ``<key_id>.pub.pem`` and ``manifest.jwt`` are
              skipped during tree-hash computation.
        signing_key: PEM-encoded Ed25519 PKCS8 private key bytes.
        key_id: short stable identifier (matches the sidecar filename
                ``<key_id>.pub.pem`` and the JWT ``kid`` header).

    The JWT payload is canonical-JSON (sort_keys, no whitespace) over::

        {
          "alg": "BLAKE3" | "SHA-256",   # tree-hash algo, NOT JWT alg
          "iat": <unix timestamp>,
          "key_id": "<key_id>",
          "tree_hash": "<hex digest>"
        }

    The JWT ``alg`` header is always ``EdDSA``; the ``alg`` payload
    field is the *tree-hash* algorithm, not the JWT signing algorithm.
    """
    pack = Path(tree)
    priv = load_pem_private_key(signing_key, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        raise PackSignatureError(
            "signing key is not Ed25519",
            key_id=key_id,
            reason="not-ed25519",
        )
    payload = {
        "alg": _hash_algo_name(),
        "iat": int(time.time()),
        "key_id": key_id,
        "tree_hash": _tree_hash(pack),
    }
    return jwt.encode(
        payload,
        priv,
        algorithm="EdDSA",
        headers={"kid": key_id},
    )


def _decode_strict(token: str, pub_pem: bytes) -> dict[str, object]:
    """Decode + verify a JWT under STRICT algorithm whitelist.

    Refuses any header that includes ``x5c`` (untrusted certificate-in-
    JWT attack vector — locked Decision #4).
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise PackSignatureError(
            "JWT header is unparseable",
            reason="header-unparseable",
        ) from exc
    if "x5c" in header:
        raise PackSignatureError(
            "JWT carries embedded x5c — refused (locked Decision #4)",
            reason="x5c-rejected",
        )
    if header.get("alg") != "EdDSA":
        raise PackSignatureError(
            "JWT alg is not EdDSA",
            actual_alg=header.get("alg"),
            reason="alg-not-eddsa",
        )
    pub = load_pem_public_key(pub_pem)
    if not isinstance(pub, Ed25519PublicKey):
        raise PackSignatureError(
            "trust-store public key is not Ed25519",
            reason="not-ed25519",
        )
    try:
        return jwt.decode(token, pub, algorithms=["EdDSA"])
    except jwt.InvalidTokenError as exc:
        raise PackSignatureError(
            "JWT signature verification failed",
            reason="signature-invalid",
        ) from exc


def _refuse_or_warn(
    profile: Profile,
    *,
    key_id: str | None,
    reason: str,
    message: str,
) -> VerifyResult:
    """Profile-conditional failure surface.

    Cleared profile (``signature_verify_mandatory=True``) raises
    :class:`PackSignatureError`. OSS-default logs a warning and returns
    a non-verified Result. TOFU-fingerprint drift bypasses this helper
    and raises directly on both profiles (security boundary).
    """
    if profile.signature_verify_mandatory:
        raise PackSignatureError(message, key_id=key_id, reason=reason)
    _LOG.warning("pack-signature-verify failed: %s (key_id=%s, reason=%s)", message, key_id, reason)
    return VerifyResult(verified=False, key_id=key_id, reason=reason)


def verify_pack(
    tree: os.PathLike[str] | str,
    token: str,
    trust_store: TrustStore,
    profile: Profile,
) -> VerifyResult:
    """Verify a signed pack against a trust store under a profile.

    TOFU pathway (when the trust store returns ``None`` for the
    ``key_id`` and is a :class:`FilesystemTrustStore`): read the per-
    pack sidecar ``<pack_dir>/<key_id>.pub.pem``, fingerprint it, and
    pin via :meth:`FilesystemTrustStore.pin`. A fingerprint mismatch
    raises under both profiles — TOFU drift is a security boundary,
    not a profile preference.

    Failure surface (per design §17 Decision #4):

    * Cleared profile → :class:`PackSignatureError`.
    * OSS-default → WARNING log + ``VerifyResult(verified=False)``.
    """
    pack = Path(tree)

    # 1. Strict header inspection (alg whitelist, x5c refusal) — done
    # before we touch the trust store so a malformed JWT cannot cause
    # a TOFU pin side effect.
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise PackSignatureError(
            "JWT header is unparseable",
            reason="header-unparseable",
        ) from exc
    if "x5c" in header:
        raise PackSignatureError(
            "JWT carries embedded x5c — refused (locked Decision #4)",
            reason="x5c-rejected",
        )
    if header.get("alg") != "EdDSA":
        raise PackSignatureError(
            "JWT alg is not EdDSA",
            actual_alg=header.get("alg"),
            reason="alg-not-eddsa",
        )
    key_id = header.get("kid")
    if not isinstance(key_id, str) or not key_id:
        raise PackSignatureError(
            "JWT missing kid header",
            reason="kid-missing",
        )

    # 2. Resolve the public key. Static stores must have it; filesystem
    # stores fall through to TOFU sidecar.
    pub_pem = trust_store.get_pubkey_pem(key_id)
    sidecar_used = False
    if pub_pem is None:
        sidecar = pack / f"{key_id}.pub.pem"
        if not sidecar.exists():
            return _refuse_or_warn(
                profile,
                key_id=key_id,
                reason="untrusted-key",
                message="key_id not in trust store and no sidecar pub.pem present",
            )
        try:
            pub_pem = sidecar.read_bytes()
        except OSError as exc:
            raise PackSignatureError(
                "sidecar pub.pem is unreadable",
                key_id=key_id,
                reason="sidecar-unreadable",
            ) from exc
        sidecar_used = True
        # Cleared + filesystem-store-with-no-record is also a load-fail
        # via _refuse_or_warn — but only when the store is StaticTrustStore.
        # FilesystemTrustStore handles cleared-mode the same as oss: TOFU
        # pins on first sight, mismatches raise (handled below).
        if isinstance(trust_store, StaticTrustStore):
            return _refuse_or_warn(
                profile,
                key_id=key_id,
                reason="untrusted-key",
                message="key_id not in static allow-list",
            )

    # 3. TOFU pin (FilesystemTrustStore only — pin is a no-op for
    # StaticTrustStore by design). Mismatch raises under both profiles
    # via the FilesystemTrustStore.pin() implementation.
    if sidecar_used or isinstance(trust_store, FilesystemTrustStore):
        # Re-read sidecar to feed pin() even when we already have pub_pem
        # from the store path (FilesystemTrustStore intentionally returns
        # None for get_pubkey_pem).
        sidecar = pack / f"{key_id}.pub.pem"
        if sidecar.exists():
            try:
                live_pem = sidecar.read_bytes()
            except OSError as exc:
                raise PackSignatureError(
                    "sidecar pub.pem is unreadable",
                    key_id=key_id,
                    reason="sidecar-unreadable",
                ) from exc
            trust_store.pin(key_id, live_pem)
            pub_pem = live_pem
        elif isinstance(trust_store, FilesystemTrustStore):
            return _refuse_or_warn(
                profile,
                key_id=key_id,
                reason="untrusted-key",
                message="filesystem trust store has no sidecar pub.pem",
            )

    # 4. Decode + signature verify (STRICT algorithms whitelist).
    # pub_pem is guaranteed bytes here: step 2 returns or assigns from
    # the static store + sidecar; step 3 may overwrite with a fresh
    # sidecar read but never sets it back to None.
    assert pub_pem is not None
    try:
        payload = _decode_strict(token, pub_pem)
    except PackSignatureError:
        if profile.signature_verify_mandatory:
            raise
        _LOG.warning("pack-signature-verify failed for key_id=%s", key_id)
        return VerifyResult(verified=False, key_id=key_id, reason="signature-invalid")

    # 5. Tree-hash check.
    expected_hash = payload.get("tree_hash")
    actual_hash = _tree_hash(pack)
    if expected_hash != actual_hash:
        return _refuse_or_warn(
            profile,
            key_id=key_id,
            reason="tree-hash-mismatch",
            message="pack tree-hash differs from signed value",
        )

    # 6. key_id sanity — payload must echo header kid.
    if payload.get("key_id") != key_id:
        return _refuse_or_warn(
            profile,
            key_id=key_id,
            reason="key-id-mismatch",
            message="JWT payload key_id differs from header kid",
        )

    return VerifyResult(verified=True, key_id=key_id, reason=None)
