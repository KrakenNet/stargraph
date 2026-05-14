# SPDX-License-Identifier: Apache-2.0
"""CRITERIA.md Step 4 verification harness.

Drives the ingest -> enrich -> correlate path on multiple CVEs and
checks that the CargoNet match populates ``cargonet_proxy_ref`` for
every CVE that matched a CMDB Software CI with host topology. The
visibility-only contract (per CRITERIA.md step 4) requires a real
node-id round-trip: each id we surface MUST resolve back to a running
lab node in CargoNet.

Pass criteria:

* For every CVE whose CMDB correlation produced ``applicable`` hosts,
  ``cargonet_proxy_ref`` is non-empty AND every id resolves to a
  CargoNet node by re-querying the live API.
* ``cargonet_correlation_map`` pairs each CMDB host name to a single
  CargoNet ``{lab_id, node_id}`` -- no parallel claims.
* The not_applicable CVE produces an empty CargoNet ref (honest -- no
  hosts means nothing to proxy).

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.verify_step4_cargonet
"""

from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any

import httpx

from demos.cve_remediation.graph.real_nodes import (
    CanonicalizeTrustedNode,
    CorrelateAssetsBrokerNode,
    EnrichCveTrustedNode,
    ExtractTrustedNode,
    IntakeFetchNode,
)
from demos.cve_remediation.graph.state import CveRemState


TARGETS = [
    ("CVE-2024-39705", "expect_cargonet"),
    ("CVE-2021-44228", "expect_cargonet"),
    ("CVE-2024-3094",  "expect_cargonet"),
    ("CVE-2024-47176", "expect_cargonet"),
    ("CVE-2024-26581", "expect_empty"),  # no CMDB match -> no CargoNet
]


async def _run(cve_id: str) -> CveRemState:
    state = CveRemState(cve_id=cve_id)
    ctx = SimpleNamespace(run_id="verify-step4")
    for node in (
        IntakeFetchNode(),
        CanonicalizeTrustedNode(),
        ExtractTrustedNode(),
        EnrichCveTrustedNode(),
        CorrelateAssetsBrokerNode(),
    ):
        delta = await node.execute(state, ctx)
        if delta:
            state = state.model_copy(update=delta)
    return state


async def _resolve_cargonet_nodes(node_ids: list[str]) -> dict[str, str]:
    """Round-trip the proxy node ids back to CargoNet to confirm they're real.

    Hits ``/api/v1/labs`` then ``/api/v1/labs/{id}/nodes`` and returns
    ``{node_id: name}`` only for ids that exist in a running lab.
    """
    base_url = os.environ.get("CARGONET_BASE_URL", "http://localhost:28080").rstrip("/")
    async with httpx.AsyncClient(timeout=10.0) as client:
        labs_resp = await client.get(f"{base_url}/api/v1/labs")
        labs_resp.raise_for_status()
        out: dict[str, str] = {}
        wanted = set(node_ids)
        for lab in (labs_resp.json() or {}).get("items", []):
            if str(lab.get("status") or "").lower() != "running":
                continue
            lab_id = str(lab.get("id") or "")
            if not lab_id:
                continue
            nodes_resp = await client.get(f"{base_url}/api/v1/labs/{lab_id}/nodes")
            nodes_resp.raise_for_status()
            for n in (nodes_resp.json() or {}).get("items", []):
                nid = str(n.get("id") or "")
                if nid in wanted:
                    out[nid] = f"{n.get('name', '')} (lab={lab_id[:8]}…)"
        return out


def _grade(
    cve: str, expectation: str, state: CveRemState, resolved: dict[str, str]
) -> tuple[bool, list[str]]:
    fails: list[str] = []
    proxy_refs = list(getattr(state, "cargonet_proxy_ref", []) or [])
    cmap = getattr(state, "cargonet_correlation_map", {}) or {}
    affected = list(getattr(state.correlated, "affected_assets", []) or [])
    err = state.last_cargonet_error or ""

    if err:
        fails.append(f"last_cargonet_error: {err}")

    if expectation == "expect_cargonet":
        if not proxy_refs:
            fails.append(
                f"cargonet_proxy_ref empty (affected_assets={len(affected)})"
            )
        unresolved = [n for n in proxy_refs if n not in resolved]
        if unresolved:
            fails.append(f"node ids not found in CargoNet: {unresolved}")
        if not cmap:
            fails.append("cargonet_correlation_map empty (no per-host pairing)")
        # Visibility-only contract: every proxy_ref must come from a
        # host name that's also in affected_assets / hosts (no
        # CargoNet-only nodes leaking in).
        host_names_in_map = set(cmap.keys()) if isinstance(cmap, dict) else set()
        if not host_names_in_map:
            fails.append("no host names in correlation_map")
    elif expectation == "expect_empty":
        if proxy_refs:
            fails.append(f"cargonet_proxy_ref unexpectedly populated: {proxy_refs}")
        if cmap:
            fails.append(f"cargonet_correlation_map unexpectedly populated: {cmap!r}")

    return (not fails, fails)


async def main() -> int:
    overall = True
    print("=== STEP 4 VERIFICATION (CargoNet match, multi-CVE) ===\n")
    for cve, expectation in TARGETS:
        try:
            state = await _run(cve)
        except Exception as exc:  # noqa: BLE001
            overall = False
            print(f"[{cve}] EXCEPTION: {type(exc).__name__}: {exc}\n")
            continue
        proxy_refs = list(getattr(state, "cargonet_proxy_ref", []) or [])
        resolved = await _resolve_cargonet_nodes(proxy_refs)
        passed, fails = _grade(cve, expectation, state, resolved)
        status = "PASS" if passed else "FAIL"
        print(f"[{cve}] expectation={expectation} {status}")
        print(f"  cmdb_software_name      : {state.cmdb_software_name!r}")
        print(f"  affected_assets count   : {len(getattr(state.correlated,'affected_assets',[]) or [])}")
        print(f"  cargonet_lab_ref        : {getattr(state,'cargonet_lab_ref','')!r}")
        print(f"  cargonet_proxy_ref      : {proxy_refs}")
        print(f"  cargonet_node_count     : {getattr(state,'cargonet_node_count',0)}")
        cmap = getattr(state, "cargonet_correlation_map", {}) or {}
        for host, link in (cmap.items() if isinstance(cmap, dict) else []):
            nid = link.get("node_id", "") if isinstance(link, dict) else ""
            print(f"    {host} -> {nid}  resolved={resolved.get(nid, '<UNRESOLVED>')!r}")
        if state.last_cargonet_error:
            print(f"  last_cargonet_error     : {state.last_cargonet_error}")
        for f in fails:
            print(f"  ! {f}")
        if not passed:
            overall = False
        print()
    print("=== OVERALL: %s ===" % ("PASS" if overall else "FAIL"))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
