# SPDX-License-Identifier: Apache-2.0
"""Seed ServiceNow PDI CMDB with h11 hosts + CVE-bound software relationships.

Reads ``fixtures/h11_cmdb_seed.json`` (output of ``build_h11_scenario``)
and writes:

1. **30 shared h11-* hosts** as ``cmdb_ci_unix_server`` (will appear in
   live CargoNet topology).
2. **5 CMDB-only hosts** as ``cmdb_ci_unix_server`` with
   ``operational_status=retired`` (decommissioned-but-tracked drift).
3. **70 CVE-bound software CIs** on ``cmdb_ci_spkg`` named
   ``<vendor> <product>`` per CVE; ``Runs on::Runs`` relationships from
   each software CI to every host the ground truth lists for that CVE.

Topology-only (shadow IT) hosts are intentionally NOT added to CMDB --
they exercise the workflow's "in topology, missing in CMDB" drift
branch.

Idempotent: ``get_or_create`` matches by name+class first, only POSTs
on miss. Safe to re-run.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.seed_h11_cmdb
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_CMDB_SEED = _FIXTURES / "h11_cmdb_seed.json"
_TRUTH_PATH = _FIXTURES / "scoring_ground_truth.json"

RUNS_ON_REL_TYPE_SYS_ID = "60bc4e22c0a8010e01f074cbe6bd73c3"


def _env(key, required=True):
    v = os.environ.get(key, "").strip()
    if required and not v:
        print(f"ERROR: {key} unset", file=sys.stderr)
        sys.exit(2)
    return v


def _client():
    return httpx.Client(
        base_url=_env("SERVICENOW_BASE_URL").rstrip("/"),
        auth=(_env("SERVICENOW_USERNAME"), _env("SERVICENOW_PASSWORD")),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )


def _get_or_create(client, table, *, query, body, label):
    resp = client.get(
        f"/api/now/table/{table}",
        params={"sysparm_query": query, "sysparm_limit": "1", "sysparm_fields": "sys_id"},
    )
    resp.raise_for_status()
    rows = resp.json().get("result") or []
    if rows:
        sys_id = str(rows[0]["sys_id"])
        return sys_id, "exists"
    resp = client.post(f"/api/now/table/{table}", json=body)
    resp.raise_for_status()
    return str(resp.json()["result"]["sys_id"]), "created"


def _reclassify(client, sys_id, host_name, target_class):
    resp = client.get(
        f"/api/now/table/cmdb_ci/{sys_id}",
        params={"sysparm_fields": "sys_class_name"},
    )
    resp.raise_for_status()
    current = (resp.json().get("result") or {}).get("sys_class_name") or ""
    if current == target_class:
        return False
    resp = client.patch(
        f"/api/now/table/cmdb_ci/{sys_id}",
        json={"sys_class_name": target_class, "os": "Linux"},
    )
    resp.raise_for_status()
    return True


def _seed_hosts(client, cis):
    name_to_sys_id = {}
    created = retired_marked = 0
    for ci in cis:
        name = ci["name"]
        sys_id, mode = _get_or_create(
            client, "cmdb_ci_unix_server",
            query=f"name={name}",
            body={
                "name": name, "os": "Linux",
                "operational_status": "5" if ci.get("decommissioned") else "1",
                "short_description": (
                    f"h11 site host (role={ci['role']}, "
                    f"{'decommissioned' if ci.get('decommissioned') else 'active'})"
                ),
                "u_site": "h11",
            },
            label=name,
        )
        if mode == "created":
            created += 1
        was_reclassed = _reclassify(client, sys_id, name, "cmdb_ci_unix_server")
        # Mark decommissioned hosts retired explicitly (PATCH may have lost it).
        if ci.get("decommissioned"):
            client.patch(
                f"/api/now/table/cmdb_ci_unix_server/{sys_id}",
                json={"operational_status": "5"},
            ).raise_for_status()
            retired_marked += 1
        name_to_sys_id[name] = sys_id
        flag = "retired" if ci.get("decommissioned") else "active"
        rc = " reclassed" if was_reclassed else ""
        print(f"  [{mode:7}] {name:25} {flag:8} {sys_id}{rc}")
    return name_to_sys_id, created, retired_marked


def _seed_software_and_rels(client, bindings, host_sys_ids):
    sw_created = sw_existing = rel_created = rel_existing = 0
    for b in bindings:
        sw_name = (f"{b['vendor']} {b['product']}").strip()
        if not sw_name:
            sw_name = b["cve_id"]
        # Strip ServiceNow-query-hostile chars (parens, equals, carets, commas).
        for bad in "()=^,":
            sw_name = sw_name.replace(bad, " ")
        sw_name = " ".join(sw_name.split())[:100]  # collapse spaces, truncate
        sw_sys_id, mode = _get_or_create(
            client, "cmdb_ci_spkg",
            query=f"name={sw_name}",
            body={
                "name": sw_name,
                "short_description": (
                    f"Software CI bound to {b['cve_id']}; "
                    f"vendor={b['vendor']}; product={b['product']}"
                ),
                "u_cve_seed": b["cve_id"],
            },
            label=sw_name,
        )
        if mode == "created":
            sw_created += 1
        else:
            sw_existing += 1
        host_versions = b.get("host_versions") or {}
        for host in b["host_names"]:
            host_sys_id = host_sys_ids.get(host)
            if not host_sys_id:
                continue
            rel_query = (
                f"parent={sw_sys_id}^child={host_sys_id}"
                f"^type={RUNS_ON_REL_TYPE_SYS_ID}"
            )
            rel_body = {
                "parent": sw_sys_id, "child": host_sys_id,
                "type": RUNS_ON_REL_TYPE_SYS_ID,
            }
            install_version = host_versions.get(host) or ""
            if install_version:
                rel_body["u_install_version"] = install_version
            rel_sys_id, rel_mode = _get_or_create(
                client, "cmdb_rel_ci",
                query=rel_query,
                body=rel_body,
                label=f"{sw_name} runs on {host}",
            )
            # Idempotent backfill: when the rel already existed but has
            # no install_version (legacy rows from pre-version-filter
            # seeds), PATCH it so cmdb_traverse_runs_on returns the
            # right attribute. New rels carry the field via POST above.
            if rel_mode == "exists" and install_version:
                try:
                    client.patch(
                        f"/api/now/table/cmdb_rel_ci/{rel_sys_id}",
                        json={"u_install_version": install_version},
                    ).raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    print(f"    ! patch install_version failed {rel_sys_id}: {exc}")
            if rel_mode == "created":
                rel_created += 1
            else:
                rel_existing += 1
        print(f"  [{mode:7}] sw={sw_name[:40]:40} hosts={len(b['host_names'])}")
    return sw_created, sw_existing, rel_created, rel_existing


def main():
    ap = argparse.ArgumentParser(description="seed PDI CMDB for h11 scoring run")
    ap.add_argument("--dry-run", action="store_true",
                    help="list intended ops; don't POST")
    args = ap.parse_args()

    seed = json.loads(_CMDB_SEED.read_text())
    print(f"=== seed_h11_cmdb ===")
    print(f"  CIs        : {len(seed['cis'])}")
    print(f"  bindings   : {len(seed['bindings'])}")
    if args.dry_run:
        print("\n--- DRY RUN ---")
        for ci in seed["cis"]:
            flag = "retired" if ci.get("decommissioned") else "active"
            print(f"  host : {ci['name']:25} {flag}")
        for b in seed["bindings"]:
            print(f"  bind : {b['cve_id']} -> "
                  f"{b['vendor'][:18]:18}/{b['product'][:30]:30}  "
                  f"hosts={len(b['host_names'])}")
        return 0

    client = _client()
    print(f"\n[1] hosts ({len(seed['cis'])})")
    host_sys_ids, created, retired_marked = _seed_hosts(client, seed["cis"])
    print(f"  -> created={created}, retired_marked={retired_marked}")

    print(f"\n[2] software + Runs-on rels ({len(seed['bindings'])})")
    sw_c, sw_e, rel_c, rel_e = _seed_software_and_rels(
        client, seed["bindings"], host_sys_ids,
    )
    print(f"  -> sw created={sw_c}, existing={sw_e}; "
          f"rels created={rel_c}, existing={rel_e}")

    print(f"\n=== summary ===")
    print(f"  CMDB hosts written   : {len(host_sys_ids)}")
    print(f"  software CIs written : {sw_c}")
    print(f"  Runs-on rels written : {rel_c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
