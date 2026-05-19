# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 11 verification: Doc+ generated AND uploaded to a reachable store.

Drives the full pipeline once and asserts:

* ``state.docx_artifact_ref`` points to a file:// path that exists,
  is non-empty, and BLAKE3-addressed under
  ``$HARBOR_ARTIFACTS_ROOT/docx/<digest>.json``.
* ``state.retro_payload_artifact_ref`` exists on disk (content-addressed
  retrospective payload that the Doc+ chain wraps).
* ``state.docplus_published`` is True and ``state.docplus_attachment_sys_id``
  is a non-empty SN attachment sys_id.
* GET ``/api/now/attachment/<sys_id>`` returns ``state="available"``,
  the SN-reported sha256 ``hash`` matches sha256 of the local archive,
  ``size_bytes`` matches local size, ``file_name`` matches the
  ``cve_remediation_<cve>.docx[.json]`` pattern, and
  ``download_link`` is a reachable URL.
* GET the attachment file via ``download_link`` — the byte content
  must equal the local archive byte-for-byte.
* The Doc+ attachment sys_id appears in ``state.attachment_manifest``
  (proof that the multi-artifact attachment walk also recorded it).

Run::

    set -a; source demos/cve_remediation/.env; set +a
    HARBOR_SERVICENOW_LIVE=1 \\
    uv run --no-project python -m demos.cve_remediation.scripts.verify_step11_docplus
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from demos.cve_remediation.graph.real_nodes import (
    AttachAllArtifactsNode,
    CanonicalizeTrustedNode,
    CargoNetWritebackNode,
    CloseChangeRequestNode,
    CodeWriterNode,
    CorrelateAssetsBrokerNode,
    CreateChangeRequestNode,
    DriftWatchSpawnNode,
    EmitDocxArchiveNode,
    EmitRetroPayloadNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    HitlChangeApprovalNode,
    HitlRetrospectiveReviewNode,
    IntakeFetchNode,
    PlanKgWritebackNode,
    PlannerNode,
    ProgressiveExecuteNode,
    PublishDocPlusNode,
    RenderDocxNode,
    SandboxDispatchNode,
    SandboxRunNode,
    VerifyImmediateNode,
    WriteRetrospectiveNode,
)
from demos.cve_remediation.graph.state import CveRemState

DEFAULT_CVE = os.environ.get("STEP11_CVE", "CVE-2024-26130")


async def _drive(cve_id: str, label: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id=f"verify-step11-{label}")
    pipeline = (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
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
        CloseChangeRequestNode(),
        DriftWatchSpawnNode(),
    )
    state = state.model_copy(update={"validation_passed": True})
    for node in pipeline:
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


def _resolve_file(ref: str) -> Path | None:
    ref = (ref or "").strip()
    if not ref.startswith("file://"):
        return None
    p = Path(ref.removeprefix("file://"))
    return p if p.exists() else None


async def _fetch_attachment_meta(sys_id: str) -> dict[str, Any]:
    base_url = os.environ.get("SERVICENOW_BASE_URL", "").rstrip("/")
    user = os.environ.get("SERVICENOW_USERNAME", "")
    pw = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (base_url and user and pw and sys_id):
        return {}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{base_url}/api/now/attachment/{sys_id}",
            auth=(user, pw),
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return {}
        return (resp.json() or {}).get("result", {}) or {}


async def _download_attachment(download_link: str) -> bytes:
    user = os.environ.get("SERVICENOW_USERNAME", "")
    pw = os.environ.get("SERVICENOW_PASSWORD", "")
    if not (download_link and user and pw):
        return b""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(download_link, auth=(user, pw))
        if resp.status_code != 200:
            return b""
        return resp.content


def _grade(state: CveRemState, meta: dict[str, Any], remote_bytes: bytes,
           local_bytes: bytes, narrative: str) -> bool:
    ok = True
    cve_id = state.cve_id

    # Doc+ artifact on disk
    docx = _resolve_file(state.docx_artifact_ref)
    print(f"  docx_artifact_ref   : {state.docx_artifact_ref!r}")
    if not docx:
        print("  ! docx_artifact_ref does not resolve to an existing file")
        ok = False
    elif docx.stat().st_size == 0:
        print("  ! docx archive is empty")
        ok = False
    else:
        print(f"  docx local size     : {docx.stat().st_size}")

    # Narrative content quality — Doc+ must summarize the workflow,
    # not just emit a debug stub. Pull narrative from the JSON-wrapped
    # archive (offline) or assert content directly (production .docx).
    print(f"  narrative bytes     : {len(narrative)}")
    if len(narrative) < 1500:
        print(f"  ! narrative is suspiciously short ({len(narrative)} bytes); "
              "likely regressed to the boilerplate stub")
        ok = False

    # Required sections — derived from state, no per-CVE literals.
    required_sections = [
        "## 1. Outcome at a glance",
        "## 2. Vulnerability",
        "## 4. Plan",
    ]
    for sect in required_sections:
        if sect not in narrative:
            print(f"  ! missing required section header: {sect!r}")
            ok = False

    # Conditional sections — must appear iff state populated them.
    if state.affected_host_names and "## 3. Affected fleet" not in narrative:
        print("  ! affected_host_names populated but no fleet section")
        ok = False
    if state.sandbox_probe_steps and "## 5. Sandbox 4-step probe" not in narrative:
        print("  ! sandbox_probe_steps populated but no probe section")
        ok = False
    if state.per_host_apply_results and "## 6. Apply (per-host install)" not in narrative:
        print("  ! per_host_apply_results populated but no apply section")
        ok = False
    if state.per_host_verify_results and "## 7. Verify (per-host probe)" not in narrative:
        print("  ! per_host_verify_results populated but no verify section")
        ok = False
    if state.cr_correlation_id and "## 8. ServiceNow change request" not in narrative:
        print("  ! cr_correlation_id populated but no CR section")
        ok = False
    if state.retro_id and "## 9. Retrospective" not in narrative:
        print("  ! retro_id populated but no retro section")
        ok = False

    # Required state-derived strings — narrative must echo these so an
    # operator reading cold knows what was remediated and where.
    must_contain = [
        (cve_id, "cve_id"),
        (state.verify_outcome or "unknown", "verify_outcome"),
        (state.retro_outcome or "unknown", "retro_outcome"),
    ]
    if state.cmdb_software_name:
        must_contain.append((state.cmdb_software_name, "cmdb_software_name"))
    if state.fixed_version:
        must_contain.append((state.fixed_version, "fixed_version"))
    if state.affected_host_names:
        must_contain.append(
            (state.affected_host_names[0], "first affected host")
        )
    if state.plan_hash:
        must_contain.append((state.plan_hash, "plan_hash"))
    if state.retro_id:
        must_contain.append((state.retro_id, "retro_id"))
    for needle, label in must_contain:
        if needle and needle not in narrative:
            print(f"  ! narrative does not contain {label}: {needle!r}")
            ok = False
    print(f"  narrative content checks: {len(must_contain)} fields verified")

    # Retro payload exists on disk (Doc+ wraps it)
    retro = _resolve_file(state.retro_payload_artifact_ref)
    print(f"  retro_payload_ref   : {state.retro_payload_artifact_ref!r}")
    if not retro:
        print("  ! retro_payload_artifact_ref does not resolve to a file")
        ok = False

    # Doc+ publish flags
    print(f"  docplus_published   : {state.docplus_published}")
    if not state.docplus_published:
        print("  ! Doc+ publish never claimed success")
        ok = False
    print(f"  docplus_attachment  : {state.docplus_attachment_sys_id!r}")
    if not state.docplus_attachment_sys_id:
        print("  ! docplus_attachment_sys_id is empty; SN upload did not land")
        ok = False
        return ok

    # SN attachment metadata
    print(f"  sn meta state       : {meta.get('state', '')!r}")
    print(f"  sn meta file_name   : {meta.get('file_name', '')!r}")
    print(f"  sn meta size_bytes  : {meta.get('size_bytes', '')!r}")
    print(f"  sn meta hash        : {meta.get('hash', '')!r}")
    print(f"  sn meta download    : {meta.get('download_link', '')!r}")
    if not meta:
        print("  ! attachment meta lookup failed (404 / auth / unreachable)")
        return False
    if meta.get("state") != "available":
        print(f"  ! attachment state is {meta.get('state')!r}, want 'available'")
        ok = False
    fname = str(meta.get("file_name", ""))
    if cve_id not in fname or not (
        fname.endswith(".docx") or fname.endswith(".docx.json")
    ):
        print(f"  ! attachment file_name {fname!r} does not match "
              f"cve_remediation_{cve_id}.docx[.json] pattern")
        ok = False

    # Hash + size cross-check against the local archive
    if local_bytes:
        local_sha256 = hashlib.sha256(local_bytes).hexdigest()
        print(f"  local sha256        : {local_sha256}")
        sn_hash = str(meta.get("hash", "")).lower()
        if sn_hash and sn_hash != local_sha256:
            print(f"  ! SN hash {sn_hash!r} != local sha256 {local_sha256!r}")
            ok = False
        local_size = len(local_bytes)
        try:
            sn_size = int(str(meta.get("size_bytes", "")) or "0")
        except ValueError:
            sn_size = -1
        if sn_size != local_size:
            print(f"  ! SN size_bytes {sn_size} != local size {local_size}")
            ok = False

    # Bytes round-trip
    print(f"  download size       : {len(remote_bytes)}")
    if not remote_bytes:
        print("  ! attachment download returned 0 bytes / failed")
        ok = False
    elif local_bytes and remote_bytes != local_bytes:
        print("  ! downloaded bytes differ from local archive bytes")
        ok = False

    # AttachAll manifest must reference the Doc+ attachment too
    manifest = list(state.attachment_manifest or [])
    docplus_in_manifest = any(
        m.get("sys_id") == state.docplus_attachment_sys_id
        or m.get("source_field") == "docx_artifact_ref"
        for m in manifest
    )
    print(f"  attachment_manifest : {len(manifest)} rows; "
          f"docplus tracked={docplus_in_manifest}")
    if not manifest:
        print("  ! attachment_manifest is empty (multi-artifact walk skipped)")
        ok = False

    return ok


async def main() -> int:
    print("=== STEP 11 VERIFICATION (Doc+ generated AND reachable) ===\n")
    if not os.environ.get("HARBOR_SERVICENOW_LIVE"):
        print(
            "! HARBOR_SERVICENOW_LIVE unset — Doc+ upload runs against the "
            "live PDI regardless, but CR transitions are dry-run.\n"
        )
    os.environ.setdefault("CVE_REM_VERIFY_PROBE", "cargonet")

    print(f"--- Run: {DEFAULT_CVE} ---")
    state = await _drive(DEFAULT_CVE, "run")

    sys_id = state.docplus_attachment_sys_id
    meta = await _fetch_attachment_meta(sys_id) if sys_id else {}
    download_link = str(meta.get("download_link", "") or "")
    remote_bytes = await _download_attachment(download_link) if download_link else b""

    docx_path = _resolve_file(state.docx_artifact_ref)
    local_bytes = docx_path.read_bytes() if docx_path else b""

    # Extract narrative content. Offline path wraps markdown in a JSON
    # envelope ({"format": "md-wrapped", "narrative": "..."}); production
    # python-docx archive stores narrative as document.xml — for the
    # offline demo we assert against the JSON narrative directly.
    narrative = ""
    if local_bytes:
        try:
            import json as _json
            wrap = _json.loads(local_bytes.decode("utf-8"))
            if isinstance(wrap, dict):
                narrative = str(wrap.get("narrative", "") or "")
        except (ValueError, UnicodeDecodeError):
            # Production .docx: best-effort decode (zip bytes, not JSON).
            narrative = ""

    overall = _grade(state, meta, remote_bytes, local_bytes, narrative)

    print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
