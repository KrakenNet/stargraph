# SPDX-License-Identifier: Apache-2.0
"""Re-sign the four ``stargraph.bosun.*`` reference packs (Phase-4 task 4.1-4.4).

Generates a fresh Ed25519 keypair, derives a 16-char ``key_id`` from the
DER-encoded public key fingerprint (matching the established
``dev-bosun-<8-hex>`` convention), and signs each pack tree via
:func:`stargraph.bosun.signing.sign_pack`. Writes the resulting JWT to
``manifest.jwt`` and the public key to ``<key_id>.pub.pem`` (TOFU
sidecar) inside each pack directory.

The private key is **never persisted** — it lives only in this process's
memory. To rotate the key, re-run this script; the new pubkey overwrites
the old sidecar and the FilesystemTrustStore TOFU pin record needs to
be removed from ``<config_dir>/trusted_keys/`` to accept the new pin.

Idempotent: re-running with no rule/manifest changes produces a stable
``tree_hash`` payload (the JWT differs only in ``iat``).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from stargraph.bosun.signing import sign_pack

PACKS = ("budgets", "audit", "safety_pii", "retries")
ROOT = Path(__file__).parent.parent / "src" / "stargraph" / "bosun"
DEV_KEYS = Path(__file__).parent.parent / "dev" / "keys"


def _make_keypair() -> tuple[bytes, bytes, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    fp_short = hashlib.sha256(pub_der).hexdigest()[:8]
    return priv_pem, pub_pem, f"dev-bosun-{fp_short}"


def main() -> None:
    priv_pem, pub_pem, key_id = _make_keypair()
    print(f"Generated dev keypair, key_id={key_id}")

    # Persist only the public half — committed to dev/keys/.
    DEV_KEYS.mkdir(parents=True, exist_ok=True)
    (DEV_KEYS / "dev-bosun.pub.pem").write_bytes(pub_pem)

    for pack_name in PACKS:
        pack_dir = ROOT / pack_name
        # Drop any prior dev-bosun-* sidecar(s) so we don't accumulate stale pubkeys.
        for stale in pack_dir.glob("dev-bosun-*.pub.pem"):
            stale.unlink()
        # Also drop the legacy non-key-id suffix file if present.
        legacy = pack_dir / "dev-bosun.pub.pem"
        if legacy.exists():
            legacy.unlink()
        # Write the fresh sidecar before signing so the tree-hash is stable.
        # Note: sign_pack itself excludes ``*.pub.pem`` + ``manifest.jwt`` from
        # the tree-hash, so the sidecar write order does not affect the hash.
        (pack_dir / f"{key_id}.pub.pem").write_bytes(pub_pem)
        token = sign_pack(pack_dir, priv_pem, key_id)
        (pack_dir / "manifest.jwt").write_text(token, encoding="utf-8")
        print(f"  signed {pack_name} ({len(token)} bytes)")

    print("All 4 packs re-signed.")


if __name__ == "__main__":
    main()
