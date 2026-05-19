# SPDX-License-Identifier: Apache-2.0
"""Reset CargoNet lab nodes to a known-vulnerable state for a CVE.

Reads the advisory via fetch_advisory, picks a vulnerable version
(last of OSV-aggregated exact_affected_versions, with fallbacks),
looks up matching CargoNet lab nodes, and installs the vulnerable
version via cargonet REST.

Idempotent: already-vulnerable nodes no-op; patched nodes downgrade;
missing-package nodes install the vulnerable pin.

Run:
    set -a; source demos/cve_remediation/.env; set +a
    uv run --no-project python -m demos.cve_remediation.scripts.seed_cargonet_vulnerable \
        --cve CVE-2024-26130
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

from demos.cve_remediation.tools.fetch_advisory import fetch_advisory
from harbor.tools.cargonet import cargonet_exec, cargonet_list_nodes


def _pick_vulnerable_version(adv: dict[str, Any]) -> str:
    exact = adv.get("exact_affected_versions") or []
    if exact:
        return str(exact[-1])
    pin = str(adv.get("vulnerable_pin") or "").strip()
    if pin:
        return pin
    ranges = adv.get("affected_version_ranges") or []
    for r in ranges:
        intro = str((r or {}).get("introduced") or "").strip()
        if intro:
            return intro
    return ""


def _pick_package(adv: dict[str, Any]) -> str:
    pkg = str(adv.get("osv_package_name") or "").strip()
    if pkg:
        return pkg
    return str(adv.get("matched_candidate_product") or "").strip()


def _pick_channel(adv: dict[str, Any]) -> str:
    return str(adv.get("install_channel") or "").lower().strip()


def _install_command(channel: str, pkg: str, version: str) -> str:
    if channel in ("pip", "pypi"):
        return (
            f"pip install --quiet --force-reinstall '{pkg}=={version}' "
            f"2>&1 | tail -3 || true"
        )
    if channel in ("apt", "deb"):
        return (
            f"apt-get update -qq && "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -qq --allow-downgrades "
            f"-y {pkg}={version} 2>&1 | tail -5"
        )
    if channel in ("rpm", "yum", "dnf"):
        return f"yum downgrade -y {pkg}-{version} 2>&1 | tail -5"
    if channel == "npm":
        return f"npm install -g {pkg}@{version} 2>&1 | tail -5"
    return (
        f"pip install --quiet --force-reinstall '{pkg}=={version}' "
        f"2>&1 | tail -3 || true"
    )


def _show_command(channel: str, pkg: str) -> str:
    if channel in ("pip", "pypi"):
        return f"pip show {pkg} 2>/dev/null | grep ^Version: || true"
    if channel in ("apt", "deb"):
        return f"dpkg -s {pkg} 2>/dev/null | grep ^Version: || true"
    if channel in ("rpm", "yum", "dnf"):
        return (
            f"rpm -q --queryformat 'Version: %{{VERSION}}\\n' {pkg} "
            f"2>/dev/null || true"
        )
    if channel == "npm":
        return (
            f"npm list -g {pkg} --depth=0 --json 2>/dev/null | "
            f"python3 -c 'import sys,json;d=json.load(sys.stdin);"
            f"v=(d.get(\"dependencies\") or {{}}).get(\"{pkg}\",{{}}).get(\"version\",\"\");"
            f"print(\"Version:\",v)' || true"
        )
    return f"pip show {pkg} 2>/dev/null | grep ^Version: || true"


def _parse_version_line(out: str) -> str:
    for line in (out or "").splitlines():
        if line.lower().startswith("version:"):
            return line.split(":", 1)[1].strip()
    return ""


async def reset_cargonet_to_vulnerable(
    cve_id: str,
    *,
    host_filter: list[str] | None = None,
    verbose: bool = True,
) -> dict[str, Any]:
    adv = await fetch_advisory(cve_id=cve_id)
    pkg = _pick_package(adv)
    channel = _pick_channel(adv) or "pip"
    vulnerable = _pick_vulnerable_version(adv)
    summary: dict[str, Any] = {
        "cve_id": cve_id,
        "package": pkg,
        "channel": channel,
        "vulnerable_version": vulnerable,
        "nodes": [],
        "errors": [],
    }
    if not (pkg and vulnerable):
        summary["errors"].append(
            f"insufficient advisory data: pkg={pkg!r} ver={vulnerable!r}"
        )
        return summary
    nodes = await cargonet_list_nodes()
    targets = [
        n for n in nodes
        if n.get("kind") == "linux"
        and (not host_filter or n.get("name") in host_filter)
    ]
    if not targets:
        summary["errors"].append("no CargoNet linux nodes available")
        return summary
    install_cmd = _install_command(channel, pkg, vulnerable)
    show_cmd = _show_command(channel, pkg)
    presence_cmd = {
        "pip": "command -v pip || command -v pip3",
        "pypi": "command -v pip || command -v pip3",
        "apt": "command -v apt-get",
        "deb": "command -v apt-get",
        "rpm": "command -v rpm",
        "yum": "command -v yum",
        "dnf": "command -v dnf",
        "npm": "command -v npm",
    }.get(channel, "command -v pip || command -v pip3")
    for n in targets:
        host = n.get("name", "")
        row: dict[str, Any] = {"host": host, "ok": False}
        try:
            check = await cargonet_exec(
                lab_id=n["lab_id"], node_id=n["id"],
                command=presence_cmd, timeout=10.0,
            )
            if int(check.get("exit_code", -1)) != 0:
                row["error"] = f"channel {channel!r} not present on host"
                row["skipped"] = True
                summary["nodes"].append(row)
                if verbose:
                    print(
                        f"  [seed] {host}: skipped ({channel} not present)",
                        flush=True,
                    )
                continue
            inst = await cargonet_exec(
                lab_id=n["lab_id"], node_id=n["id"],
                command=install_cmd, timeout=180.0,
            )
            row["install_exit_code"] = inst.get("exit_code", -1)
            row["install_tail"] = (inst.get("output") or "")[-160:]
            show = await cargonet_exec(
                lab_id=n["lab_id"], node_id=n["id"],
                command=show_cmd, timeout=30.0,
            )
            observed = _parse_version_line(show.get("output", ""))
            row["observed_version"] = observed
            row["ok"] = observed == vulnerable
            if not row["ok"]:
                row["error"] = (
                    f"observed {observed!r} != vulnerable {vulnerable!r}"
                )
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
        summary["nodes"].append(row)
        if verbose:
            status = "OK" if row.get("ok") else f"FAIL ({row.get('error', '')})"
            print(
                f"  [seed] {host}: {pkg}=={vulnerable} -> {status}",
                flush=True,
            )
    return summary


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="seed_cargonet_vulnerable")
    parser.add_argument("--cve", default=os.environ.get("STEP7_CVE", "CVE-2024-26130"))
    parser.add_argument("--host", action="append", default=[])
    args = parser.parse_args(argv)
    summary = await reset_cargonet_to_vulnerable(
        cve_id=args.cve,
        host_filter=args.host or None,
    )
    print(
        f"[seed] cve={summary['cve_id']} pkg={summary['package']} "
        f"channel={summary['channel']} "
        f"target_version={summary['vulnerable_version']}"
    )
    if summary["errors"]:
        for e in summary["errors"]:
            print(f"  ! {e}")
        return 1
    # Skipped nodes (channel not present) aren't failures; only count
    # nodes that attempted seed and missed the target version.
    failed = [
        r for r in summary["nodes"]
        if not r.get("ok") and not r.get("skipped")
    ]
    return 0 if not failed else 1


def main() -> int:
    return asyncio.run(_main_async(sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())
