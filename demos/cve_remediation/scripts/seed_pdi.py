#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Idempotent seed script for the cve_remediation demo's ServiceNow PDI.

The demo expects a specific shape of CMDB inventory + relationships for
its CMDB↔CargoNet correlation to be meaningful. This script makes that
shape deterministic so the live run produces the same affected-asset
list across re-runs.

What it seeds (each step skipped if the record already exists):

* **NLTK Software CI** -- ``cmdb_ci_spkg`` named exactly
  ``NLTK (Natural Language Toolkit)`` with ``version=3.6`` (so the
  remediation has a real version to bump).
* **Three host CIs** -- ``cmdb_ci_unix_server`` named
  ``laptop-nlp-dev-01/02/03``, matching the CargoNet lab inventory by
  name. We rename pre-existing ``cmdb_ci`` rows to the ``unix_server``
  class so the host has the right schema (network interfaces, OS, etc).
* **Three Runs-on relationships** -- one per host CI, binding NLTK
  Software (parent) to the host (child) with ``cmdb_rel_type =
  Runs on::Runs``. CMDB traversal queries can then find affected hosts
  by walking from the Software CI.
* **Vulnerability Management Service** -- ``cmdb_ci_service`` so the
  CR's ``business_service`` field has somewhere real to point.

Idempotency: every record is keyed by ``name`` (and
``parent``/``child`` for relationships); existing rows are detected via
GET-by-query and re-used. Re-running prints "exists" and exits 0.

Auth: reads ``SERVICENOW_*`` env vars (the same ones the demo's
``.env`` already provides). Run from the repo root::

    set -a; . demos/cve_remediation/.env; set +a
    uv run --no-project python demos/cve_remediation/scripts/seed_pdi.py

Output: a one-line-per-record summary listing each ``sys_id`` and a
``[CREATED]`` / ``[EXISTS]`` tag, plus a final summary the operator
can sanity-check before the demo runs.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Static seeds. These are the names CargoNet's lab inventory uses; the
# whole point of seeding is to make the CMDB names match exactly so the
# correlation step can join on them.
# ---------------------------------------------------------------------------

NLTK_SOFTWARE_NAME = "NLTK (Natural Language Toolkit)"
NLTK_SOFTWARE_VERSION = "3.6"
NLTK_SOFTWARE_VENDOR = "NLTK Project"

def _discover_hosts_from_cargonet() -> list[str]:
    """Discover lab host names live from the CargoNet REST surface.

    Closes CargoNet Phase 5: drop env-hardcoded host lists. The seed
    used to carry a static ``HOST_NAMES`` literal that mirrored the
    digital-twin lab inventory; that drifts the moment the lab adds
    or renames a node. Production scales to 1000+ devices on tens of
    labs -- no static list survives that.

    Strategy:

    * Query CargoNet ``/api/v1/labs/<lab>/nodes`` (every running lab).
    * Filter to ``kind in {"linux","unix","host"}`` so the seed only
      registers OS-level hosts in the CMDB (skipping routers/switches).
    * Sort by ``name`` so re-runs produce a stable order.
    * Optional override: ``CVE_REM_HOST_LIMIT`` caps the count for
      smaller demos (default = no cap).

    Fail-loud: a CargoNet outage or empty inventory returns ``[]`` and
    the caller exits with a clear message rather than silently
    seeding a stale fixture list.
    """
    base = os.environ.get(
        "CARGONET_BASE_URL", "http://localhost:28080"
    ).rstrip("/")
    timeout = float(os.environ.get("CARGONET_TIMEOUT", "10") or "10")
    try:
        with httpx.Client(timeout=timeout) as client:
            labs_resp = client.get(f"{base}/api/v1/labs")
            labs_resp.raise_for_status()
            labs_body = labs_resp.json() or {}
            # CargoNet returns {"items": [...]} (paginated); older
            # builds returned {"labs": [...]}; accept both.
            labs = (
                labs_body.get("items")
                or labs_body.get("labs")
                or []
            )
            hosts: list[str] = []
            seen: set[str] = set()
            for lab in labs:
                lab_id = str(lab.get("id") or lab.get("lab_id") or "")
                if not lab_id:
                    continue
                nodes_resp = client.get(
                    f"{base}/api/v1/labs/{lab_id}/nodes"
                )
                if nodes_resp.status_code != 200:
                    continue
                nodes_body = nodes_resp.json() or {}
                rows = (
                    nodes_body.get("items")
                    or nodes_body.get("nodes")
                    or []
                )
                for n in rows:
                    kind = str(n.get("kind", "")).lower()
                    if kind not in {"linux", "unix", "host"}:
                        continue
                    name = str(n.get("name", "")).strip()
                    if not name or name in seen:
                        continue
                    seen.add(name)
                    hosts.append(name)
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: cargonet host discovery failed "
            f"({type(exc).__name__}: {exc}); refusing to fall back to a "
            "stale static list. Start CargoNet (CARGONET_BASE_URL) or "
            "set CVE_REM_HOST_NAMES_OVERRIDE=h1,h2,h3 to skip discovery.",
            file=sys.stderr,
        )
        return []
    hosts.sort()
    cap = os.environ.get("CVE_REM_HOST_LIMIT", "").strip()
    if cap.isdigit():
        hosts = hosts[: int(cap)]
    return hosts


def _resolve_host_names() -> list[str]:
    """Resolve the lab host list (override env var or live CargoNet).

    Order of precedence:
      1. ``CVE_REM_HOST_NAMES_OVERRIDE`` (comma-sep) -- escape hatch
         for offline demos / CI without CargoNet running.
      2. Live CargoNet REST discovery.
    Empty result is a hard error: seeding without hosts produces a
    no-op CMDB and breaks downstream correlation.
    """
    override = os.environ.get(
        "CVE_REM_HOST_NAMES_OVERRIDE", ""
    ).strip()
    if override:
        names = [h.strip() for h in override.split(",") if h.strip()]
        if names:
            return names
    hosts = _discover_hosts_from_cargonet()
    if not hosts:
        print(
            "ERROR: no host names resolved (CargoNet returned empty + "
            "no CVE_REM_HOST_NAMES_OVERRIDE). Cannot seed CMDB.",
            file=sys.stderr,
        )
        sys.exit(2)
    return hosts


HOST_NAMES = _resolve_host_names()

# Step 3 multi-CVE proof: real Software CIs already in PDI's default
# seed (Apache Log4j 2, xz-utils) are missing Runs-on relationships, so
# the correlation node returns ``not_applicable`` for them. Seeding the
# relationships against the same lab-host CIs lets us demonstrate
# correlation working across distinct CVEs without inventing fake
# software entries. Each entry is matched by exact name; the seed is
# idempotent on (parent, child, type) so re-running is safe.
EXTRA_SOFTWARE_BINDINGS: list[dict[str, object]] = [
    {
        "name": "Apache Log4j 2",
        # Bind Log4j to the same lab hosts -- they're general-purpose
        # development workstations, so it's plausible they run a Java
        # service that pulls in Log4j 2.
        "hosts": HOST_NAMES,
    },
    {
        "name": "xz-utils",
        # xz-utils ships in every Linux base image; bind to all hosts.
        "hosts": HOST_NAMES,
    },
    {
        # cups-browsed isn't in the PDI default catalog, so we create
        # it as well as seeding the Runs-on edge. ``create_if_missing``
        # is True only for this entry so we don't accidentally invent
        # entries for the others.
        "name": "cups-browsed",
        "create_if_missing": True,
        "version": "2.0.1",
        "vendor": "OpenPrinting",
        "short_description": (
            "OpenPrinting cups-browsed -- network printing service "
            "targeted by CVE-2024-47176. Installed on a subset of lab "
            "hosts that act as print gateways."
        ),
        # Bind to two of the three lab hosts -- the third is a non-
        # printing dev workstation, so the asymmetry exercises the
        # ``count(affected_assets) < count(hosts)`` path.
        "hosts": HOST_NAMES[:2],
    },
    {
        # PyPI cryptography library -- exercises CVE-2024-26130 and
        # similar PyCA advisories on the Python pip channel. Bound to
        # all lab hosts because the Python venv on each NLP-dev
        # workstation pulls cryptography in transitively.
        "name": "cryptography",
        "create_if_missing": True,
        "version": "41.0.0",
        "vendor": "Python Cryptographic Authority",
        "short_description": (
            "Python ``cryptography`` library -- transitively pulled in "
            "by the NLP venv on every lab host."
        ),
        "hosts": HOST_NAMES,
    },
    {
        # PyPI requests library -- ubiquitous HTTP client. Exercises
        # CVE-2023-32681 and related cookie/session advisories on the
        # pip channel. Bound to all lab hosts.
        "name": "requests",
        "create_if_missing": True,
        "version": "2.30.0",
        "vendor": "Python Software Foundation",
        "short_description": (
            "Python ``requests`` HTTP client -- bundled in the NLP "
            "venv on every lab host."
        ),
        "hosts": HOST_NAMES,
    },
]

# 'Runs on::Runs' rel_type — Software runs on Host. Discovered via:
# GET /api/now/table/cmdb_rel_type?sysparm_query=nameLIKERuns
RUNS_ON_REL_TYPE_SYS_ID = "60bc4e22c0a8010e01f074cbe6bd73c3"

VULN_SERVICE_NAME = "Vulnerability Management"


def env(key: str, required: bool = True) -> str:
    value = os.environ.get(key, "").strip()
    if required and not value:
        print(f"ERROR: {key} is unset; source .env first.", file=sys.stderr)
        sys.exit(2)
    return value


def make_client() -> httpx.Client:
    base_url = env("SERVICENOW_BASE_URL").rstrip("/")
    user = env("SERVICENOW_USERNAME")
    password = env("SERVICENOW_PASSWORD")
    return httpx.Client(
        base_url=base_url,
        auth=(user, password),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30.0,
    )


def get_or_create(
    client: httpx.Client,
    table: str,
    *,
    query: str,
    body: dict[str, Any],
    label: str,
) -> str:
    """Return ``sys_id`` of an existing row matching ``query`` or create one.

    ``query`` is a ServiceNow ``sysparm_query`` value (e.g.
    ``name=foo^bar=baz``). ``body`` is the POST payload used when no
    match exists. Prints a one-line audit entry to stdout.
    """
    resp = client.get(
        f"/api/now/table/{table}",
        params={"sysparm_query": query, "sysparm_limit": "1", "sysparm_fields": "sys_id"},
    )
    resp.raise_for_status()
    rows = resp.json().get("result") or []
    if rows:
        sys_id = str(rows[0]["sys_id"])
        print(f"[EXISTS]  {table:<22} {sys_id}  {label}")
        return sys_id
    resp = client.post(f"/api/now/table/{table}", json=body)
    resp.raise_for_status()
    sys_id = str(resp.json()["result"]["sys_id"])
    print(f"[CREATED] {table:<22} {sys_id}  {label}")
    return sys_id


def reclassify_to_unix_server(client: httpx.Client, sys_id: str, host_name: str) -> None:
    """Promote a generic ``cmdb_ci`` row to ``cmdb_ci_unix_server``.

    PDI default name-only seeding lands rows on the base ``cmdb_ci``
    class; the demo wants ``unix_server`` so downstream queries that
    filter by class find them. This is a no-op when the row is already
    on the right class.
    """
    resp = client.get(
        f"/api/now/table/cmdb_ci/{sys_id}",
        params={"sysparm_fields": "sys_class_name"},
    )
    resp.raise_for_status()
    current = (resp.json().get("result") or {}).get("sys_class_name") or ""
    if current == "cmdb_ci_unix_server":
        return
    # ServiceNow won't move the row across classes via plain PATCH; the
    # supported path is to set ``sys_class_name`` directly which the
    # platform reclassifies in place.
    resp = client.patch(
        f"/api/now/table/cmdb_ci/{sys_id}",
        json={"sys_class_name": "cmdb_ci_unix_server", "os": "Linux"},
    )
    resp.raise_for_status()
    print(f"           reclassified         {sys_id}  {host_name} -> cmdb_ci_unix_server")


def main() -> int:
    client = make_client()
    print(f"Seeding PDI at {env('SERVICENOW_BASE_URL')}")

    # 1. NLTK Software CI on cmdb_ci_spkg (idempotent by exact name).
    nltk_sys_id = get_or_create(
        client,
        "cmdb_ci_spkg",
        query=f"name={NLTK_SOFTWARE_NAME}",
        body={
            "name": NLTK_SOFTWARE_NAME,
            "version": NLTK_SOFTWARE_VERSION,
            "vendor": NLTK_SOFTWARE_VENDOR,
            "short_description": (
                "Natural Language Toolkit -- Python library for NLP. "
                "Used by NLP development hosts. Vulnerable to CVE-2026-33231 "
                "(WordNet DoS) at versions <= 3.8."
            ),
        },
        label=NLTK_SOFTWARE_NAME,
    )

    # 2. Three host CIs matching CargoNet lab node names exactly.
    host_sys_ids: dict[str, str] = {}
    for host in HOST_NAMES:
        sys_id = get_or_create(
            client,
            "cmdb_ci",
            query=f"name={host}",
            body={
                "name": host,
                "sys_class_name": "cmdb_ci_unix_server",
                "short_description": (
                    f"NLP development workstation. Provisioned in CargoNet lab "
                    f"as node '{host}'. Runs NLTK for prototype training jobs."
                ),
                "os": "Linux",
            },
            label=host,
        )
        reclassify_to_unix_server(client, sys_id, host)
        host_sys_ids[host] = sys_id

    # 3. Runs-on relationships (Software -> Host) keyed by parent+child.
    for host, host_id in host_sys_ids.items():
        get_or_create(
            client,
            "cmdb_rel_ci",
            query=(
                f"parent={nltk_sys_id}^child={host_id}"
                f"^type={RUNS_ON_REL_TYPE_SYS_ID}"
            ),
            body={
                "parent": nltk_sys_id,
                "child": host_id,
                "type": RUNS_ON_REL_TYPE_SYS_ID,
            },
            label=f"NLTK runs_on {host}",
        )

    # 3b. Runs-on bindings for additional pre-existing Software CIs
    # (Apache Log4j 2, xz-utils). Each is looked up by exact name; if
    # absent, the seed step is skipped with a notice rather than
    # creating a fake Software CI -- that's the ``no cheats`` line.
    for spec in EXTRA_SOFTWARE_BINDINGS:
        sw_name = str(spec["name"])
        resp = client.get(
            "/api/now/table/cmdb_ci_spkg",
            params={
                "sysparm_query": f"name={sw_name}",
                "sysparm_limit": "1",
                "sysparm_fields": "sys_id,name",
            },
        )
        resp.raise_for_status()
        rows = resp.json().get("result") or []
        if rows:
            sw_sys_id = str(rows[0]["sys_id"])
        elif spec.get("create_if_missing"):
            sw_sys_id = get_or_create(
                client,
                "cmdb_ci_spkg",
                query=f"name={sw_name}",
                body={
                    "name": sw_name,
                    "version": str(spec.get("version", "")),
                    "vendor": str(spec.get("vendor", "")),
                    "short_description": str(spec.get("short_description", "")),
                },
                label=sw_name,
            )
        else:
            print(f"[SKIP]    cmdb_ci_spkg          (not in PDI)         {sw_name}")
            continue
        for host in spec["hosts"]:  # type: ignore[union-attr]
            host_id = host_sys_ids.get(str(host))
            if not host_id:
                print(f"[SKIP]    cmdb_rel_ci           (host missing)       {host}")
                continue
            get_or_create(
                client,
                "cmdb_rel_ci",
                query=(
                    f"parent={sw_sys_id}^child={host_id}"
                    f"^type={RUNS_ON_REL_TYPE_SYS_ID}"
                ),
                body={
                    "parent": sw_sys_id,
                    "child": host_id,
                    "type": RUNS_ON_REL_TYPE_SYS_ID,
                },
                label=f"{sw_name} runs_on {host}",
            )

    # 4. Vulnerability Management business service for CR.business_service.
    svc_sys_id = get_or_create(
        client,
        "cmdb_ci_service",
        query=f"name={VULN_SERVICE_NAME}",
        body={
            "name": VULN_SERVICE_NAME,
            "short_description": (
                "Service responsible for tracking, prioritizing, and "
                "remediating vulnerabilities (CVEs) across the fleet."
            ),
            "service_classification": "Business Service",
            "operational_status": "1",
        },
        label=VULN_SERVICE_NAME,
    )

    # 5. Service offering parented to the Vulnerability Management
    # service so CR.service_offering can be set without an env override.
    # The PDI default Change Model expects a non-empty service_offering
    # for the criterion "all required spec fields populated".
    offering_name = "Automated CVE Remediation"
    offering_sys_id = get_or_create(
        client,
        "service_offering",
        query=f"name={offering_name}^parent={svc_sys_id}",
        body={
            "name": offering_name,
            "parent": svc_sys_id,
            "short_description": (
                "Offering for fully automated CVE remediation runs: "
                "intake -> sandbox probe -> CR -> progressive rollout "
                "-> verify -> retro -> Doc+ -> drift watch."
            ),
            "service_classification": "Service Offering",
            "operational_status": "1",
        },
        label=offering_name,
    )

    summary = {
        "nltk_software_ci": nltk_sys_id,
        "host_cis": host_sys_ids,
        "vulnerability_service_ci": svc_sys_id,
        "vulnerability_service_offering_ci": offering_sys_id,
        "runs_on_rel_type": RUNS_ON_REL_TYPE_SYS_ID,
    }
    print()
    print("Seed summary (paste into .env or scripts as needed):")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
