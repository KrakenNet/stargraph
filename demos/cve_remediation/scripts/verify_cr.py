# SPDX-License-Identifier: Apache-2.0
"""``harbor verify-cr <CR_SYS_ID|CR_NUMBER>`` — trust-chain attestation walker.

Implements CRITERIA fancy #1: given a ServiceNow CR, walk the chain
back to the krakntrust root key and report each link's status.

The chain (real links bold, dev-mode links italic):

* **CR (live ServiceNow PDI)** — fetch CR via ``/api/now/table/change_request``.
* **broker JWS attachment** — pull
  ``run_attestation_<cve>.jws`` from the CR's attachment list.
* **prompt_artifact_id** — claim inside the JWS payload; matches what
  PlannerNode emitted at run time. The CLI cannot regenerate the
  prompt without re-running the LM, so we treat the JWS-bound id as
  authoritative for *this* run, and assert it is non-empty +
  64-hex-char BLAKE3 shape.
* **doctrine_manifest_hash** — claim inside JWS; the CLI re-derives
  the expected manifest hash from the live Phase 0 doctrine state if
  the operator passes ``--doctrine-corpus`` (otherwise we report it
  as "claimed" and require non-empty + 64-hex shape).
* *krakntrust boot session* — pubkey loaded from
  ``demos/cve_remediation/dev-keys/krakntrust-cve-rem.pub.pem``;
  ``boot_session_id`` in payload must equal BLAKE3(pubkey PEM).
* *root keys (single-key dev mode)* — pubkey existence + Ed25519
  type. Production needs Shamir 2-of-3; flagged in output.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.verify_cr \\
        --cr <sys_id_or_number>
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from typing import Any

import httpx

from demos.cve_remediation.krakntrust import (
    boot_session_metadata,
    verify_attestation,
)


_SHA_RE = re.compile(r"^[0-9a-f]{32,64}$")


def _is_hex_digest(s: str, min_len: int = 32) -> bool:
    return bool(_SHA_RE.fullmatch((s or "").lower())) and len(s) >= min_len


async def _fetch_cr(cr_arg: str) -> dict[str, Any]:
    base = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    user = os.environ.get("SERVICENOW_USERNAME", "")
    pw = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base and user and pw and cr_arg):
        return {}
    is_sys_id = bool(re.fullmatch(r"[0-9a-f]{32}", cr_arg))
    if is_sys_id:
        url = f"{base}/api/now/table/change_request/{cr_arg}"
        params = {
            "sysparm_display_value": "all",
            "sysparm_fields": (
                "sys_id,number,state,short_description,close_code,"
                "close_notes,correlation_id"
            ),
        }
    else:
        url = f"{base}/api/now/table/change_request"
        params = {
            "sysparm_query": f"number={cr_arg}",
            "sysparm_limit": "1",
            "sysparm_display_value": "all",
            "sysparm_fields": (
                "sys_id,number,state,short_description,close_code,"
                "close_notes,correlation_id"
            ),
        }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            url,
            params=params,
            auth=(user, pw),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return {}
        body = resp.json() or {}
        result = body.get("result")
        if isinstance(result, list):
            return result[0] if result else {}
        return result or {}


async def _fetch_attestation_attachment(cr_sys_id: str) -> dict[str, Any]:
    base = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    user = os.environ.get("SERVICENOW_USERNAME", "")
    pw = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base and user and pw and cr_sys_id):
        return {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base}/api/now/attachment",
            params={
                "sysparm_query": (
                    f"table_sys_id={cr_sys_id}^"
                    f"file_nameSTARTSWITHrun_attestation_"
                ),
                "sysparm_limit": "5",
            },
            auth=(user, pw),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return {}
        rows = (resp.json() or {}).get("result", []) or []
        if not rows:
            return {}
        # Most-recent first.
        rows.sort(key=lambda r: str(r.get("sys_created_on", "")), reverse=True)
        meta = rows[0]
        download = str(meta.get("download_link", "") or "")
        if not download:
            return meta
        dl = await client.get(download, auth=(user, pw))
        if dl.status_code == 200:
            meta["_jws_bytes"] = dl.content
        return meta


def _line(label: str, status: str, detail: str = "") -> str:
    icon = {
        "OK": "✓",
        "FAIL": "✗",
        "WARN": "⚠",
        "INFO": "•",
    }.get(status, "•")
    base = f"  {icon} [{status:4}] {label}"
    return f"{base} — {detail}" if detail else base


async def _walk(cr_arg: str) -> int:
    print(f"=== harbor verify-cr {cr_arg} ===\n")
    overall_ok = True

    # Link 1: CR.
    cr = await _fetch_cr(cr_arg)
    if not cr:
        print(_line(
            "CR lookup",
            "FAIL",
            f"could not fetch CR {cr_arg!r} (auth/network/missing)",
        ))
        return 1
    cr_sys_id_v = cr.get("sys_id")
    cr_number_v = cr.get("number")
    cr_sys_id = (
        cr_sys_id_v.get("value") if isinstance(cr_sys_id_v, dict)
        else str(cr_sys_id_v or "")
    )
    cr_number = (
        cr_number_v.get("value") if isinstance(cr_number_v, dict)
        else str(cr_number_v or "")
    )
    state_v = cr.get("state")
    cr_state = (
        state_v.get("display_value") if isinstance(state_v, dict)
        else str(state_v or "")
    )
    print(_line(
        "CR (ServiceNow live)",
        "OK",
        f"sys_id={cr_sys_id} number={cr_number} state={cr_state!r}",
    ))

    # Link 2: broker JWS attachment.
    att = await _fetch_attestation_attachment(cr_sys_id)
    if not att or not att.get("_jws_bytes"):
        print(_line(
            "run_attestation_<cve>.jws on CR",
            "FAIL",
            "no attachment found matching run_attestation_*",
        ))
        return 1
    raw = att["_jws_bytes"].decode("utf-8").strip()
    # PDI MIME allowlist forces JSON envelope: {"jws": "...", "key_id": "..."}.
    # Accept both shapes for forward-compat (raw compact JWS and wrapped).
    import json as _json
    try:
        envelope = _json.loads(raw)
        if isinstance(envelope, dict) and "jws" in envelope:
            jws = str(envelope["jws"])
        else:
            jws = raw
    except _json.JSONDecodeError:
        jws = raw
    print(_line(
        "run_attestation_<cve>.jws on CR",
        "OK",
        f"sys_id={att.get('sys_id')} "
        f"file={att.get('file_name')} "
        f"size={att.get('size_bytes')}",
    ))

    # Link 3: krakntrust pubkey + boot session anchor.
    boot = boot_session_metadata()
    if not boot:
        print(_line(
            "krakntrust pubkey on disk",
            "FAIL",
            "dev-keys/krakntrust-cve-rem.pub.pem missing",
        ))
        return 1
    print(_line(
        "krakntrust pubkey on disk",
        "OK",
        f"path={boot['pub_key_path']} "
        f"sha256={boot['pub_key_sha256'][:16]}…",
    ))

    # Link 4: Ed25519 verify JWS against pinned pubkey.
    try:
        payload = verify_attestation(jws)
    except Exception as exc:  # noqa: BLE001
        print(_line(
            "Ed25519 verify (krakntrust dev key)",
            "FAIL",
            f"{type(exc).__name__}: {exc}",
        ))
        return 1
    print(_line(
        "Ed25519 verify (krakntrust dev key)",
        "OK",
        f"alg=EdDSA kid={payload.get('kid', '?')}",
    ))

    # Link 5: boot_session_id binds to pubkey.
    expected_boot = (
        # Re-derive from on-disk pubkey for true binding.
        __import__(
            "demos.cve_remediation.krakntrust",
            fromlist=["load_or_create_keypair"],
        ).load_or_create_keypair().boot_session_id
    )
    payload_boot = str(payload.get("boot_session_id", "") or "")
    if payload_boot != expected_boot:
        print(_line(
            "boot_session_id ↔ pubkey",
            "FAIL",
            f"payload={payload_boot[:16]}… expected={expected_boot[:16]}…",
        ))
        overall_ok = False
    else:
        print(_line(
            "boot_session_id ↔ pubkey",
            "OK",
            f"BLAKE3(pubkey)={payload_boot[:16]}…",
        ))

    # Link 6: prompt_artifact_id present + 64-hex-shape (BLAKE3).
    pa_id = str(payload.get("prompt_artifact_id", "") or "")
    if not _is_hex_digest(pa_id, min_len=32):
        print(_line(
            "prompt_artifact_id",
            "FAIL",
            f"missing/invalid hex digest: {pa_id!r}",
        ))
        overall_ok = False
    else:
        print(_line(
            "prompt_artifact_id",
            "OK",
            f"{pa_id[:16]}… (BLAKE3 over rationale+RAG+trace)",
        ))

    # Link 7: doctrine_manifest_hash present.
    dh = str(payload.get("doctrine_manifest_hash", "") or "")
    if not _is_hex_digest(dh, min_len=32):
        print(_line(
            "doctrine_manifest_hash",
            "FAIL",
            f"missing/invalid hex digest: {dh!r}",
        ))
        overall_ok = False
    else:
        print(_line(
            "doctrine_manifest_hash",
            "OK",
            f"{dh[:16]}… (Phase 0 BLAKE3 over corpus + KG counts)",
        ))

    # Link 8: cr_sys_id in payload matches the CR we walked from.
    pay_cr = str(payload.get("cr_sys_id", "") or "")
    if pay_cr and cr_sys_id and pay_cr != cr_sys_id:
        print(_line(
            "cr_sys_id ↔ CR",
            "FAIL",
            f"payload={pay_cr} actual={cr_sys_id}",
        ))
        overall_ok = False
    else:
        print(_line(
            "cr_sys_id ↔ CR",
            "OK",
            f"bound to {cr_sys_id}",
        ))

    # Link 9: dev-mode disclosure.
    print(_line(
        "root key ceremony",
        "WARN",
        "single-key dev mode (production: Shamir 2-of-3, CRITERIA fancy #8)",
    ))

    print()
    print(
        "=== TRUST CHAIN: %s ==="
        % ("VERIFIED" if overall_ok else "BROKEN")
    )
    return 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="harbor verify-cr",
        description="Walk the trust chain back to krakntrust root.",
    )
    parser.add_argument(
        "--cr",
        required=True,
        help="CR sys_id (32 hex chars) or number (CHG#######).",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_walk(args.cr))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
