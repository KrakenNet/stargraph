# SPDX-License-Identifier: Apache-2.0
"""Install real vulnerable state on h11 hosts via cargonet exec.

Reads ``fixtures/vuln_install_recipes/<cve>.yaml`` and ground-truth host
mapping (``scoring_ground_truth.json``); for each (cve_id, host) pair
where a recipe exists, runs the recipe's ``setup`` then ``state``
operations on the live container via ``cargonet_exec``. Updates the
ground truth manifest with ``recipe_authored: bool`` per CVE so the
scoring report can distinguish "expected detectable" from "no recipe
authored, do not score detection".

IMPORTANT: these recipes are **lab-seed infrastructure only** — they
plant the vulnerable artifact on h11 containers so the pipeline has a
real CVE to detect / remediate. The pipeline itself MUST NOT look up
per-CVE recipes when deciding fix.cmd or probe.cmd — that would be
hand-curated remediation knowledge. The pipeline derives its fix from
``RemediationDiscoveryNode`` + LM-emitted Ansible bundles. Recipes
live next to ``install_h11_vulns.py`` because they are scoring-rig
test fixtures, not production remediation policy.

Recipe schema (one YAML per CVE):

    cve_id: CVE-XXXX-NNNN
    vuln_class: cargonet | docker | static | hitl
    install_type: config_file | apk_pin | planted_script | vulhub
    audit_signal: "human-readable signature an auditor would catch"
    description: "free text"
    setup: ["shell command", ...]
    state:
      - kind: write_file | mkdir | shell
        path: <fs path>            # write_file/mkdir
        mode: "0644"               # write_file
        content: |                 # write_file
          ...
        cmd: "shell"               # shell kind
    probe: {cmd, description?, expected_rc?}
    fix: {cmd, rationale}

The probe block is the GROUND TRUTH detector -- the workflow's
generated verify script will be graded against it (does the workflow's
probe also fire on the same vuln state?).

Run::

    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.install_h11_vulns
    uv run --no-project python -m demos.cve_remediation.scripts.install_h11_vulns --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shlex
import sys
from pathlib import Path

import yaml

from harbor.tools.cargonet.exec_node import cargonet_exec, cargonet_list_nodes

_DEMO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES = _DEMO_ROOT / "fixtures"
_RECIPES_DIR = _FIXTURES / "vuln_install_recipes"
_TRUTH_PATH = _FIXTURES / "scoring_ground_truth.json"
_DEPLOY_META = _FIXTURES / "h11_deployment.json"


def _load_recipes() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in sorted(_RECIPES_DIR.glob("*.yaml")):
        recipe = yaml.safe_load(p.read_text(encoding="utf-8"))
        cve_id = recipe.get("cve_id")
        if not cve_id:
            print(f"  ! skipping {p.name}: no cve_id")
            continue
        if cve_id != p.stem:
            print(f"  ! warning: {p.name} declares cve_id={cve_id}")
        out[cve_id] = recipe
    return out


def _b64_write_command(path: str, content: str, mode: str = "0644") -> str:
    """Build a single shell command that writes content to path.

    Uses base64 to avoid quoting hell. mkdir parent first.
    """
    b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
    parent = str(Path(path).parent) or "/"
    return (
        f"mkdir -p {shlex.quote(parent)} "
        f"&& echo {shlex.quote(b64)} | base64 -d > {shlex.quote(path)} "
        f"&& chmod {shlex.quote(mode)} {shlex.quote(path)}"
    )


def _state_to_command(item: dict) -> str:
    kind = (item.get("kind") or "shell").lower()
    if kind == "write_file":
        return _b64_write_command(
            item["path"], item.get("content", ""),
            item.get("mode", "0644"),
        )
    if kind == "mkdir":
        return f"mkdir -p {shlex.quote(item['path'])}"
    if kind == "shell":
        return str(item["cmd"])
    raise ValueError(f"unknown state kind: {kind!r}")


async def _exec(lab_id: str, node_id: str, cmd: str) -> dict:
    return await cargonet_exec(
        lab_id=lab_id, node_id=node_id,
        command=("/bin/sh -c " + shlex.quote(cmd)),
    )


async def _install_one(
    *, lab_id: str, node_id: str, host: str, recipe: dict,
    setup_done: set[tuple[str, str]],
) -> tuple[bool, list[str]]:
    """Run a recipe on a host. Returns (ok, errors)."""
    errors: list[str] = []
    cve_id = recipe["cve_id"]
    # Setup: idempotent + dedup per (host, command).
    for cmd in recipe.get("setup") or []:
        key = (host, cmd)
        if key in setup_done:
            continue
        rc = await _exec(lab_id, node_id, cmd)
        if rc.get("exit_code") != 0:
            errors.append(
                f"{host}/{cve_id}: setup {cmd!r} rc={rc.get('exit_code')} "
                f"err={rc.get('stderr', '')[:120]}"
            )
            return False, errors
        setup_done.add(key)
    # State: install vulnerable artifacts.
    for item in recipe.get("state") or []:
        try:
            cmd = _state_to_command(item)
        except Exception as exc:
            errors.append(f"{host}/{cve_id}: state shape: {exc}")
            return False, errors
        rc = await _exec(lab_id, node_id, cmd)
        if rc.get("exit_code") != 0:
            errors.append(
                f"{host}/{cve_id}: state rc={rc.get('exit_code')} "
                f"err={rc.get('stderr', '')[:120]}"
            )
            return False, errors
    # Probe: verify vuln state present (smoke check).
    probe = recipe.get("probe") or {}
    probe_cmd = probe.get("cmd")
    if probe_cmd:
        rc = await _exec(lab_id, node_id, probe_cmd)
        expected = int(probe.get("expected_rc", 0))
        if int(rc.get("exit_code", -1)) != expected:
            errors.append(
                f"{host}/{cve_id}: probe rc={rc.get('exit_code')} "
                f"(expected {expected}); recipe state did NOT establish vuln"
            )
            return False, errors
    return True, errors


async def _amain(args: argparse.Namespace) -> int:
    if not _DEPLOY_META.exists():
        print(f"! missing {_DEPLOY_META}; deploy h11 first")
        return 1
    meta = json.loads(_DEPLOY_META.read_text())
    truth = json.loads(_TRUTH_PATH.read_text())
    recipes = _load_recipes()
    print(f"=== install_h11_vulns ===")
    print(f"  lab        : {meta['lab_id']} ({meta['node_count']} nodes)")
    print(f"  recipes    : {len(recipes)}")
    print(f"  cves total : {len(truth['cves'])}")
    if args.dry_run:
        print("\n--- DRY RUN ---")
    # Authored cve_ids:
    authored_ids = set(recipes)
    # Filter ground-truth cves to those with both a recipe AND a topo host.
    targets: list[tuple[str, str, dict]] = []
    for cve in truth["cves"]:
        cid = cve["cve_id"]
        if cid not in authored_ids:
            continue
        for host in cve["topo_nodes"]:
            targets.append((cid, host, recipes[cid]))
    print(f"  targets    : {len(targets)} (cve,host) pairs")

    # Resolve host names.
    nodes = await cargonet_list_nodes()
    name_to_node = {
        n["name"]: n["id"]
        for n in nodes if n.get("lab_id") == meta["lab_id"]
    }

    setup_done: set[tuple[str, str]] = set()
    summary = {"ok": 0, "fail": 0, "errors": []}
    for i, (cid, host, recipe) in enumerate(targets, 1):
        node_id = name_to_node.get(host)
        if not node_id:
            summary["errors"].append(f"{host}/{cid}: host not in lab")
            summary["fail"] += 1
            continue
        if args.dry_run:
            print(f"  [{i:3d}/{len(targets)}] {cid} -> {host}  (would install)")
            continue
        ok, errs = await _install_one(
            lab_id=meta["lab_id"], node_id=node_id,
            host=host, recipe=recipe, setup_done=setup_done,
        )
        if ok:
            summary["ok"] += 1
            print(f"  [{i:3d}/{len(targets)}] {cid} -> {host}  OK")
        else:
            summary["fail"] += 1
            summary["errors"].extend(errs)
            print(f"  [{i:3d}/{len(targets)}] {cid} -> {host}  FAIL")
            for e in errs:
                print(f"        | {e}")

    if not args.dry_run:
        # Update ground truth: per CVE, mark recipe_authored + carry probe/fix.
        for cve in truth["cves"]:
            r = recipes.get(cve["cve_id"])
            cve["recipe_authored"] = r is not None
            if r:
                cve["install_type"] = r.get("install_type")
                cve["audit_signal"] = r.get("audit_signal")
                cve["expected_probe"] = (r.get("probe") or {}).get("cmd")
                cve["expected_fix"] = (r.get("fix") or {}).get("cmd")
        _TRUTH_PATH.write_text(json.dumps(truth, indent=2, sort_keys=True))
        print(f"\nupdated ground truth -> {_TRUTH_PATH}")

    print(f"\n=== summary ===")
    print(f"  ok        : {summary['ok']}")
    print(f"  fail      : {summary['fail']}")
    print(f"  recipes   : {len(recipes)}")
    print(f"  un-authored: {len(truth['cves']) - len(recipes)}")
    if summary["errors"][:10]:
        print("\n  first errors:")
        for e in summary["errors"][:10]:
            print(f"    {e}")
    return 0 if summary["fail"] == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="install h11 vulnerabilities")
    ap.add_argument("--dry-run", action="store_true",
                    help="list (cve, host) targets; don't run cargonet exec")
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    sys.exit(main())
