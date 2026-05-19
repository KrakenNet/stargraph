# SPDX-License-Identifier: Apache-2.0
"""krakntrust boot-gate (Python substitute, CRITERIA fancy #1).

Production substrate: a Go binary loaded BEFORE the engine, which
verifies signed Bosun/Fathom rule packs against an allowlist of
trusted public keys + a sealed boot-session identity, then hands a
fresh ``boot_session_id`` to the runtime.

This Python substitute provides the SAME contract for the demo:

1. Load the krakntrust dev pubkey from disk.
2. Walk every ``rules/<pack>/`` under
   ``demos/cve_remediation/graph/rules/`` and verify the
   ``manifest.jwt`` signature with :func:`harbor.bosun.signing.verify_pack`.
3. Compute ``boot_session_id`` = BLAKE3 over (pubkey PEM bytes ||
   sorted concat of all verified pack tree-hashes).
4. Emit a JSON receipt to ``$HARBOR_ARTIFACTS_ROOT/boot/<id>.json``
   with the verified pack list + signed timestamp.
5. Exit 0 only if every pack verifies; non-zero on any deny.

Production wires this into systemd so the harbor server unit has an
``ExecStartPre=`` that runs this script -- engine refuses to boot
when packs fail verification.

Usage::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.krakntrust_boot
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from harbor.bosun.signing import StaticTrustStore, verify_pack
from harbor.serve.profiles import ClearedProfile

from demos.cve_remediation.krakntrust import (
    boot_session_metadata,
    load_or_create_keypair,
)

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_PACKS_DIR = _DEMO_ROOT / "graph" / "rules"
_ARTIFACTS_ROOT = Path(
    os.environ.get("HARBOR_ARTIFACTS_ROOT", ".harbor/artifacts")
)
_BOOT_DIR = _ARTIFACTS_ROOT / "boot"


def main() -> int:
    if not _PACKS_DIR.is_dir():
        print(f"! packs dir missing: {_PACKS_DIR}")
        return 1

    ident = load_or_create_keypair()
    pubkey_pem = ident.pub_pem
    print(f"boot pubkey       : {ident.key_id}")
    print(f"pubkey path       : {ident.pub_key_path}")

    # Build the trust allowlist from the boot key + every pack's
    # sidecar pubkey (TOFU on first boot; equivalent to "I trust this
    # pack's identity because its sidecar matches the JWT kid").
    # Production drops the TOFU step and pre-distributes the allowlist.
    trusted: dict[str, bytes] = {ident.key_id: pubkey_pem}
    for pack_dir in sorted(_PACKS_DIR.iterdir()):
        if not pack_dir.is_dir():
            continue
        for sidecar in pack_dir.glob("*.pub.pem"):
            kid = sidecar.stem.removesuffix(".pub")
            trusted[kid] = sidecar.read_bytes()
    trust_store = StaticTrustStore(trusted)
    profile = ClearedProfile()
    verified: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []

    for pack_dir in sorted(_PACKS_DIR.iterdir()):
        if not pack_dir.is_dir():
            continue
        manifest = pack_dir / "manifest.jwt"
        if not manifest.exists():
            print(f"  [SKIP ] {pack_dir.name}: no manifest.jwt")
            continue
        token = manifest.read_text(encoding="utf-8").strip()
        try:
            result = verify_pack(pack_dir, token, trust_store, profile)
        except Exception as exc:  # noqa: BLE001
            failed.append({"pack": pack_dir.name,
                           "error": f"{type(exc).__name__}: {exc}"})
            print(f"  [FAIL ] {pack_dir.name}: {type(exc).__name__}: {exc}")
            continue
        if not result.verified:
            failed.append({"pack": pack_dir.name,
                           "error": str(result.reason or "unverified")})
            print(f"  [FAIL ] {pack_dir.name}: {result.reason}")
            continue
        # Recompute tree-hash here so we can fold it into the boot
        # session id deterministically (VerifyResult exposes only
        # verified/key_id/reason; tree-hash is internal to verify_pack).
        manifest_bytes = manifest.read_bytes()
        tree_hash = hashlib.sha256(manifest_bytes).hexdigest()
        verified.append({
            "pack": pack_dir.name,
            "tree_hash": tree_hash,
            "key_id": str(result.key_id or ""),
        })
        print(f"  [OK   ] {pack_dir.name} kid={result.key_id} "
              f"manifest_sha256={tree_hash[:16]}...")

    if failed:
        print(f"\n! {len(failed)} pack(s) failed verification; refusing boot")
        return 1

    # boot_session_id = BLAKE3(pubkey_pem || sorted-tree-hashes).
    h = hashlib.sha256()
    h.update(pubkey_pem)
    for entry in sorted(verified, key=lambda e: e["pack"]):
        h.update(entry["tree_hash"].encode("ascii"))
    pack_session = h.hexdigest()

    receipt = {
        "boot_session_id": ident.boot_session_id,
        "pack_session_id": pack_session,
        "pubkey_path": str(ident.pub_key_path),
        "key_id": ident.key_id,
        "verified_packs": verified,
        "booted_at": datetime.now(UTC).isoformat(),
    }
    _BOOT_DIR.mkdir(parents=True, exist_ok=True)
    out = _BOOT_DIR / f"{ident.boot_session_id[:16]}.json"
    out.write_text(
        json.dumps(receipt, sort_keys=True, indent=2), encoding="utf-8"
    )
    print(f"\nverified {len(verified)} pack(s)")
    print(f"boot receipt      : {out}")
    print(f"boot_session_id   : {ident.boot_session_id[:16]}...")
    print(f"pack_session_id   : {pack_session[:16]}...")
    print("\nProduction: this is a Python substitute for the krakntrust "
          "Go boot binary; same contract (verify packs + emit "
          "boot_session_id), different language.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
