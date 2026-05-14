# SPDX-License-Identifier: Apache-2.0
"""Recombine Shamir shares + sign a kill_signal fact (CRITERIA fancy #8 + #11).

Loads any 2-of-3 share files written by ``shamir_split.py``,
reconstructs the krakntrust seed, signs a JWS attestation over a
``cve_rem.kill_signal`` payload (kind, role, run_id, etc.), and
writes the JWS to ``stdout`` for downstream injection into the
Fathom engine via ``fathom_killswitch_daemon`` or a dedicated
ceremony emitter.

Demonstrates: ship requires 2-of-3 quorum (Shamir-recovery), AND
the recovered key is then used to author a signed fact. A single
share cannot author the fact at all -- not just rule-rejected, but
cryptographically impossible.

Usage:
  uv run --no-project python -m demos.cve_remediation.scripts.shamir_recombine \
    --shares dev-keys/shares/security-eng.share.json \
             dev-keys/shares/pipeline-owner.share.json \
    --kind halt-rollback-in-flight \
    --run-id run-abc \
    --actor security-eng-actor

Two share paths required (any pair of {security-eng, pipeline-owner,
netops-lead}); 3 are accepted but redundant. 1 path: hard error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import jwt
from Crypto.Protocol.SecretSharing import Shamir
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="shamir_recombine")
    parser.add_argument("--shares", nargs="+", required=True,
                        help="Path(s) to share JSON envelopes; >=2 needed.")
    parser.add_argument("--kind", required=True,
                        choices=("halt-new", "halt-pause-in-flight",
                                 "halt-rollback-in-flight"))
    parser.add_argument("--run-id", default="fleet")
    parser.add_argument("--actor", required=True)
    args = parser.parse_args(argv)

    if len(args.shares) < 2:
        parser.error("need at least 2 distinct share files (2-of-3 quorum)")

    envelopes = []
    seen_roles: set[str] = set()
    for path in args.shares:
        env = json.loads(Path(path).read_text(encoding="utf-8"))
        if env["role"] in seen_roles:
            parser.error(
                f"duplicate role {env['role']!r}: 2-of-3 quorum requires "
                "distinct roles"
            )
        seen_roles.add(env["role"])
        envelopes.append(env)

    key_ids = {e["key_id"] for e in envelopes}
    if len(key_ids) != 1:
        parser.error(f"share files mismatch on key_id: {key_ids}")
    key_id = key_ids.pop()

    lo_shares = [(e["lo"][0], bytes.fromhex(e["lo"][1])) for e in envelopes]
    hi_shares = [(e["hi"][0], bytes.fromhex(e["hi"][1])) for e in envelopes]
    seed = Shamir.combine(lo_shares) + Shamir.combine(hi_shares)
    priv = Ed25519PrivateKey.from_private_bytes(seed)
    priv_pem = priv.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption(),
    )

    payload = {
        "iss": key_id,
        "kid": key_id,
        "iat": int(time.time()),
        "kill_signal": {
            "kind": args.kind,
            "role": envelopes[0]["role"],   # primary signer
            "actor": args.actor,
            "run_id": args.run_id,
            "co_signers": sorted(seen_roles),
        },
    }
    token = jwt.encode(
        payload, priv_pem, algorithm="EdDSA", headers={"kid": key_id},
    )
    print(f"recombined under {sorted(seen_roles)}; key_id={key_id}",
          file=sys.stderr)
    sys.stdout.write(token + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
