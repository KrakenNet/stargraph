# SPDX-License-Identifier: Apache-2.0
"""Deploy h11 topology + push CVE vulnerability markers via cargonet exec.

Two phases:

* **Phase A: deploy** -- POST ``fixtures/h11_topology.yaml`` to
  ``/api/v1/networks/topologies`` (idempotent: detect existing
  topology by name, skip create), then deploy as a lab. Writes lab
  metadata to ``fixtures/h11_deployment.json`` for downstream scripts.
  Skips this phase if ``--reuse`` is passed and the file already lists
  a running lab.

* **Phase B: push markers** -- For each (cve_id, host) pair in
  ``fixtures/scoring_ground_truth.json``, drop a JSON marker at
  ``/var/cve_marker/<cve_id>`` on the target container. Marker
  contains ``{cve_id, vendor, product, vuln_class, planted_at}`` --
  enough for the workflow's probe step to detect "vulnerable software
  present".

Why markers instead of real vulnerable packages: 35 hosts x 70 CVE
mappings would require per-CVE specialized base images. Markers give
deterministic, idempotent, probe-able state without building 70 vuln
images. The workflow's probe + plan + apply + verify loop uses the
marker as ground truth: planning marks ``rm /var/cve_marker/<cve>``
as the remediation; verification re-probes the path.

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.deploy_h11
    uv run --no-project python -m demos.cve_remediation.scripts.deploy_h11 \
        --reuse --skip-deploy
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shlex
import sys
import time
from pathlib import Path
from datetime import UTC, datetime

import httpx

from harbor.tools.cargonet.exec_node import cargonet_exec, cargonet_list_nodes

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_TOPO_YAML = _FIXTURES / "h11_topology.yaml"
_TRUTH_PATH = _FIXTURES / "scoring_ground_truth.json"
_DEPLOY_META = _FIXTURES / "h11_deployment.json"
_TOPO_NAME = "h11-cve-scoring"

_CARGONET_BASE = os.environ.get(
    "CARGONET_BASE_URL", "http://localhost:28080"
).rstrip("/")
_DEPLOY_TIMEOUT_S = 120


async def _http_get(client, path):
    r = await client.get(f"{_CARGONET_BASE}{path}", timeout=15)
    r.raise_for_status()
    return r.json()


async def _http_post(client, path, *, json_body=None):
    r = await client.post(
        f"{_CARGONET_BASE}{path}", json=json_body, timeout=30,
    )
    r.raise_for_status()
    return r.json()


async def _find_existing_topology(client):
    body = await _http_get(client, "/api/v1/networks/topologies")
    for t in (body or {}).get("items", []):
        if t.get("name") == _TOPO_NAME:
            return t["id"]
    return None


async def _deploy(args):
    """Phase A: ensure topology + lab exist; return deployment meta."""
    if args.reuse and _DEPLOY_META.exists():
        meta = json.loads(_DEPLOY_META.read_text())
        print(f"  reusing deployment from {_DEPLOY_META}")
        return meta

    yaml_text = _TOPO_YAML.read_text(encoding="utf-8")
    async with httpx.AsyncClient() as client:
        topo_id = await _find_existing_topology(client)
        if topo_id:
            print(f"  topology {_TOPO_NAME!r} already exists: {topo_id}")
        else:
            create = await _http_post(
                client, "/api/v1/networks/topologies",
                json_body={"name": _TOPO_NAME, "topology_yaml": yaml_text},
            )
            topo_id = create["id"]
            print(f"  created topology: {topo_id} "
                  f"({create.get('node_count')} nodes, "
                  f"{create.get('link_count')} links)")
        deps_body = await _http_get(client, "/api/v1/networks/deployments")
        deployment = None
        for d in (deps_body or {}).get("items", []):
            if d.get("topology_id") == topo_id and d.get("status") in (
                "deploying", "running",
            ):
                deployment = d
                break
        if deployment is None:
            print(f"  deploying topology {topo_id} ...")
            deployment = await _http_post(
                client,
                f"/api/v1/networks/topologies/{topo_id}/deploy",
            )
        dep_id = deployment["id"]
        print(f"  deployment {dep_id}: {deployment['status']}")
        deadline = time.monotonic() + _DEPLOY_TIMEOUT_S
        while deployment["status"] != "running":
            await asyncio.sleep(3)
            deployment = await _http_get(
                client, f"/api/v1/networks/deployments/{dep_id}"
            )
            print(f"    status={deployment['status']}")
            if time.monotonic() > deadline:
                raise RuntimeError(
                    f"deployment {dep_id} did not reach running within "
                    f"{_DEPLOY_TIMEOUT_S}s; last status={deployment['status']}"
                )
        labs = await _http_get(client, "/api/v1/labs")
        lab = None
        for la in (labs or {}).get("items", []):
            if la.get("node_count") == 35 and la.get("status") == "running":
                lab = la
                break
        if lab is None:
            raise RuntimeError("could not find lab linked to deployment")

    meta = {
        "topology_id": topo_id,
        "topology_name": _TOPO_NAME,
        "deployment_id": dep_id,
        "lab_id": lab["id"],
        "lab_name": lab["name"],
        "node_count": lab["node_count"],
        "deployed_at": datetime.now(UTC).isoformat(),
    }
    _DEPLOY_META.write_text(json.dumps(meta, indent=2, sort_keys=True))
    print(f"  wrote {_DEPLOY_META}")
    return meta


async def _push_markers(meta):
    """Phase B: drop /var/cve_marker/<cve_id>.json on each vulnerable host."""
    truth = json.loads(_TRUTH_PATH.read_text())
    host_to_cves = {}
    for cve in truth["cves"]:
        for host in cve["topo_nodes"]:
            host_to_cves.setdefault(host, []).append(cve)

    nodes = await cargonet_list_nodes()
    name_to_node = {
        n["name"]: n["id"]
        for n in nodes if n.get("lab_id") == meta["lab_id"]
    }
    missing = [h for h in host_to_cves if h not in name_to_node]
    if missing:
        print(f"  ! missing nodes in lab: {missing[:5]}... "
              f"({len(missing)} total)")

    summary = {"hosts": 0, "markers": 0, "errors": []}
    for host, cves in sorted(host_to_cves.items()):
        node_id = name_to_node.get(host)
        if not node_id:
            continue
        try:
            await cargonet_exec(
                lab_id=meta["lab_id"], node_id=node_id,
                command="mkdir -p /var/cve_marker",
            )
        except Exception as exc:
            summary["errors"].append(f"{host}: mkdir: {exc}")
            continue
        for cve in cves:
            payload = {
                "cve_id": cve["cve_id"],
                "vendor": cve["vendor"],
                "product": cve["product"],
                "vuln_class": cve["vuln_class"],
                "planted_at": datetime.now(UTC).isoformat(),
                "site": "h11",
            }
            content = json.dumps(payload, sort_keys=True)
            quoted = shlex.quote(content)
            cmd = (
                "/bin/sh -c " + shlex.quote(
                    "printf '%s' " + quoted +
                    " > /var/cve_marker/" + cve["cve_id"] + ".json"
                )
            )
            try:
                rc = await cargonet_exec(
                    lab_id=meta["lab_id"], node_id=node_id, command=cmd,
                )
                if rc.get("exit_code") != 0:
                    summary["errors"].append(
                        f"{host}/{cve['cve_id']}: rc={rc.get('exit_code')} "
                        f"err={rc.get('stderr', '')[:80]}"
                    )
                    continue
                summary["markers"] += 1
            except Exception as exc:
                summary["errors"].append(
                    f"{host}/{cve['cve_id']}: {type(exc).__name__}: {exc}"
                )
        summary["hosts"] += 1
        print(f"  [{summary['hosts']:2d}] {host:20s}  "
              f"+{len(cves)} markers  total={summary['markers']}")

    return summary


async def _amain(args):
    print("=== h11 deploy ===")
    print(f"  cargonet : {_CARGONET_BASE}")
    print(f"  topology : {_TOPO_YAML}")
    print(f"  truth    : {_TRUTH_PATH}")

    if args.skip_deploy and not _DEPLOY_META.exists():
        print("! --skip-deploy requires existing deployment metadata")
        return 1

    if args.skip_deploy:
        meta = json.loads(_DEPLOY_META.read_text())
        print(f"\n[A] reusing lab {meta['lab_id']} ({meta['node_count']} nodes)")
    else:
        print("\n[A] deploy ...")
        meta = await _deploy(args)

    if args.skip_markers:
        print("\n[B] skipped markers")
        return 0

    print("\n[B] push markers ...")
    summary = await _push_markers(meta)
    print("\n=== summary ===")
    print(f"  hosts touched : {summary['hosts']}")
    print(f"  markers       : {summary['markers']}")
    print(f"  errors        : {len(summary['errors'])}")
    if summary["errors"]:
        for e in summary["errors"][:10]:
            print(f"    {e}")
        if len(summary["errors"]) > 10:
            print(f"    ... and {len(summary['errors']) - 10} more")
    return 0 if not summary["errors"] else 1


def main():
    ap = argparse.ArgumentParser(description="deploy h11 + push CVE markers")
    ap.add_argument("--reuse", action="store_true",
                    help="reuse fixtures/h11_deployment.json (skip topology+deploy)")
    ap.add_argument("--skip-deploy", action="store_true",
                    help="don't (re)deploy; use existing meta only")
    ap.add_argument("--skip-markers", action="store_true",
                    help="don't push CVE markers; deploy lab only")
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
