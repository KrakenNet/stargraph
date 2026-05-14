# SPDX-License-Identifier: Apache-2.0
"""Sign the 5 cve_rem.* Bosun packs with a krakntrust dev key (E2).

Mirrors :mod:`scripts.sign_bosun_packs` for the demo's pack tree.
Generates an Ed25519 keypair in process, signs every pack via
:func:`harbor.bosun.signing.sign_pack`, and writes:

  - ``<pack_dir>/manifest.jwt``  — compact EdDSA-JWT
  - ``<pack_dir>/<key_id>.pub.pem`` — TOFU sidecar
  - ``demos/cve_remediation/dev-keys/krakntrust-cve-rem.pub.pem`` —
    project-scoped pubkey copy (committed)

Idempotent: re-running with no rule changes yields the same
``tree_hash`` (the JWT differs only in ``iat``).

Usage::

    uv run --no-project python -m demos.cve_remediation.sign_packs

Phase E follow-on: rotate the key by re-running the script and
deleting the ``trusted_keys/<key_id>.json`` TOFU pin.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from harbor.bosun.signing import sign_pack

PACKS: tuple[str, ...] = (
    "cve_rem.routing",
    "cve_rem.kill_switches",
    "cve_rem.doctrine_trust",
    "cve_rem.offline_isolation",
    "cve_rem.gepa_score_policy",
)

DEMO_DIR = Path(__file__).resolve().parent
RULES_DIR = DEMO_DIR / "graph" / "rules"
DEV_KEYS_DIR = DEMO_DIR / "dev-keys"
PUBKEY_DEST = DEV_KEYS_DIR / "krakntrust-cve-rem.pub.pem"


def _make_keypair() -> tuple[bytes, bytes, str]:
    """Generate an Ed25519 keypair; return (priv_pem, pub_pem, key_id).

    ``key_id`` is ``krakntrust-cve-rem-<8 hex>`` — stable across rotations
    only when the same private key is reused. The convention matches the
    Bosun reference packs' ``dev-bosun-<8 hex>`` suffix shape.
    """
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    priv_pem = priv.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    pub_pem = pub.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    pub_der = pub.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    fp = hashlib.sha256(pub_der).hexdigest()[:8]
    return priv_pem, pub_pem, f"krakntrust-cve-rem-{fp}"


def main() -> int:
    priv_pem, pub_pem, key_id = _make_keypair()
    print(f"Generated dev keypair  key_id={key_id}")

    DEV_KEYS_DIR.mkdir(parents=True, exist_ok=True)
    PUBKEY_DEST.write_bytes(pub_pem)
    print(f"Wrote project pubkey  {PUBKEY_DEST}")

    for pack_name in PACKS:
        pack_dir = RULES_DIR / pack_name
        if not pack_dir.is_dir():
            print(f"SKIP {pack_name}: missing pack dir")
            continue

        # Drop stale sidecars so we don't accumulate rotated pubkeys.
        for stale in pack_dir.glob("krakntrust-cve-rem-*.pub.pem"):
            stale.unlink()

        # Write the fresh sidecar before signing — sign_pack excludes
        # ``*.pub.pem`` and ``manifest.jwt`` from the tree-hash, so the
        # write order does not affect determinism.
        (pack_dir / f"{key_id}.pub.pem").write_bytes(pub_pem)
        token = sign_pack(pack_dir, priv_pem, key_id)
        (pack_dir / "manifest.jwt").write_text(token, encoding="utf-8")
        print(f"  signed {pack_name}  ({len(token)} bytes)")

    print(f"All {len(PACKS)} packs signed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
