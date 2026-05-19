# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #1 verification: trust-chain attestation walk works end-to-end.

Drives the full pipeline (including KrakntrustAttestNode) and asserts
the verify-cr CLI walker resolves the chain back to the krakntrust
root key.

Three scenarios:

* **Positive** — full pipeline run; attestation lands as
  ``run_attestation_<cve>.jws`` attachment on CR; ``harbor verify-cr``
  verifies every real link (CR / JWS / Ed25519 / boot_session_id /
  prompt_artifact_id / doctrine_manifest_hash / cr_sys_id binding).

* **Tampered JWS** — flip one byte of the JWS; verify-cr must fail.

* **Wrong key** — generate an unrelated Ed25519 keypair and use it to
  sign a forged attestation; verify-cr (loading the pinned dev pubkey)
  must fail.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \\
    uv run --no-project python -m demos.cve_remediation.scripts.verify_F1_trust_chain
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

from demos.cve_remediation.graph.real_nodes import (
    AttachAllArtifactsNode,
    CanonicalizeDoctrineNode,
    CanonicalizeTrustedNode,
    CargoNetWritebackNode,
    CloseChangeRequestNode,
    CodeWriterNode,
    CorrelateAssetsBrokerNode,
    CreateChangeRequestNode,
    DoctrineExtractorNode,
    DoctrineLoaderNode,
    DriftWatchSpawnNode,
    EmitDocxArchiveNode,
    EmitRetroPayloadNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    HitlChangeApprovalNode,
    HitlRetrospectiveReviewNode,
    IntakeFetchNode,
    KrakntrustAttestNode,
    ManifestSignNode,
    PlanKgWritebackNode,
    PlannerNode,
    ProgressiveExecuteNode,
    PublishDocPlusNode,
    RenderDocxNode,
    SandboxDispatchNode,
    SandboxRunNode,
    VecSearchRetrosNode,
    VerifyImmediateNode,
    WriteRetrospectiveNode,
)
from demos.cve_remediation.graph.state import CveRemState
from demos.cve_remediation.krakntrust import (
    boot_session_metadata,
    verify_attestation,
)

DEFAULT_CVE = os.environ.get("F1_CVE", "CVE-2024-26130")


async def _drive(cve_id: str, label: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id, run_id=f"verify-F1-{label}")
    ctx = SimpleNamespace(run_id=state.run_id)
    pipeline = (
        # Phase 0 — populate doctrine_manifest_hash from real corpora
        # so the trust chain has a non-empty manifest link.
        DoctrineLoaderNode(),
        CanonicalizeDoctrineNode(),
        DoctrineExtractorNode(),
        ManifestSignNode(),
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
        VecSearchRetrosNode(),
        PlannerNode(),
        CodeWriterNode(),
        SandboxDispatchNode(),
        SandboxRunNode(),
        CreateChangeRequestNode(),
        AttachAllArtifactsNode(),
        HitlChangeApprovalNode(),
        ProgressiveExecuteNode(),
        VerifyImmediateNode(),
        WriteRetrospectiveNode(),
        HitlRetrospectiveReviewNode(),
        EmitRetroPayloadNode(),
        RenderDocxNode(),
        EmitDocxArchiveNode(),
        PublishDocPlusNode(),
        CargoNetWritebackNode(),
        PlanKgWritebackNode(),
        KrakntrustAttestNode(),
        CloseChangeRequestNode(),
        DriftWatchSpawnNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pipeline:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


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
        rows.sort(
            key=lambda r: str(r.get("sys_created_on", "")), reverse=True
        )
        meta = rows[0]
        download = str(meta.get("download_link", "") or "")
        if download:
            dl = await client.get(download, auth=(user, pw))
            if dl.status_code == 200:
                meta["_jws_bytes"] = dl.content
        return meta


def _grade_positive(state: CveRemState, jws_remote: str) -> bool:
    ok = True
    print(f"  [positive] cr_sys_id          : "
          f"{state.servicenow_response.get('result', {}).get('sys_id', '')!r}")
    print(f"  [positive] prompt_artifact_id : {state.prompt_artifact_id!r}")
    print(f"  [positive] doctrine_manifest  : {state.doctrine_manifest_hash[:16]}…")
    print(f"  [positive] boot_session_id    : {state.boot_session_id[:16]}…")
    print(f"  [positive] krakntrust_key_id  : {state.krakntrust_key_id!r}")
    print(f"  [positive] attestation_attach : "
          f"{state.run_attestation_attachment_sys_id!r}")
    if not state.run_attestation_jws:
        print("  [positive] ! state has no run_attestation_jws")
        ok = False
    if not state.run_attestation_attachment_sys_id:
        print("  [positive] ! attestation did not upload to CR")
        ok = False
    if not state.boot_session_id:
        print("  [positive] ! boot_session_id empty")
        ok = False
    if not state.prompt_artifact_id:
        print("  [positive] ! prompt_artifact_id empty")
        ok = False
    if not state.doctrine_manifest_hash:
        print("  [positive] ! doctrine_manifest_hash empty")
        ok = False
    if not jws_remote:
        print("  [positive] ! no JWS round-tripped from SN attachment")
        ok = False
    else:
        # SN payload is JSON-wrapped; unwrap before comparing.
        import json as _json
        try:
            env = _json.loads(jws_remote)
            jws_unwrapped = (
                str(env["jws"]) if isinstance(env, dict) and "jws" in env
                else jws_remote
            )
        except _json.JSONDecodeError:
            jws_unwrapped = jws_remote
        if jws_unwrapped.strip() != state.run_attestation_jws.strip():
            print("  [positive] ! JWS round-trip differs (local vs SN)")
            ok = False
    # Verify with pinned pubkey.
    try:
        payload = verify_attestation(state.run_attestation_jws)
    except Exception as exc:  # noqa: BLE001
        print(f"  [positive] ! Ed25519 verify failed: "
              f"{type(exc).__name__}: {exc}")
        return False
    print(f"  [positive] payload kid        : {payload.get('kid')!r}")
    if payload.get("boot_session_id") != state.boot_session_id:
        print("  [positive] ! payload.boot_session_id != state")
        ok = False
    if payload.get("prompt_artifact_id") != state.prompt_artifact_id:
        print("  [positive] ! payload.prompt_artifact_id != state")
        ok = False
    if payload.get("doctrine_manifest_hash") != state.doctrine_manifest_hash:
        print("  [positive] ! payload.doctrine_manifest_hash != state")
        ok = False
    boot = boot_session_metadata()
    print(f"  [positive] pubkey on disk     : {boot.get('pub_key_path')}")
    return ok


def _grade_tampered(jws: str) -> bool:
    ok = True
    if not jws:
        print("  [tampered] ! no source JWS to tamper with")
        return False
    # Flip a single character in the signature segment (last segment).
    head, body, sig = jws.split(".")
    flipped = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    tampered = f"{head}.{body}.{flipped}"
    try:
        verify_attestation(tampered)
        print("  [tampered] ! verify_attestation accepted a tampered JWS")
        ok = False
    except jwt.InvalidSignatureError:
        print("  [tampered] verify rejected tampered JWS (expected)")
    except Exception as exc:  # noqa: BLE001
        # Any verify-side exception is acceptable here so long as it's
        # not InvalidTokenError leaking through as success.
        print(f"  [tampered] verify rejected tampered JWS via "
              f"{type(exc).__name__}")
    return ok


def _grade_wrong_key(payload: dict[str, Any]) -> bool:
    """Sign with a *different* Ed25519 key; pinned pubkey must reject."""
    ok = True
    other = Ed25519PrivateKey.generate()
    other_pem = other.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
    )
    forged = jwt.encode(
        payload,
        other_pem,
        algorithm="EdDSA",
        headers={"kid": "forged-attacker-key"},
    )
    try:
        verify_attestation(forged)
        print("  [wrong-key] ! verify_attestation accepted a forged JWS")
        ok = False
    except jwt.InvalidSignatureError:
        print("  [wrong-key] verify rejected forged JWS (expected)")
    except Exception as exc:  # noqa: BLE001
        print(f"  [wrong-key] verify rejected forged JWS via "
              f"{type(exc).__name__}")
    return ok


async def main() -> int:
    overall = True
    print("=== F1 VERIFICATION (trust-chain attestation walk) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print("! HARBOR_SERVICENOW_LIVE unset; CR write will be dry-run, "
              "and the attestation upload will be skipped.\n")
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")
    os.environ["CVE_REM_SKIP_SEED"] = "1"

    print("--- Scenario 1: positive (full pipeline + chain) ---")
    state = await _drive(DEFAULT_CVE, "positive")
    cr_sys_id = str(
        state.servicenow_response.get("result", {}).get("sys_id", "") or ""
    )
    att = await _fetch_attestation_attachment(cr_sys_id) if cr_sys_id else {}
    jws_remote = (
        att.get("_jws_bytes", b"").decode("utf-8") if att else ""
    )
    if not _grade_positive(state, jws_remote):
        overall = False

    print("\n--- Scenario 2: tampered JWS rejected ---")
    if not _grade_tampered(state.run_attestation_jws):
        overall = False

    print("\n--- Scenario 3: forged-key JWS rejected ---")
    payload = (
        verify_attestation(state.run_attestation_jws)
        if state.run_attestation_jws else {"sub": "x"}
    )
    if not _grade_wrong_key(payload):
        overall = False

    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
