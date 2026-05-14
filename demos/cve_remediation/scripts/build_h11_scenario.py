# SPDX-License-Identifier: Apache-2.0
"""Build h11-* CargoNet topology YAML + scoring ground-truth manifest.

Single deterministic pass that emits THREE coherent artifacts:

1. ``fixtures/h11_topology.yaml`` -- containerlab-format topology the
   operator POSTs to ``/api/v1/networks/topologies`` (NOT the heavy
   Scenario shape; CargoNet's scenarios endpoint has a known
   ``status`` constraint bug -- networks/topologies is the correct
   path for raw blueprints with no scoring overlay). ~35 ``h11-*``
   nodes spanning roles (rtr/sw/fw/web/api/db/etc).

2. ``fixtures/h11_cmdb_seed.json`` -- 35 CMDB CIs + software-binding
   rows ready for ``seed_scoring_cmdb.py`` to insert into
   ``cmdb_ci`` / ``cmdb_software_relationship``. Coverage: 70 of 100
   CVEs have at least one CI binding (the prompted "70 in CMDB" rate).

3. ``fixtures/scoring_ground_truth.json`` -- truth maps used by
   ``score_report.py``:
     * ``cves[]`` -- ``{cve_id, cmdb_cis: [], topo_nodes: []}``
     * ``hosts[]`` -- ``{name, in_cmdb, in_topo, role, vulns: []}``

Drift design (matches user spec "~80% topo-CMDB overlap, both
directions drift"):

* 30 hosts in BOTH cmdb + topology  (shared baseline)
*  5 hosts in CMDB only             (decommissioned-but-tracked)
*  5 hosts in topology only         (shadow IT, CMDB blind)
   ----
* 35 hosts in topology
* 35 CMDB CIs

Of 100 CVEs: 70 have a CMDB CI binding (per user spec); the
remaining 30 hit the "no asset" path. Of the 70 CMDB-bound CVEs,
some land on CMDB-only hosts (no live container) -- exercises the
drift detection path the workflow needs to handle.

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.build_h11_scenario
    uv run --no-project python -m demos.cve_remediation.scripts.build_h11_scenario \\
        --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import yaml

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_DEFAULT_CVES = _FIXTURES / "scoring_cves_v1.json"
_OUT_TOPO = _FIXTURES / "h11_topology.yaml"
_OUT_CMDB = _FIXTURES / "h11_cmdb_seed.json"
_OUT_TRUTH = _FIXTURES / "scoring_ground_truth.json"

# Node-role definitions: role -> (cargonet kind, image, count)
# Counts sum to 35 (the topology size). All linux/alpine for image
# portability -- role is metadata for the scoring run, not for
# CargoNet's container runtime.
_ROLE_PLAN: list[tuple[str, str, str, int]] = [
    # role,    kind,    image,           count
    ("rtr",    "linux", "alpine:latest", 4),   # routers (logical role only)
    ("sw",     "linux", "alpine:latest", 3),   # switches
    ("fw",     "linux", "alpine:latest", 2),   # firewalls
    ("web",    "linux", "alpine:latest", 7),   # web servers
    ("api",    "linux", "alpine:latest", 6),   # api hosts
    ("db",     "linux", "alpine:latest", 4),   # db hosts
    ("worker", "linux", "alpine:latest", 5),   # worker pool
    ("jump",   "linux", "alpine:latest", 2),   # jump hosts
    ("idp",    "linux", "alpine:latest", 2),   # IDP / auth
]
# Role -> compatible vuln_class (which CVE buckets a host of this
# role can plausibly host).
_ROLE_VULN_AFFINITY: dict[str, set[str]] = {
    "rtr":    {"cargonet", "static"},
    "sw":     {"cargonet", "static"},
    "fw":     {"cargonet", "static", "hitl"},
    "web":    {"docker", "static", "hitl"},
    "api":    {"docker", "hitl"},
    "db":     {"docker", "static", "hitl"},
    "worker": {"docker"},
    "jump":   {"docker", "hitl", "static"},
    "idp":    {"docker", "hitl", "static"},
}

_SITE = "h11"  # Herndon site 11
_TOTAL_HOSTS = 35           # in topology
_CMDB_ONLY_HOSTS = 5        # decommissioned-but-tracked (in CMDB only)
_TOPO_ONLY_HOSTS = 5        # shadow IT (in topology only)
_TOPO_SHARED = 30           # in both (35 - 5 topo-only)
_CMDB_TOTAL = _TOPO_SHARED + _CMDB_ONLY_HOSTS  # = 35
_CMDB_BOUND_CVES = 70       # of 100; remaining 30 hit no-asset path


def _build_topology_nodes(rng: random.Random) -> list[dict]:
    """Return 35 host dicts: ``{name, role, kind, image}``."""
    out: list[dict] = []
    role_counter: dict[str, int] = defaultdict(int)
    plan = list(_ROLE_PLAN)
    for role, kind, image, count in plan:
        for _ in range(count):
            role_counter[role] += 1
            name = f"{_SITE}-{role}-{role_counter[role]:02d}"
            out.append({"name": name, "role": role,
                        "kind": kind, "image": image})
    if len(out) != _TOTAL_HOSTS:
        raise SystemExit(
            f"role plan sums to {len(out)}, expected {_TOTAL_HOSTS}"
        )
    rng.shuffle(out)
    return out


def _split_topo_cmdb(
    hosts: list[dict], rng: random.Random,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (shared, topo_only, cmdb_only_extras)."""
    # Topology = all 35 hosts. Of those, 30 are also in CMDB; 5 topo-only.
    # Then CMDB has 5 EXTRA decommissioned hosts not in topology.
    shuffled = list(hosts)
    rng.shuffle(shuffled)
    topo_only = shuffled[:_TOPO_ONLY_HOSTS]
    shared = shuffled[_TOPO_ONLY_HOSTS:]
    # Generate 5 cmdb-only "decommissioned" CIs with the same shape.
    decom_roles = ["web", "api", "db", "worker", "jump"]
    cmdb_only: list[dict] = []
    for i, role in enumerate(decom_roles[:_CMDB_ONLY_HOSTS], 1):
        cmdb_only.append({
            "name": f"{_SITE}-decom-{role}-{i:02d}",
            "role": role,
            "kind": "linux",
            "image": "alpine:latest",
            "decommissioned": True,
        })
    return shared, topo_only, cmdb_only


def _assign_vulns(
    cves: list[dict], topo_hosts: list[dict], cmdb_hosts: list[dict],
    rng: random.Random,
) -> dict[str, list[str]]:
    """Map CVEs to host names.

    Returns ``{cve_id: [host_name, ...]}`` covering the CMDB rate
    (70 CVEs get >=1 host; 30 get []).

    Distribution: 70 covered CVEs spread across 35 CMDB hosts; some
    hosts get 3-4 vulns, some get 0. Hosts only get vulns whose
    vuln_class matches their role affinity (so a router doesn't get
    a Spring deserialization CVE).
    """
    cmdb_by_name = {h["name"]: h for h in cmdb_hosts}
    # Pick 70 CVEs to cover; remainder are no-asset CVEs.
    picks = rng.sample(cves, _CMDB_BOUND_CVES)
    pick_ids = {c["cve_id"] for c in picks}
    out: dict[str, list[str]] = {c["cve_id"]: [] for c in cves}

    # For each picked CVE, choose 1-2 hosts whose role affinity matches.
    for cve in picks:
        vc = cve["vuln_class"]
        candidates = [
            h for h in cmdb_hosts
            if vc in _ROLE_VULN_AFFINITY.get(h["role"], set())
        ]
        if not candidates:
            # Affinity miss: degrade to ANY cmdb host so we don't
            # break the 70-coverage target. This will look like a
            # CMDB row that lists a vendor mismatch -- realistic.
            candidates = list(cmdb_hosts)
        # Multi-vuln distribution: 30% chance of 2 hosts, 5% of 3.
        roll = rng.random()
        n = 3 if roll < 0.05 else (2 if roll < 0.35 else 1)
        n = min(n, len(candidates))
        chosen = rng.sample(candidates, n)
        out[cve["cve_id"]] = [h["name"] for h in chosen]
    # Sanity: 70 CVEs covered, 30 empty.
    covered = sum(1 for v in out.values() if v)
    print(f"  vuln mapping: {covered} CVEs covered, "
          f"{len(out) - covered} no-asset")
    assert covered == _CMDB_BOUND_CVES, covered
    # Filter chosen hosts that are CMDB-only (not in topology) into
    # the ground-truth's separate "topo_nodes" projection.
    return out


def _emit_topology_yaml(
    topo_hosts: list[dict], rng: random.Random,
) -> dict:
    """Build a containerlab topology dict (POST /networks/topologies shape).

    Schema (per ``CargoNet/internal/network/validator.go``):

        name: <topo name>
        topology:
          nodes:
            <name>: {kind, image}
          links:
            - endpoints: [<node>:<iface>, <node>:<iface>]
    """
    nodes_map: dict[str, dict] = {}
    for h in topo_hosts:
        nodes_map[h["name"]] = {"kind": h["kind"], "image": h["image"]}

    # Star topology around first router. Each link uses unique
    # interface names to satisfy containerlab's 1:1 endpoint rule.
    rtrs = [h["name"] for h in topo_hosts if h["role"] == "rtr"]
    leaves = [h["name"] for h in topo_hosts if h["role"] != "rtr"]
    links: list[dict] = []
    if rtrs:
        core = rtrs[0]
        # Wire each non-core router to core with a unique interface.
        for i, r in enumerate(rtrs[1:], 2):
            links.append({"endpoints": [f"{core}:eth{i}", f"{r}:eth1"]})
        # Distribute leaves across rtrs round-robin; interface index
        # is global so anchor reuse is safe.
        for i, leaf in enumerate(leaves):
            anchor = rtrs[i % len(rtrs)]
            links.append({
                "endpoints": [f"{anchor}:eth{10 + i}", f"{leaf}:eth1"],
            })

    return {
        "name": "h11-topology",
        "topology": {"nodes": nodes_map, "links": links},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="build h11 scenario + ground truth")
    ap.add_argument("--seed", type=int, default=20260507)
    ap.add_argument("--cves", type=Path, default=_DEFAULT_CVES)
    ap.add_argument("--out-topology", type=Path, default=_OUT_TOPO)
    ap.add_argument("--out-cmdb", type=Path, default=_OUT_CMDB)
    ap.add_argument("--out-truth", type=Path, default=_OUT_TRUTH)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    payload = json.loads(args.cves.read_text())
    cves = payload["cves"]
    print(f"loaded {len(cves)} CVEs from {args.cves}")

    topo_hosts = _build_topology_nodes(rng)
    shared, topo_only, cmdb_only = _split_topo_cmdb(topo_hosts, rng)
    cmdb_hosts = shared + cmdb_only       # 30 + 5 = 35
    topo_full = shared + topo_only        # 30 + 5 = 35
    print(f"topology: {len(topo_full)} hosts "
          f"(shared={len(shared)}, topo_only={len(topo_only)})")
    print(f"cmdb:     {len(cmdb_hosts)} CIs "
          f"(shared={len(shared)}, cmdb_only={len(cmdb_only)})")

    cve_to_cmdb = _assign_vulns(cves, topo_full, cmdb_hosts, rng)
    # topo_nodes for a CVE = subset of cmdb_cis that ALSO appear in topo.
    topo_names = {h["name"] for h in topo_full}
    truth_cves = []
    for cve in cves:
        cis = cve_to_cmdb.get(cve["cve_id"], [])
        topo_for_cve = [n for n in cis if n in topo_names]
        truth_cves.append({
            "cve_id": cve["cve_id"],
            "vuln_class": cve["vuln_class"],
            "vendor": cve["vendor"],
            "product": cve["product"],
            "cmdb_cis": cis,
            "topo_nodes": topo_for_cve,
            "in_cmdb": bool(cis),
            "in_topology": bool(topo_for_cve),
        })

    # Per-host vuln roll-up for the truth manifest.
    host_vulns: dict[str, list[str]] = defaultdict(list)
    for cve_id, hosts in cve_to_cmdb.items():
        for h in hosts:
            host_vulns[h].append(cve_id)
    truth_hosts = []
    cmdb_names = {h["name"] for h in cmdb_hosts}
    for h in topo_full:
        truth_hosts.append({
            "name": h["name"], "role": h["role"],
            "in_cmdb": h["name"] in cmdb_names,
            "in_topology": True,
            "vulns": sorted(host_vulns.get(h["name"], [])),
        })
    for h in cmdb_only:
        truth_hosts.append({
            "name": h["name"], "role": h["role"],
            "in_cmdb": True,
            "in_topology": False,
            "vulns": sorted(host_vulns.get(h["name"], [])),
        })

    # Coverage & drift summary.
    covered_in_topo = sum(1 for c in truth_cves if c["in_topology"])
    covered_cmdb = sum(1 for c in truth_cves if c["in_cmdb"])
    print("\n=== coverage summary ===")
    print(f"  CVEs in CMDB     : {covered_cmdb}/100 (target=70)")
    print(f"  CVEs in topology : {covered_in_topo}/100")
    print(f"  CVEs no-asset    : {100 - covered_cmdb}/100 (target=30)")

    # Write artifacts.
    topology = _emit_topology_yaml(topo_full, rng)
    args.out_topology.parent.mkdir(parents=True, exist_ok=True)
    args.out_topology.write_text(yaml.safe_dump(topology, sort_keys=False))
    print(f"\nwrote topology YAML  -> {args.out_topology}")

    cmdb_payload = {
        "site": _SITE,
        "ci_count": len(cmdb_hosts),
        "cis": [
            {
                "name": h["name"],
                "role": h["role"],
                "decommissioned": h.get("decommissioned", False),
                "in_topology": h["name"] in topo_names,
            }
            for h in cmdb_hosts
        ],
        "bindings": [
            {
                "cve_id": cve["cve_id"],
                "vendor": cve["vendor"],
                "product": cve["product"],
                "host_names": cve_to_cmdb[cve["cve_id"]],
            }
            for cve in cves
            if cve_to_cmdb[cve["cve_id"]]
        ],
    }
    args.out_cmdb.write_text(json.dumps(cmdb_payload, indent=2, sort_keys=True))
    print(f"wrote cmdb seed       -> {args.out_cmdb}")

    truth = {
        "schema_version": "1.0",
        "site": _SITE,
        "seed": args.seed,
        "summary": {
            "topo_hosts": len(topo_full),
            "cmdb_cis": len(cmdb_hosts),
            "shared": len(shared),
            "topo_only": len(topo_only),
            "cmdb_only": len(cmdb_only),
            "cves_in_cmdb": covered_cmdb,
            "cves_in_topology": covered_in_topo,
            "cves_no_asset": 100 - covered_cmdb,
        },
        "cves": truth_cves,
        "hosts": truth_hosts,
    }
    args.out_truth.write_text(json.dumps(truth, indent=2, sort_keys=True))
    print(f"wrote ground truth    -> {args.out_truth}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
