# SPDX-License-Identifier: Apache-2.0
"""Daily audit-chain anchor (Fancy CRITERIA #10).

Walks all run-attestation JWS artifacts under
``$HARBOR_ARTIFACTS_ROOT/attestations/``, computes a chain head
(SHA256 over each JWS in deterministic order), signs it with the
krakntrust dev key, and writes:

  ``<artifacts>/anchors/<YYYY-MM-DD>.anchor.json``

Schema:
  {
    "anchor_date": "YYYY-MM-DD",
    "chain_head_sha256": "<hex>",
    "entry_count": <int>,
    "entries": [{"sha256": "...", "path": "..."}],
    "signed_jws": "<EdDSA compact>",
    "key_id": "krakntrust-cve-rem-<fp>",
    "boot_session_id": "<blake3 of pubkey pem>",
    "anchored_at": "<ISO8601>"
  }

Usage:
  uv run --no-project python -m demos.cve_remediation.scripts.anchor_daily \
      [--date YYYY-MM-DD]

External verifier (separate script ``verify_F10_audit_anchor``):
  - Loads anchor file.
  - Recomputes chain_head over the listed entries (verifying each
    file still hashes to the recorded sha256).
  - Verifies the JWS signature with the on-disk krakntrust pubkey.
  - Asserts mtime within last 24h (active anchor); 24h gap = page,
    72h gap = halt-new fires.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from demos.cve_remediation.krakntrust import (
    load_or_create_keypair,
    sign_attestation,
)

_ARTIFACTS_ROOT = Path(
    os.environ.get("HARBOR_ARTIFACTS_ROOT", ".harbor/artifacts")
)


def _attestation_dir() -> Path:
    return _ARTIFACTS_ROOT / "attestations"


def _anchor_dir() -> Path:
    out = _ARTIFACTS_ROOT / "anchors"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _enumerate_entries(target_date: date) -> list[dict[str, str]]:
    """Return sorted, deterministic entries for the chain head.

    Each entry: ``{"sha256": "<hex>", "path": "<rel>"}``. Order is
    by sha256 ascending so the head is invariant under file-system
    enumeration order. Filters to JWS files whose mtime falls within
    the requested ``target_date`` (UTC). Without an mtime filter the
    chain head would aggregate all-time history; daily anchors must
    pin the day's incremental contribution.
    """
    src = _attestation_dir()
    if not src.exists():
        return []
    rows: list[dict[str, str]] = []
    day_start = datetime(
        target_date.year, target_date.month, target_date.day, tzinfo=UTC
    ).timestamp()
    day_end = day_start + 86400
    for p in src.glob("*.jws"):
        mt = p.stat().st_mtime
        if mt < day_start or mt >= day_end:
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        rows.append({
            "sha256": sha,
            "path": str(p.relative_to(_ARTIFACTS_ROOT)),
        })
    rows.sort(key=lambda r: r["sha256"])
    return rows


def _chain_head(entries: list[dict[str, str]]) -> str:
    canonical = json.dumps(
        [{"sha256": e["sha256"]} for e in entries],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _build_anchor(target_date: date) -> dict[str, Any]:
    ident = load_or_create_keypair()
    entries = _enumerate_entries(target_date)
    head = _chain_head(entries)
    payload = {
        "iss": ident.key_id,
        "kid": ident.key_id,
        "iat": int(datetime.now(UTC).timestamp()),
        "anchor_date": target_date.isoformat(),
        "chain_head_sha256": head,
        "entry_count": len(entries),
        "boot_session_id": ident.boot_session_id,
    }
    signed = sign_attestation(payload, ident)
    return {
        "anchor_date": target_date.isoformat(),
        "chain_head_sha256": head,
        "entry_count": len(entries),
        "entries": entries,
        "signed_jws": signed,
        "key_id": ident.key_id,
        "boot_session_id": ident.boot_session_id,
        "anchored_at": datetime.now(UTC).isoformat(),
    }


def write_anchor(target_date: date) -> Path:
    anchor = _build_anchor(target_date)
    out = _anchor_dir() / f"{target_date.isoformat()}.anchor.json"
    out.write_text(
        json.dumps(anchor, sort_keys=True, indent=2),
        encoding="utf-8",
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="harbor anchor-daily")
    parser.add_argument(
        "--date",
        default=datetime.now(UTC).date().isoformat(),
        help="Target date (UTC, YYYY-MM-DD). Defaults to today.",
    )
    args = parser.parse_args(argv)
    try:
        target = date.fromisoformat(args.date)
    except ValueError:
        parser.error(f"invalid date: {args.date!r}")
    out = write_anchor(target)
    print(f"wrote anchor: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
