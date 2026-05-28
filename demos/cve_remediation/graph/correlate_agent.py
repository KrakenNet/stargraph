# SPDX-License-Identifier: Apache-2.0
"""CMDB CorrelateAgent — CPE list → affected hosts via @tool callables.

Replaces the hand-iterated regex bash in
``CorrelateAssetsBrokerNode._cmdb_traverse`` for the path where the
advisory carries a CPE 2.3 URI list. Behavior:

1. Enumerate unique ``(vendor, product)`` pairs across the CPE list.
   Each pair is the agent's "lead" — the vendor narrows nameLIKE noise,
   the product seeds variant expansion.
2. For each lead, fan-out a small search plan:
   - vendor-narrowed ``cmdb_query_software`` per :func:`_derive_cpe_variants`
   - fall back to vendor-free ``cmdb_query_software`` when nothing scored.
3. Score every returned row with the existing
   :func:`_score_cmdb_candidate` heuristic; keep only ``high``/``medium``
   candidates with Runs-on topology.
4. Walk Runs-on per surviving Software CI, batch-resolve host names,
   union across all leads.

Returns the same envelope shape ``_cmdb_traverse`` does, plus a
per-lead ``correlate_agent_trace`` audit chain so the rule pack /
broker layer can replay or quarantine decisions.

The CMDB calls go through the harbor ``@tool`` callables
(``servicenow.cmdb_query_software`` etc.). Routing through
``nautilus.broker_request`` is a follow-up (task #14) — once the rule
pack ships, swap each call for ``broker.arequest(intent=..., tool=...)``
without touching this agent.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from harbor.tools.servicenow import (
    cmdb_query_software,
    cmdb_resolve_hosts,
    cmdb_traverse_runs_on,
)

log = logging.getLogger(__name__)


# CPE 2.3 URI: cpe:2.3:{part}:{vendor}:{product}:{version}:...
_CPE_RE = re.compile(
    r"^cpe:2\.3:[aoh]:(?P<vendor>[^:]+):(?P<product>[^:]+):"
)


_VER_SPLIT = re.compile(r"[^A-Za-z0-9]+")


def _ver_tuple(v: str) -> tuple:
    """Normalize a version string into a lex-comparable tuple.

    Splits on every non-alphanumeric run, then tags each chunk as
    numeric (tag 0) or alpha (tag 1) so that ``2.4.60 < 2.4.60a``
    (numeric < same-prefix-with-alpha-suffix). Handles semver,
    Microsoft 4-part build numbers, Cisco IOS ``15.6(2)T``,
    Microsoft 10.0.10240.21013, and similar — anything where a
    numeric-first tuple compare gives a sensible order.

    Returns ``()`` on empty / wildcard input.
    """
    s = (v or "").strip().lower()
    if not s or s == "*":
        return ()
    out: list[tuple[int, int | str]] = []
    for part in _VER_SPLIT.split(s):
        if not part:
            continue
        try:
            out.append((0, int(part)))
        except ValueError:
            out.append((1, part))
    return tuple(out)


def version_in_range(
    install_version: str,
    *,
    affected_version_ranges: list[dict[str, str]] | None = None,
    exact_affected_versions: list[str] | None = None,
    fixed_version: str = "",
) -> bool:
    """Return True if ``install_version`` is in the advisory's affected set.

    Truth table:

    * ``install_version`` empty / ``"*"`` → True (unknown host version,
      keep — preserves pre-filter behavior; no regression).
    * No constraints supplied → True (advisory has no version data;
      product-name match is the only signal we have).
    * ``exact_affected_versions`` contains the install version (string
      equality, case-folded) → True.
    * Any ``affected_version_ranges`` row covers the install version
      (NVD-style startIncluding / startExcluding / endIncluding /
      endExcluding semantics) → True.
    * Otherwise → False, but if ``fixed_version`` is set and install is
      strictly < fixed, True (catches CVEs where the only NVD signal is
      ``versionEndExcluding``).
    """
    iv = (install_version or "").strip()
    if not iv or iv == "*":
        return True
    have_ranges = bool(affected_version_ranges)
    have_exact = bool(exact_affected_versions)
    have_fixed = bool(fixed_version)
    if not (have_ranges or have_exact or have_fixed):
        return True
    iv_lc = iv.lower()
    for v in (exact_affected_versions or []):
        if str(v).strip().lower() == iv_lc:
            return True
    iv_t = _ver_tuple(iv)
    for row in (affected_version_ranges or []):
        si = (row.get("versionStartIncluding") or "").strip()
        se = (row.get("versionStartExcluding") or "").strip()
        ei = (row.get("versionEndIncluding") or "").strip()
        ee = (row.get("versionEndExcluding") or "").strip()
        if not (si or se or ei or ee):
            continue
        if si and iv_t < _ver_tuple(si):
            continue
        if se and iv_t <= _ver_tuple(se):
            continue
        if ei and iv_t > _ver_tuple(ei):
            continue
        if ee and iv_t >= _ver_tuple(ee):
            continue
        return True
    if have_fixed:
        ft = _ver_tuple(fixed_version)
        if ft and iv_t < ft:
            return True
    return False


def extract_vendor_product_pairs(cpe_uris: list[str]) -> list[tuple[str, str]]:
    """Pull deduped ``(vendor, product)`` pairs out of a CPE URI list.

    ``"*"`` / ``"-"`` / empty fields are dropped; case-folded; order
    preserves first-seen so the agent's first hit is the most-cited
    pair, not an arbitrary later one.
    """
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for uri in cpe_uris or []:
        m = _CPE_RE.match(str(uri or ""))
        if not m:
            continue
        vendor = m.group("vendor").strip().lower()
        product = m.group("product").strip().lower()
        if vendor in ("", "*", "-") or product in ("", "*", "-"):
            continue
        key = (vendor, product)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(key)
    return pairs


@dataclass
class CorrelateAgentTrace:
    """One lead's full audit chain — input pair, queries tried, hits."""

    vendor: str
    product: str
    variants_tried: list[str] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    matched_software_sys_id: str = ""
    matched_software_name: str = ""
    matched_score: int = 0
    matched_quality: str = "miss"
    host_sys_ids: list[str] = field(default_factory=list)
    host_names: list[str] = field(default_factory=list)
    error: str = ""


# Minimum composite score for a CMDB Software CI to be treated as a
# real match (mirrors `_MIN_HIGH_CONF_SCORE` in real_nodes._cmdb_traverse).
_MIN_HIGH_CONF_SCORE = 60

# Hard cap on (vendor, product) leads explored per advisory. NVD
# advisories that enumerate every device SKU (CVE-2017-3881 emits 323
# Cisco Catalyst products) generate thousands of CMDB queries — the
# agent hits a 35-min wallclock long after the first lead has surfaced
# the matching CI. The first ~30 leads cover every real CMDB-name
# variant; the rest are SKU noise that CMDB doesn't carry.
_MAX_LEADS = 30
# Early-exit threshold: once this many consecutive leads return zero
# new high-confidence candidates AND at least one earlier lead matched,
# stop. Protects multi-CI advisories (log4j-core + log4j-api with
# disjoint host sets) while still capping runaway SKU enumerations.
_NO_NEW_HITS_STREAK = 10


async def correlate_hosts_from_cpes(
    *,
    cpe_uris: list[str],
    candidate_products: list[str] | None = None,
    score_candidate,  # injected: (row, vendor, product, extra_aliases) -> (int, str)
    derive_variants,  # injected: (token, vendor) -> list[str]
    affected_version_ranges: list[dict[str, str]] | None = None,
    exact_affected_versions: list[str] | None = None,
    fixed_version: str = "",
    install_channel: str = "",
) -> dict[str, Any]:
    """Run the CorrelateAgent against a CPE list.

    Parameters
    ----------
    cpe_uris
        Advisory's CPE 2.3 URI list. Empty list → agent is a no-op
        (returns an empty envelope with ``status="no_cpes"``).
    candidate_products
        Optional fallback when the URI list is empty / unparseable —
        falls back to the regex-derived product list so legacy fixtures
        still resolve.
    score_candidate, derive_variants
        Injected from ``real_nodes`` so the agent reuses the same
        scoring + variant expansion the legacy path used. Keeps the
        agent free of a hard import from ``real_nodes`` (avoids a
        circular dep when ``real_nodes`` calls the agent).

    Returns
    -------
    dict[str, Any]
        ``{"status": "ok", "host_sys_ids": [...], "host_names": [...],
        "name_by_sys_id": {...}, "software_sys_id": "...",
        "software_name": "...", "score": int, "quality": str,
        "traces": [CorrelateAgentTrace, ...]}``.
        ``status="no_cpes"`` when nothing was parseable.
    """
    pairs = extract_vendor_product_pairs(cpe_uris)
    if not pairs and candidate_products:
        # Legacy fallback: synth (vendor="", product) pairs from regex extracts
        pairs = [("", p.strip().lower()) for p in candidate_products if (p or "").strip()]

    if not pairs:
        return {
            "status": "no_cpes",
            "host_sys_ids": [],
            "host_names": [],
            "name_by_sys_id": {},
            "software_sys_id": "",
            "software_name": "",
            "score": 0,
            "quality": "miss",
            "traces": [],
        }

    all_host_sys_ids: set[str] = set()
    best_software_sys_id = ""
    best_software_name = ""
    best_score = 0
    best_quality = "miss"
    traces: list[CorrelateAgentTrace] = []
    any_match_seen = False
    no_new_streak = 0

    capped_pairs = pairs[:_MAX_LEADS]
    for vendor, product in capped_pairs:
        if any_match_seen and no_new_streak >= _NO_NEW_HITS_STREAK:
            break
        hosts_before = len(all_host_sys_ids)
        tr = CorrelateAgentTrace(vendor=vendor, product=product)
        traces.append(tr)
        variants = derive_variants(product, vendor)
        if not variants:
            variants = [product]

        # Pass 1: each variant with vendor narrowing.
        # Pass 2: re-try with vendor="" if pass 1 scored nothing high.
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for variant in variants:
            if variant in tr.variants_tried:
                continue
            tr.variants_tried.append(variant)
            try:
                res = await cmdb_query_software(
                    name_like=variant, vendor=vendor, limit=25,
                )
            except Exception as exc:  # noqa: BLE001 — fail loud per-lead
                tr.error = f"{type(exc).__name__}: {exc}"
                continue
            for row in res.get("rows", []) or []:
                # Score each row against every variant; keep the max.
                # CPE products carry version suffixes (``windows_10_1507``)
                # that dilute token coverage against shorter CMDB names
                # (``Windows``). Re-scoring across each variant lets the
                # prefix variants (``windows``) recover full coverage.
                best_s, best_q = 0, "reject"
                for cand_term in variants:
                    s, q = score_candidate(row, vendor, cand_term, variants)
                    if s > best_s:
                        best_s, best_q = s, q
                scored.append((best_s, best_q, row))

        if not any(s >= _MIN_HIGH_CONF_SCORE for s, _q, _r in scored) and vendor:
            # Vendor-narrowed search came back weak — retry vendor-free.
            for variant in list(tr.variants_tried):
                try:
                    res = await cmdb_query_software(
                        name_like=variant, vendor="", limit=25,
                    )
                except Exception as exc:  # noqa: BLE001
                    tr.error = f"{type(exc).__name__}: {exc}"
                    continue
                for row in res.get("rows", []) or []:
                    best_s, best_q = 0, "reject"
                    for cand_term in variants:
                        s, q = score_candidate(row, vendor, cand_term, variants)
                        if s > best_s:
                            best_s, best_q = s, q
                    scored.append((best_s, best_q, row))

        # Pass 3: vendor-prefixed name search. CMDB CIs commonly carry
        # the vendor in the display name ("Microsoft Windows", "Cisco
        # IOS XE", "Apple iOS") even when the ``vendor`` column is
        # blank — vendor-narrowed pass returns nothing, vendor-free
        # nameLIKE=product drowns in unrelated rows. Prepending the
        # vendor token to the name_like surfaces the right CI.
        prefix_tokens: list[str] = []
        if vendor:
            prefix_tokens.append(vendor.replace("_", " ").replace("-", " "))
        # Also prepend ``install_channel`` (pip / npm / rubygems / apt /
        # maven / gem). When the CMDB seeds Software CIs as
        # ``{channel} {package}`` (e.g. "npm tar", "PyPI urllib3"), the
        # CPE-derived vendor (e.g. "isaacs") doesn't match the seed
        # name -- the channel does. Skip when channel already equals
        # vendor so we don't double-query.
        if install_channel:
            ch_display = install_channel.replace("_", " ").lower()
            if ch_display and ch_display not in {t.lower() for t in prefix_tokens}:
                prefix_tokens.append(ch_display)
        for prefix in prefix_tokens:
            for variant in list(tr.variants_tried):
                composed = f"{prefix} {variant}".strip()
                if composed in tr.variants_tried:
                    continue
                tr.variants_tried.append(composed)
                try:
                    res = await cmdb_query_software(
                        name_like=composed, vendor="", limit=25,
                    )
                except Exception as exc:  # noqa: BLE001
                    tr.error = f"{type(exc).__name__}: {exc}"
                    continue
                for row in res.get("rows", []) or []:
                    best_s, best_q = 0, "reject"
                    for cand_term in variants:
                        s, q = score_candidate(row, vendor, cand_term, variants)
                        if s > best_s:
                            best_s, best_q = s, q
                    scored.append((best_s, best_q, row))

        if not scored:
            continue
        # Dedup by sys_id (a Software CI can match multiple variants in
        # the same lead — vendor-narrowed + vendor-free passes also
        # double-count). Keep the highest score per sys_id.
        best_per_sys: dict[str, tuple[int, str, dict[str, Any]]] = {}
        for s, q, row in scored:
            sid = str(row.get("sys_id", "")).strip()
            if not sid:
                continue
            prev = best_per_sys.get(sid)
            if prev is None or s > prev[0]:
                best_per_sys[sid] = (s, q, row)
        scored = list(best_per_sys.values())
        # Best-quality first; ties broken by score.
        scored.sort(key=lambda t: -t[0])
        tr.candidates = [
            {"sys_id": str(r.get("sys_id", "")), "name": str(r.get("name", "")),
             "vendor": str(r.get("vendor", "")), "version": str(r.get("version", "")),
             "score": s, "quality": q}
            for s, q, r in scored[:5]
        ]

        # Walk Runs-on for EVERY high-conf candidate, not just the
        # first applicable. CMDB commonly carries multiple Software CIs
        # named for the same library (e.g. "Apache Log4j" + "Apache
        # Log4j 2") with disjoint Runs-on sets; first-applicable would
        # drop the union. Per-lead trace records the first CI that
        # surfaced hosts (for audit) while ``all_host_sys_ids`` unions
        # across every walked candidate.
        per_lead_hosts: list[str] = []
        for s, q, row in scored:
            if s < _MIN_HIGH_CONF_SCORE:
                break
            sys_id = str(row.get("sys_id", "")).strip()
            if not sys_id:
                continue
            try:
                rel = await cmdb_traverse_runs_on(parent_sys_id=sys_id, limit=200)
            except Exception as exc:  # noqa: BLE001
                tr.error = f"{type(exc).__name__}: {exc}"
                continue
            raw_child_ids = rel.get("child_sys_ids") or []
            install_by_sid = rel.get("install_version_by_sys_id") or {}
            # Version filter: when the advisory carries version
            # constraints AND the relationship row carries the host's
            # install_version, drop children whose installed version
            # falls outside the affected range. Hosts without an
            # install_version stay (no info → can't exclude).
            child_ids: list[str] = []
            for cid in raw_child_ids:
                iv = install_by_sid.get(cid, "")
                if version_in_range(
                    iv,
                    affected_version_ranges=affected_version_ranges,
                    exact_affected_versions=exact_affected_versions,
                    fixed_version=fixed_version,
                ):
                    child_ids.append(cid)
            if not child_ids:
                continue
            if not tr.matched_software_sys_id:
                # First CI to yield hosts is the trace's "matched" entry.
                tr.matched_software_sys_id = sys_id
                tr.matched_software_name = str(row.get("name", ""))
                tr.matched_score = s
                tr.matched_quality = q
            per_lead_hosts.extend(child_ids)
            all_host_sys_ids.update(child_ids)
            if s > best_score:
                best_score = s
                best_quality = q
                best_software_sys_id = sys_id
                best_software_name = str(row.get("name", ""))
        tr.host_sys_ids = sorted(set(per_lead_hosts))
        if len(all_host_sys_ids) > hosts_before:
            any_match_seen = True
            no_new_streak = 0
        elif any_match_seen:
            no_new_streak += 1

    # Single batched resolve across every host the agent surfaced.
    name_by_sys_id: dict[str, str] = {}
    host_names_sorted: list[str] = []
    if all_host_sys_ids:
        try:
            resolved = await cmdb_resolve_hosts(
                sys_ids=sorted(all_host_sys_ids),
            )
            name_by_sys_id = dict(resolved.get("name_by_sys_id") or {})
            host_names_sorted = list(resolved.get("host_names") or [])
        except Exception as exc:  # noqa: BLE001 — propagate to caller via empty names
            log.warning("cmdb_resolve_hosts failed: %s", exc)

    # Backfill per-trace host_names from the batch result.
    for tr in traces:
        if tr.host_sys_ids:
            tr.host_names = sorted({
                name_by_sys_id.get(sid, "") for sid in tr.host_sys_ids
                if name_by_sys_id.get(sid)
            })

    return {
        "status": "ok",
        "host_sys_ids": sorted(all_host_sys_ids),
        "host_names": host_names_sorted,
        "name_by_sys_id": name_by_sys_id,
        "software_sys_id": best_software_sys_id,
        "software_name": best_software_name,
        "score": best_score,
        "quality": best_quality,
        "traces": traces,
    }


__all__ = [
    "CorrelateAgentTrace",
    "correlate_hosts_from_cpes",
    "extract_vendor_product_pairs",
    "version_in_range",
]
