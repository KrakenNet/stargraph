# SPDX-License-Identifier: Apache-2.0
"""Split krakntrust dev key into 3 Shamir shares (CRITERIA fancy #8).

Reads ``demos/cve_remediation/dev-keys/krakntrust-cve-rem.priv.pem``,
extracts the 32-byte Ed25519 seed, splits each 16-byte half via
PyCryptodome ``Shamir.split(2, 3, ...)``, and writes one share file
per role under ``demos/cve_remediation/dev-keys/shares/``:

* ``security-eng.share.json``
* ``pipeline-owner.share.json``
* ``netops-lead.share.json``

Each share file is a JSON envelope:

    {"role": "...", "key_id": "krakntrust-cve-rem-<fp>",
     "lo": [<index>, "<hex>"], "hi": [<index>, "<hex>"]}

The hex-encoded share bytes are the textbook PyCryptodome
``(idx, bytes)`` tuples. ``key_id`` lets the recombiner refuse a
share file mismatched against a rotated keypair.

Production: each share would land on a separate hardware token, not
on the same laptop disk. This demo writes them to a gitignored
subdirectory; rotate by re-running the script and physically
distributing the new shares.

Usage:
  uv run --no-project python -m demos.cve_remediation.scripts.shamir_split
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from Crypto.Protocol.SecretSharing import Shamir
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from demos.cve_remediation.krakntrust import (
    _DEV_KEYS_DIR,  # type: ignore[attr-defined]
    load_or_create_keypair,
)

_ROLES = ("security-eng", "pipeline-owner", "netops-lead")
_SHARE_DIR = _DEV_KEYS_DIR / "shares"


def main() -> int:
    ident = load_or_create_keypair()
    priv = load_pem_private_key(ident.priv_pem, password=None)
    if not isinstance(priv, Ed25519PrivateKey):
        print("krakntrust priv is not Ed25519")
        return 1
    seed = priv.private_bytes_raw()
    if len(seed) != 32:
        print(f"unexpected seed length: {len(seed)}")
        return 1

    lo_shares = Shamir.split(2, 3, seed[:16])
    hi_shares = Shamir.split(2, 3, seed[16:])

    _SHARE_DIR.mkdir(parents=True, exist_ok=True)
    for i, role in enumerate(_ROLES):
        idx_lo, raw_lo = lo_shares[i]
        idx_hi, raw_hi = hi_shares[i]
        envelope = {
            "role": role,
            "key_id": ident.key_id,
            "boot_session_id": ident.boot_session_id,
            "lo": [idx_lo, raw_lo.hex()],
            "hi": [idx_hi, raw_hi.hex()],
        }
        path = _SHARE_DIR / f"{role}.share.json"
        path.write_text(
            json.dumps(envelope, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        path.chmod(0o600)
        print(f"wrote {path}")
    print(f"\nkey_id          : {ident.key_id}")
    print(f"boot_session_id : {ident.boot_session_id[:16]}...")
    print("\nProduction: distribute one share per role to separate "
          "hardware tokens. Demo: shares live in dev-keys/shares/ "
          "(gitignored). Rotate by re-running this script.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
