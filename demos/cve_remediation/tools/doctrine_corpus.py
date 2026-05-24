# SPDX-License-Identifier: Apache-2.0
"""Real-doctrine corpus fetchers (Phase 0 ingest).

Replaces the trimmed ``fixtures/doctrine/*.md`` markdown sample with the
actual upstream corpora and the actual published mappings between them:

* **NIST SP 800-53 rev5 controls** — pulled from
  ``usnistgov/oscal-content`` as the canonical OSCAL JSON catalog.
  Yields ~1,189 control + control-enhancement entries (AC-1 ... SR-12).
* **MITRE ATT&CK Enterprise techniques** — pulled from ``mitre/cti``
  STIX 2.x bundle. Yields ~600+ technique + sub-technique entries
  (T1059, T1190, T1547, ...).
* **MITRE CAPEC** — STIX 2.x bundle. Each CAPEC carries the authoritative
  ``Related_Weaknesses`` (CWE) edges and ``Taxonomy_Mappings → ATT&CK``
  edges. CAPEC is the bridge that lets us close the chain
  ``Control → ATT&CK → CAPEC → CWE``.
* **CWE catalog (View 1000)** — MITRE CWE CSV bundle. Yields the full
  CWE name + abstraction so the KG carries readable metadata.
* **NIST 800-53r5 → ATT&CK mapping** — Center for Threat-Informed
  Defense STIX bundle. The authoritative published Control→ATT&CK
  mapping (vs. the previous regex co-occurrence cheat).

Output: structured ``nodes`` + ``edges`` lists matching the existing
``broker_request_envelope`` shape so :class:`KgLoaderNode` can write them
straight to Neo4j without further regex parsing.

Caching: each source is pulled to ``$HARBOR_CACHE_ROOT/doctrine/<name>``
and reused for 7 days unless ``HARBOR_DOCTRINE_REFRESH=1``. On feed
failure with a cache present, the cached blob is used (replay mode);
with no cache present, the fetch raises ``HarborRuntimeError`` so the
caller can route the run to quarantine instead of silently emitting an
empty doctrine.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import zipfile
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import httpx

from harbor.errors import HarborRuntimeError

__all__ = [
    "build_doctrine_kg",
    "DoctrineCorpus",
]


# Chain version tag — folded into the corpus sha256 so that any change
# to chain composition (e.g. enabling ChildOf propagation) invalidates a
# previously-allowlisted manifest and forces re-ingest.
_CHAIN_VERSION = b"v2-childof-propagation"

_DEFAULT_TTL_S = int(os.environ.get("HARBOR_DOCTRINE_TTL_S", str(7 * 24 * 3600)))
_HTTP_TIMEOUT_S = float(os.environ.get("HARBOR_DOCTRINE_TIMEOUT_S", "120.0"))
_CACHE_ROOT = Path(
    os.environ.get("HARBOR_CACHE_ROOT", ".harbor/cache")
) / "doctrine"

# Upstream URLs (overridable for offline testing).
_URLS: dict[str, str] = {
    "nist_oscal": os.environ.get(
        "DOCTRINE_NIST_OSCAL_URL",
        "https://raw.githubusercontent.com/usnistgov/oscal-content/main/"
        "nist.gov/SP800-53/rev5/json/NIST_SP-800-53_rev5_catalog.json",
    ),
    "attack_stix": os.environ.get(
        "DOCTRINE_ATTACK_STIX_URL",
        "https://raw.githubusercontent.com/mitre/cti/master/"
        "enterprise-attack/enterprise-attack.json",
    ),
    "capec_stix": os.environ.get(
        "DOCTRINE_CAPEC_STIX_URL",
        "https://raw.githubusercontent.com/mitre/cti/master/capec/2.1/stix-capec.json",
    ),
    "cwe_csv_zip": os.environ.get(
        "DOCTRINE_CWE_URL",
        "https://cwe.mitre.org/data/csv/1000.csv.zip",
    ),
    "control_attack_mapping": os.environ.get(
        "DOCTRINE_CONTROL_ATTACK_URL",
        "https://raw.githubusercontent.com/center-for-threat-informed-defense/"
        "attack-control-framework-mappings/main/frameworks/attack_12_1/"
        "nist800_53_r5/stix/nist800-53-r5-mappings.json",
    ),
    # Companion bundle — STIX course-of-action objects for every NIST
    # control (referenced by source_ref in the mapping bundle above).
    "control_stix_bundle": os.environ.get(
        "DOCTRINE_CONTROL_STIX_URL",
        "https://raw.githubusercontent.com/center-for-threat-informed-defense/"
        "attack-control-framework-mappings/main/frameworks/attack_12_1/"
        "nist800_53_r5/stix/nist800-53-r5-controls.json",
    ),
}

_CACHE_FILES: dict[str, str] = {
    "nist_oscal": "nist_80053r5_catalog.json",
    "attack_stix": "enterprise_attack.json",
    "capec_stix": "capec_stix.json",
    "cwe_csv_zip": "cwe_1000.csv.zip",
    "control_attack_mapping": "ctid_control_attack_r5.json",
    "control_stix_bundle": "ctid_controls_r5.json",
}


class DoctrineCorpus:
    """Aggregated structured doctrine, ready for KG write."""

    def __init__(
        self,
        *,
        nodes: list[dict[str, str]],
        edges: list[dict[str, str]],
        source_bytes: bytes,
        counts: dict[str, int],
    ) -> None:
        self.nodes = nodes
        self.edges = edges
        self.source_bytes = source_bytes
        self.counts = counts


def _is_fresh(p: Path) -> bool:
    if not p.is_file():
        return False
    if os.environ.get("HARBOR_DOCTRINE_REFRESH") == "1":
        return False
    return (time.time() - p.stat().st_mtime) < _DEFAULT_TTL_S


async def _fetch_to_cache(name: str, url: str, target: Path) -> bytes:
    target.parent.mkdir(parents=True, exist_ok=True)
    if _is_fresh(target):
        return target.read_bytes()
    try:
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT_S, follow_redirects=True
        ) as client:
            resp = await client.get(
                url, headers={"User-Agent": "harbor-cve-rem/1.0"}
            )
            resp.raise_for_status()
            blob = resp.content
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(blob)
        tmp.replace(target)
        return blob
    except (httpx.HTTPError, OSError) as exc:
        if target.is_file():
            return target.read_bytes()
        raise HarborRuntimeError(
            f"doctrine source {name!r} unreachable and no cache at {target}: "
            f"{type(exc).__name__}: {exc}",
            source=name,
            url=url,
        ) from exc


# ---------------------------------------------------------------------------
# Per-source parsers
# ---------------------------------------------------------------------------


def _parse_nist_controls(blob: bytes) -> dict[str, dict[str, str]]:
    """Walk OSCAL catalog → ``{control_id: {title, family}}``.

    Returns canonical NIST ids (``AC-2``, ``AC-2(1)``) -- OSCAL stores
    them lowercase (``ac-2``, ``ac-2.1``); we re-canonicalize so the
    Cypher MATCH against ``id`` is consistent with how downstream nodes
    address controls.
    """
    catalog = json.loads(blob.decode("utf-8")).get("catalog", {})
    out: dict[str, dict[str, str]] = {}

    def _normalize_id(raw: str) -> str:
        # ``ac-2`` -> ``AC-2``; ``ac-2.1`` -> ``AC-2(1)``.
        s = raw.upper()
        if "." in s:
            base, _, enh = s.partition(".")
            return f"{base}({enh})"
        return s

    def _walk_controls(items: list[dict[str, Any]], family: str) -> None:
        for ctl in items or []:
            cid = _normalize_id(str(ctl.get("id", "")))
            if cid:
                out[cid] = {
                    "id": cid,
                    "title": str(ctl.get("title", "")),
                    "family": family,
                }
            sub = ctl.get("controls") or []
            if sub:
                _walk_controls(sub, family)

    for group in catalog.get("groups", []) or []:
        family = str(group.get("title") or group.get("id", ""))
        _walk_controls(group.get("controls", []) or [], family)
    return out


def _parse_attack_techniques(blob: bytes) -> dict[str, dict[str, str]]:
    """ATT&CK STIX bundle → ``{T-id: {name, kill_chain}}`` (techniques + sub).

    Also extracts a parallel mapping ``technique_stix_id -> attack_id``
    used to resolve CTID control→technique edges from STIX object refs.
    """
    bundle = json.loads(blob.decode("utf-8"))
    out: dict[str, dict[str, str]] = {}
    for obj in bundle.get("objects", []) or []:
        if obj.get("type") != "attack-pattern":
            continue
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        attack_id = ""
        for ref in obj.get("external_references", []) or []:
            if ref.get("source_name") == "mitre-attack":
                attack_id = str(ref.get("external_id") or "")
                break
        if not attack_id:
            continue
        kill_chain_phases = obj.get("kill_chain_phases") or []
        kc = (
            kill_chain_phases[0].get("phase_name", "")
            if kill_chain_phases
            else ""
        )
        out[attack_id] = {
            "id": attack_id,
            "name": str(obj.get("name", "")),
            "kill_chain": kc,
            "stix_id": str(obj.get("id", "")),
        }
    return out


def _parse_capec_relationships(
    blob: bytes,
) -> tuple[
    dict[str, dict[str, str]],
    dict[str, list[str]],  # capec_id -> [cwe_id]
    dict[str, list[str]],  # capec_id -> [attack_id]
]:
    """CAPEC STIX bundle → CAPEC catalog + CAPEC→CWE + CAPEC→ATT&CK.

    Each ``attack-pattern`` STIX object in the CAPEC bundle is a CAPEC.
    Its ``external_references`` carry CAPEC id (``CAPEC-XXX``), CWE refs
    (``source_name=="cwe"``), and ATT&CK Taxonomy Mapping refs
    (``source_name=="ATTACK"`` -- variant capitalizations exist; we
    accept any case-insensitive match).
    """
    bundle = json.loads(blob.decode("utf-8"))
    catalog: dict[str, dict[str, str]] = {}
    capec_to_cwe: dict[str, list[str]] = defaultdict(list)
    capec_to_attack: dict[str, list[str]] = defaultdict(list)
    for obj in bundle.get("objects", []) or []:
        if obj.get("type") != "attack-pattern":
            continue
        if obj.get("revoked"):
            continue
        capec_id = ""
        cwes: list[str] = []
        attacks: list[str] = []
        for ref in obj.get("external_references", []) or []:
            sn = str(ref.get("source_name") or "")
            ext = str(ref.get("external_id") or "")
            sn_lo = sn.lower()
            if sn_lo == "capec" and ext.startswith("CAPEC-"):
                capec_id = ext
            elif sn_lo == "cwe" and ext.startswith("CWE-"):
                cwes.append(ext)
            elif sn_lo == "attack" and ext.startswith("T") and ext[1:2].isdigit():
                attacks.append(ext)
        if not capec_id:
            continue
        catalog[capec_id] = {
            "id": capec_id,
            "name": str(obj.get("name", "")),
        }
        if cwes:
            capec_to_cwe[capec_id].extend(cwes)
        if attacks:
            capec_to_attack[capec_id].extend(attacks)
    return catalog, dict(capec_to_cwe), dict(capec_to_attack)


def _parse_cwe_csv(
    blob: bytes,
) -> tuple[dict[str, dict[str, str]], dict[str, list[str]]]:
    """CWE 1000.csv.zip → ``({CWE-N: {name, abstraction}}, {child: [parents]})``.

    The second return value carries ``ChildOf`` parents from the MITRE
    ``Related Weaknesses`` field (View-1000 abstraction hierarchy). It is
    consumed by :func:`build_doctrine_kg` to propagate Control→CWE coverage
    down the abstraction tree (controls that mitigate the parent CWE also
    mitigate every child — this is the intended use of the View-1000 view).
    """
    out: dict[str, dict[str, str]] = {}
    childof: dict[str, list[str]] = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            return out, childof
        with zf.open(names[0]) as f:
            text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            reader = csv.DictReader(text)
            for row in reader:
                raw_id = str(row.get("CWE-ID", "")).strip()
                if not raw_id:
                    continue
                cwe_id = f"CWE-{raw_id}"
                out[cwe_id] = {
                    "id": cwe_id,
                    "name": str(row.get("Name", "")),
                    "abstraction": str(row.get("Weakness Abstraction", "")),
                }
                # Related Weaknesses field format:
                # ``::NATURE:ChildOf:CWE ID:74:VIEW ID:1000::NATURE:...::``
                # Each parent appears as a NATURE:ChildOf chunk; chunks may
                # repeat the same parent across multiple VIEW IDs, so dedupe.
                rw = row.get("Related Weaknesses", "") or ""
                parents: list[str] = []
                for chunk in rw.split("::"):
                    if "NATURE:ChildOf" not in chunk:
                        continue
                    m = re.search(r"CWE ID:(\d+)", chunk)
                    if not m:
                        continue
                    pid = f"CWE-{m.group(1)}"
                    if pid not in parents:
                        parents.append(pid)
                if parents:
                    childof[cwe_id] = parents
    return out, childof


def _parse_ctid_control_lookup(blob: bytes) -> dict[str, str]:
    """Controls-bundle → ``{course-of-action stix id: NIST id}``.

    CTID's mapping repo splits controls into ``stix/nist800-53-r5-controls.json``
    (one ``course-of-action`` per NIST control with ``external_id`` set to
    the canonical id, e.g. ``AC-2``) and ``stix/nist800-53-r5-mappings.json``
    (relationships only). This builds the resolver dict the mappings
    parser needs to translate ``source_ref`` -> NIST id.
    """
    bundle = json.loads(blob.decode("utf-8"))
    out: dict[str, str] = {}
    for obj in bundle.get("objects", []) or []:
        if obj.get("type") != "course-of-action":
            continue
        sid = str(obj.get("id", ""))
        if not sid:
            continue
        for ref in obj.get("external_references", []) or []:
            ext = str(ref.get("external_id") or "").strip()
            if ext and ext[:2].isalpha() and "-" in ext:
                out[sid] = ext.upper()
                break
    return out


def _parse_control_to_attack(
    blob: bytes,
    technique_stix_to_id: dict[str, str],
    control_stix_to_id: dict[str, str],
) -> list[tuple[str, str]]:
    """CTID 800-53r5→ATT&CK STIX bundle → ``[(control_id, attack_id), ...]``.

    Uses the pre-built lookup tables for both ends:

    * ``control_stix_to_id`` — resolves the source ``course-of-action`` ref
      to its NIST id (built from the companion controls bundle).
    * ``technique_stix_to_id`` — resolves the target ``attack-pattern`` ref
      to its T-id (built from the ATT&CK STIX bundle).
    """
    bundle = json.loads(blob.decode("utf-8"))
    edges: list[tuple[str, str]] = []
    for obj in bundle.get("objects", []) or []:
        if obj.get("type") != "relationship":
            continue
        if obj.get("relationship_type") != "mitigates":
            continue
        ctl_id = control_stix_to_id.get(str(obj.get("source_ref", "")))
        att_id = technique_stix_to_id.get(str(obj.get("target_ref", "")))
        if ctl_id and att_id:
            edges.append((ctl_id, att_id))
    return edges


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


async def build_doctrine_kg() -> DoctrineCorpus:
    """Fetch all five sources, parse, compose, return KG-ready nodes/edges.

    Edges produced:

    * ``Control -[:MAPS_TO]-> CWE``  (transitive Control→ATT&CK→CAPEC→CWE,
      then propagated down the View-1000 ``ChildOf`` abstraction tree so a
      control that mitigates an abstract parent CWE also maps to every
      concrete child)
    * ``Control -[:MITIGATES]-> Attack``
    * ``Attack  -[:RELATES_TO]-> Capec``
    * ``Capec   -[:WEAKNESS]-> CWE``
    * ``CWE     -[:CHILD_OF]-> CWE``  (from CWE View-1000 ``Related Weaknesses``)

    Nodes produced: ``Control``, ``Attack``, ``Capec``, ``CWE``. Labels
    intentionally use ``CWE`` (uppercase) to match the CRITERIA Cypher.
    """
    blobs: dict[str, bytes] = {}
    for key, url in _URLS.items():
        target = _CACHE_ROOT / _CACHE_FILES[key]
        blobs[key] = await _fetch_to_cache(key, url, target)

    controls = _parse_nist_controls(blobs["nist_oscal"])
    attack_techs = _parse_attack_techniques(blobs["attack_stix"])
    capecs, capec_to_cwe, capec_to_attack = _parse_capec_relationships(
        blobs["capec_stix"]
    )
    cwes, cwe_childof = _parse_cwe_csv(blobs["cwe_csv_zip"])

    technique_stix_to_id = {
        v["stix_id"]: k for k, v in attack_techs.items() if v.get("stix_id")
    }
    control_stix_to_id = _parse_ctid_control_lookup(blobs["control_stix_bundle"])
    control_attack_pairs = _parse_control_to_attack(
        blobs["control_attack_mapping"],
        technique_stix_to_id,
        control_stix_to_id,
    )

    # Invert CAPEC→ATT&CK to ATT&CK→CAPEC for the transitive walk.
    attack_to_capec: dict[str, list[str]] = defaultdict(list)
    for cap_id, atts in capec_to_attack.items():
        for att in atts:
            attack_to_capec[att].append(cap_id)

    # Compose Control→CWE via the published chain.
    control_to_cwe: set[tuple[str, str]] = set()
    for ctl_id, att_id in control_attack_pairs:
        for cap_id in attack_to_capec.get(att_id, []):
            for cwe_id in capec_to_cwe.get(cap_id, []):
                if cwe_id in cwes:
                    control_to_cwe.add((ctl_id, cwe_id))
    direct_control_cwe_count = len(control_to_cwe)

    # Propagate Control coverage down the View-1000 ``ChildOf`` tree:
    # a control that mitigates parent CWE-74 (Injection) inherently
    # mitigates child CWE-89 (SQL Injection), CWE-77, CWE-917, etc.
    # The CAPEC STIX bundle only lists Taxonomy_Mappings on ~28% of
    # CAPECs, so the unaugmented chain reaches only ~14% of CWEs;
    # propagation lifts coverage to ~73% without inventing edges.
    parent_to_children: dict[str, list[str]] = defaultdict(list)
    for child, parents in cwe_childof.items():
        for p in parents:
            parent_to_children[p].append(child)

    ctl_to_cwes: dict[str, set[str]] = defaultdict(set)
    for ctl_id, cwe_id in control_to_cwe:
        ctl_to_cwes[ctl_id].add(cwe_id)
    # BFS down from every covered CWE; guard against cycles via the
    # set-membership check on each control's covered CWEs.
    queue: deque[tuple[str, str]] = deque(control_to_cwe)
    while queue:
        ctl_id, cwe_id = queue.popleft()
        for child in parent_to_children.get(cwe_id, []):
            if child not in cwes:
                continue
            if child in ctl_to_cwes[ctl_id]:
                continue
            ctl_to_cwes[ctl_id].add(child)
            control_to_cwe.add((ctl_id, child))
            queue.append((ctl_id, child))

    # Build node + edge dict lists.
    nodes: list[dict[str, str]] = []
    for cid, meta in controls.items():
        nodes.append({"label": "Control", "id": cid, "name": meta["title"]})
    for aid, meta in attack_techs.items():
        nodes.append({"label": "Attack", "id": aid, "name": meta["name"]})
    for cap_id, meta in capecs.items():
        nodes.append({"label": "Capec", "id": cap_id, "name": meta["name"]})
    for cwe_id, meta in cwes.items():
        nodes.append({"label": "CWE", "id": cwe_id, "name": meta["name"]})

    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str, str, str]] = set()

    def _add_edge(sl: str, sv: str, rel: str, tl: str, tv: str) -> None:
        key = (sl, sv, rel, tl, tv)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"sl": sl, "sv": sv, "rel": rel, "tl": tl, "tv": tv})

    for ctl_id, att_id in control_attack_pairs:
        if ctl_id in controls and att_id in attack_techs:
            _add_edge("Control", ctl_id, "MITIGATES", "Attack", att_id)
    for cap_id, atts in capec_to_attack.items():
        if cap_id not in capecs:
            continue
        for att in atts:
            if att in attack_techs:
                _add_edge("Attack", att, "RELATES_TO", "Capec", cap_id)
    for cap_id, cwe_list in capec_to_cwe.items():
        if cap_id not in capecs:
            continue
        for cwe in cwe_list:
            if cwe in cwes:
                _add_edge("Capec", cap_id, "WEAKNESS", "CWE", cwe)
    for ctl_id, cwe_id in control_to_cwe:
        _add_edge("Control", ctl_id, "MAPS_TO", "CWE", cwe_id)
    # CWE abstraction tree (View 1000 ChildOf). Keeps the parent edge
    # explicit in the graph so downstream queries can walk it directly
    # instead of re-deriving from the propagation above.
    for child, parents in cwe_childof.items():
        if child not in cwes:
            continue
        for p in parents:
            if p in cwes:
                _add_edge("CWE", child, "CHILD_OF", "CWE", p)

    # Source bytes for sha256: chain version tag + concatenated raw cached
    # blobs in fixed order. Same upstream files + same chain → same sha →
    # bootgate idempotency stays sound; bumping _CHAIN_VERSION forces a
    # rebuild even when upstream sources are unchanged.
    source_bytes = b"\n".join(
        [_CHAIN_VERSION] + [blobs[k] for k in sorted(_URLS.keys())]
    )

    counts = {
        "controls": len(controls),
        "attack_techniques": len(attack_techs),
        "capecs": len(capecs),
        "cwes": len(cwes),
        "control_attack_edges": len(control_attack_pairs),
        "control_cwe_edges_direct": direct_control_cwe_count,
        "control_cwe_edges_materialized": len(control_to_cwe),
        "cwe_childof_edges": sum(
            1 for child, ps in cwe_childof.items()
            if child in cwes for p in ps if p in cwes
        ),
    }
    return DoctrineCorpus(
        nodes=nodes,
        edges=edges,
        source_bytes=source_bytes,
        counts=counts,
    )
