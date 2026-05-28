# SPDX-License-Identifier: Apache-2.0
"""``cve.fetch_advisory`` -- demo-local CVE intake against NVD JSON 2.0.

This is a *demo* tool, not a framework tool. It lives under
``demos/cve_remediation/tools/`` because CVE-shaped intake is specific
to this demo; the Harbor framework only publishes tools that are useful
across demos (e.g. ``harbor.tools.servicenow.create_change_request``).
The decorator and registry it uses (``harbor.tools.decorator.tool``)
are framework surfaces -- this tool just consumes them, the same way a
third-party plugin would.

The NVD CVE 2.0 API is the canonical source for CVE metadata. Free, no
auth required for low rates (5 req / 30s without an API key, 50 req /
30s with one), stable. This tool issues a single GET to
``https://services.nvd.nist.gov/rest/json/cves/2.0?cveId=<id>``,
parses the response into a flat dict the demo's Phase-1 nodes can
consume, and returns:

* ``cve_id`` -- canonicalized id (NVD echoes whatever case you sent).
* ``description`` -- English description (canonical body for Phase-1
  canonicalization).
* ``url`` -- canonical NVD source URL for this CVE.
* ``source_class`` -- ``"trusted"`` (NVD is in the demo's trusted source
  table; see ``demos/cve_remediation/cve-rem-graph.md`` line 108).
* ``cvss`` -- base score (float; v3.1 preferred, falls back to v2).
* ``cwe`` -- primary CWE id, e.g. ``"CWE-306"``.
* ``kev_listed`` -- ``True`` when the CVE is in CISA KEV.
* ``epss`` -- exploit prediction score (0.0 if unknown -- this endpoint
  doesn't return EPSS, the EnrichCveTrustedNode merges it from the EPSS
  feed).
* ``vendor`` / ``product`` -- best-effort extracted from the first
  ``cpeMatch`` entry's CPE 2.3 string.
* ``references`` -- list of ``{url, tags}`` pulled verbatim.
* ``raw`` -- the upstream JSON envelope so downstream nodes that need
  fields not in the flat projection can still get to them.

Intentionally **read-only**: ``side_effects=SideEffects.read`` →
``replay_policy=ReplayPolicy.recorded_result``. Cassette layer (FR-7)
covers replay.

Capability: ``tools:cve:fetch_advisory`` (so the registry filter can
restrict graphs that shouldn't have outbound network access).
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from harbor.errors import HarborRuntimeError
from harbor.tools.decorator import tool
from harbor.tools.spec import SideEffects

__all__ = ["fetch_advisory"]


_NAMESPACE = "cve"
_NAME = "fetch_advisory"
_VERSION = "1"
_REQUIRED_CAPABILITY = "tools:cve:fetch_advisory"
_DEFAULT_TIMEOUT_S = 15.0
_NVD_TIMEOUT_S = 30.0
_NVD_BASE = os.environ.get("NVD_BASE_URL", "https://services.nvd.nist.gov/rest/json/cves/2.0")
_API_KEY = os.environ.get("NVD_API_KEY", "").strip()

_CIRCUIT_BREAKER_COUNTER: dict[str, int] = {}
_CIRCUIT_BREAKER_OPENED_AT: dict[str, float] = {}
_CIRCUIT_BREAKER_THRESHOLD = 5
_CIRCUIT_BREAKER_COOLDOWN_S = 60.0


def _cb_host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _cb_check(host: str) -> None:
    if not host:
        return
    count = _CIRCUIT_BREAKER_COUNTER.get(host, 0)
    if count < _CIRCUIT_BREAKER_THRESHOLD:
        return
    opened_at = _CIRCUIT_BREAKER_OPENED_AT.get(host, 0.0)
    if (time.monotonic() - opened_at) >= _CIRCUIT_BREAKER_COOLDOWN_S:
        _CIRCUIT_BREAKER_COUNTER[host] = 0
        _CIRCUIT_BREAKER_OPENED_AT.pop(host, None)
        return
    raise RuntimeError(f"circuit breaker open for {host}; cooling down")


def _cb_record_failure(host: str) -> None:
    if not host:
        return
    new_count = _CIRCUIT_BREAKER_COUNTER.get(host, 0) + 1
    _CIRCUIT_BREAKER_COUNTER[host] = new_count
    if new_count >= _CIRCUIT_BREAKER_THRESHOLD and host not in _CIRCUIT_BREAKER_OPENED_AT:
        _CIRCUIT_BREAKER_OPENED_AT[host] = time.monotonic()


def _cb_record_success(host: str) -> None:
    if not host:
        return
    _CIRCUIT_BREAKER_COUNTER[host] = 0
    _CIRCUIT_BREAKER_OPENED_AT.pop(host, None)


async def _retry_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_attempts: int = 4,
    base_delay: float = 0.5,
    **kwargs: Any,
) -> httpx.Response:
    """GET with exponential backoff + circuit breaker on persistent failures.

    Retries on ``ReadTimeout`` / ``ConnectTimeout`` / ``RemoteProtocolError`` /
    5xx / 429. Returns immediately on 4xx (other than 429). On final
    failure, increments the per-host circuit breaker and re-raises with
    a message that includes "after {N} retries" so the retro detector
    can categorize. When the breaker is open, raises ``RuntimeError``
    with "circuit breaker open" before any network call.
    """
    host = _cb_host(url)
    _cb_check(host)
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        try:
            resp = await client.get(url, **kwargs)
        except (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.RemoteProtocolError,
        ) as exc:
            last_exc = exc
        else:
            if 500 <= resp.status_code < 600 or resp.status_code == 429:
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code} from {url} after {attempt + 1} retries",
                    request=resp.request,
                    response=resp,
                )
            else:
                _cb_record_success(host)
                return resp
        if attempt < max_attempts - 1:
            delay = base_delay * (2 ** attempt) + random.uniform(0, 0.25)
            await asyncio.sleep(delay)
    _cb_record_failure(host)
    if last_exc is not None:
        raise RuntimeError(
            f"{type(last_exc).__name__}: {last_exc} after {max_attempts} retries"
        ) from last_exc
    raise RuntimeError(f"GET {url} failed after {max_attempts} retries")


def _english_description(item: dict[str, Any]) -> str:
    for d in item.get("descriptions", []):
        if d.get("lang") == "en" and d.get("value"):
            return str(d["value"])
    return ""


def _primary_cwe(item: dict[str, Any]) -> str:
    for w in item.get("weaknesses", []):
        if w.get("type") == "Primary":
            for d in w.get("description", []):
                v = d.get("value", "")
                if v.startswith("CWE-"):
                    return v
    # Fall back to the first weakness if Primary isn't tagged.
    for w in item.get("weaknesses", []):
        for d in w.get("description", []):
            v = d.get("value", "")
            if v.startswith("CWE-"):
                return v
    return ""


def _cvss_base_score(item: dict[str, Any]) -> float:
    metrics = item.get("metrics", {})
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        rows = metrics.get(key, [])
        if rows:
            data = rows[0].get("cvssData", {})
            score = data.get("baseScore")
            if score is not None:
                return float(score)
    return 0.0


def _all_cpe_uris(item: dict[str, Any]) -> list[str]:
    """Return every distinct CPE 2.3 ``criteria`` URI from the CVE.

    Feeds :func:`cmdb_substrate.derive_substrate_profile_from_cpes` so
    substrate applicability is decided mechanically from NVD CPE rows,
    not a hand-authored ``(vendor, product)`` table.
    """
    seen: set[str] = set()
    out: list[str] = []
    for cfg in item.get("configurations", []):
        for node in cfg.get("nodes", []):
            for m in node.get("cpeMatch", []):
                uri = str(m.get("criteria", "") or "")
                if not uri or uri in seen:
                    continue
                seen.add(uri)
                out.append(uri)
    return out


def _all_cpe_products(item: dict[str, Any]) -> list[tuple[str, str]]:
    """Return every distinct ``(vendor, product)`` from the CVE's CPE list.

    NVD lists CVEs against every product that bundles the vulnerable
    component (Log4Shell, for example, has 166 CPE rows); first-match-
    wins drops the actual upstream package on the floor for any CVE
    whose CPEs span multiple vendors. The correlation step iterates
    this list against CMDB until one product hits.
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for cfg in item.get("configurations", []):
        for node in cfg.get("nodes", []):
            for m in node.get("cpeMatch", []):
                parts = str(m.get("criteria", "")).split(":")
                if len(parts) < 5:
                    continue
                pair = (parts[3], parts[4])
                if pair in seen:
                    continue
                seen.add(pair)
                out.append(pair)
    return out


def _version_key(v: str) -> tuple[int, ...]:
    """Coerce ``v`` to a tuple of ints for ordering.

    Best-effort: any non-numeric segment becomes ``0``. Empty string ⇒
    ``(0,)``. Sufficient for picking the *latest* ``versionEndExcluding``
    across CPE rows; not a full PEP 440 / SemVer comparator.
    """
    if not v:
        return (0,)
    out: list[int] = []
    for seg in v.split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        out.append(int(digits) if digits else 0)
    return tuple(out)


def _extract_version_data(
    item: dict[str, Any], primary_product: str
) -> tuple[list[dict[str, str]], str, list[str]]:
    """Pull affected-version ranges + the latest-fix version from CPE matches.

    Reads ``cpeMatch[]`` rows whose ``vulnerable=True`` and groups by
    product. Returns:

    * ``ranges`` - list of ``{product, start_inc, start_exc, end_inc,
      end_exc, exact}`` dicts, one per cpeMatch row.
    * ``fixed_version`` - the **largest** ``versionEndExcluding`` seen
      across rows for the primary product. Picking the latest matters
      for vulns with multiple recommended fix versions (Log4Shell has
      2.3.1 / 2.12.2 / 2.12.3 / 2.15.0 / 2.16.0 / 2.17.0 / 2.17.1; we
      want 2.17.1 -- the most recent recommended fix). Falls back to
      ``versionEndIncluding`` when no exclusive bound is given.
    * ``exact_affected`` - sorted list of exact version strings parsed
      from CPE 2.3 ``criteria`` (the 6th colon-segment), filtered to
      ones that aren't ``*`` / ``-``. Caller uses this as the
      "known-bad" set for classification.
    """
    ranges: list[dict[str, str]] = []
    end_exc_versions: list[str] = []
    end_inc_versions: list[str] = []
    start_inc_versions: list[str] = []
    exact_affected: list[str] = []
    target = (primary_product or "").lower()
    for cfg in item.get("configurations", []):
        for node in cfg.get("nodes", []):
            for m in node.get("cpeMatch", []):
                if not m.get("vulnerable", False):
                    continue
                criteria = str(m.get("criteria", ""))
                parts = criteria.split(":")
                prod = parts[4] if len(parts) > 4 else ""
                exact = parts[5] if len(parts) > 5 else ""
                row = {
                    "product": prod,
                    "start_inc": str(m.get("versionStartIncluding", "") or ""),
                    "start_exc": str(m.get("versionStartExcluding", "") or ""),
                    "end_inc": str(m.get("versionEndIncluding", "") or ""),
                    "end_exc": str(m.get("versionEndExcluding", "") or ""),
                    "exact": exact if exact and exact not in ("*", "-") else "",
                }
                ranges.append(row)
                if prod.lower() == target:
                    if row["end_exc"]:
                        end_exc_versions.append(row["end_exc"])
                    if row["end_inc"]:
                        end_inc_versions.append(row["end_inc"])
                    if row["start_inc"]:
                        start_inc_versions.append(row["start_inc"])
                    if row["exact"]:
                        exact_affected.append(row["exact"])
    fixed_version = ""
    # Only ``versionEndExcluding`` gives a definitive fix version per
    # NVD semantics ("vulnerable when v < X" ⇒ fixed in X). A bare
    # ``versionEndIncluding`` means "everything up to AND including X
    # is affected" -- the fix lives somewhere above X but NVD doesn't
    # tell us where, so we leave it empty rather than guess.
    if end_exc_versions:
        fixed_version = max(end_exc_versions, key=_version_key)
    # Build the affected-version set: exact CPE-listed versions PLUS
    # any ``versionStartIncluding`` values (they define the lower edge
    # of the vulnerable range and are themselves affected by definition).
    # Probe layer uses [0] for the vulnerable phase and the full set for
    # observed-status classification.
    combined_affected = sorted(set(exact_affected) | set(start_inc_versions))
    return ranges, fixed_version, combined_affected


_CHANNEL_HOSTS: tuple[tuple[str, str], ...] = (
    # High-confidence package registries -- match these against ALL
    # references first regardless of advisory order, because the upstream
    # registry is a better "where to install from" answer than the GitHub
    # advisory-database link that often appears earlier.
    ("pypi.org",                      "pip"),
    ("files.pythonhosted.org",        "pip"),
    ("repo1.maven.org",               "maven"),
    ("search.maven.org",              "maven"),
    ("mvnrepository.com",             "maven"),
    ("logging.apache.org",            "maven"),
    ("security.debian.org",           "apt"),
    ("tracker.debian.org",            "apt"),
    ("ubuntu.com/security",           "apt"),
    ("usn.ubuntu.com",                "apt"),
    ("access.redhat.com/security",    "rpm"),
    ("registry.npmjs.org",            "npm"),
    ("rubygems.org",                  "rubygems"),
    ("crates.io",                     "cargo"),
    ("pkg.go.dev",                    "go"),
)
# Last-resort: GitHub advisory pages tell us "this CVE exists" but
# don't tell us how to install. Only used when no high-confidence
# registry reference is present.
_FALLBACK_CHANNEL_HOSTS: tuple[tuple[str, str], ...] = (
    ("github.com", "github"),
)


def _derive_install_channel(references: list[dict[str, Any]]) -> str:
    """Pick the package-installation channel from the reference URLs.

    Two-pass scan: first match against the high-confidence registry
    list (PyPI, Maven Central, Debian/Ubuntu/RHEL trackers, etc.);
    only fall back to advisory-database hosts (GitHub) if no registry
    match. Returns empty string when no reference points at an
    installable channel -- probe layer treats that as "no install
    path, escalate to HITL".
    """
    for needles, _ in [(_CHANNEL_HOSTS, None), (_FALLBACK_CHANNEL_HOSTS, None)]:
        for r in references:
            url = str(r.get("url", "") or "").lower()
            for needle, channel in needles:
                if needle in url:
                    return channel
    return ""


# OSV ecosystem → our channel slug. Mapping derived from OSV's documented
# ecosystem set (https://ossf.github.io/osv-schema/#ecosystem-field), not
# guessed -- one row per upstream ecosystem name.
_OSV_ECOSYSTEM_MAP: dict[str, str] = {
    "PyPI":           "pip",
    "Maven":          "maven",
    "npm":            "npm",
    "RubyGems":       "rubygems",
    "Go":             "go",
    "crates.io":      "cargo",
    "Packagist":      "composer",
    "NuGet":          "nuget",
    "Hex":            "hex",
    "Pub":            "pub",
    "Hackage":        "cabal",
    "CRAN":           "cran",
    "SwiftURL":       "swift",
    "Debian":         "apt",
    "Ubuntu":         "apt",
    "Alpine":         "apk",
    "Rocky Linux":    "rpm",
    "AlmaLinux":      "rpm",
    "Red Hat":        "rpm",
    "openSUSE":       "rpm",
    "SUSE":           "rpm",
    "GitHub Actions": "github_actions",
}


def _detect_withdrawn(envelope: dict[str, Any]) -> str:
    """Identify OSV advisories with no patch available.

    Two signals:

    * Top-level ``withdrawn`` timestamp on the OSV record (the
      advisory itself was retracted; rare).
    * No ``fixed`` event anywhere in the affected ranges -- the
      vulnerability has no published patch (e.g. CVE-2024-3094 xz
      backdoor: maintainers withdrew the tarballs rather than ship a
      fix). The pipeline should route to mitigation_only HITL with
      this status rather than silently skip.
    """
    if envelope.get("withdrawn"):
        return "withdrawn"
    has_any_fix = False
    for affected in envelope.get("affected", []) or []:
        for r in affected.get("ranges", []) or []:
            for ev in r.get("events", []) or []:
                if "fixed" in ev and str(ev["fixed"]):
                    has_any_fix = True
                    break
            if has_any_fix:
                break
        if has_any_fix:
            break
    if not has_any_fix and envelope.get("affected"):
        return "no_fix_published"
    return ""


def _scan_osv_affected(envelope: dict[str, Any]) -> dict[str, str]:
    """Walk an OSV vuln envelope and return the recognized ecosystem.

    Aggregates across ALL ``affected`` rows for the same ecosystem
    (Log4Shell has 7 affected rows -- one per maintained 2.x branch
    each with its own ``fixed`` event, e.g. 2.3.1 / 2.12.2 / 2.12.3 /
    2.15.0 / 2.16.0 / 2.17.0 / 2.17.1). We:

    * Pick the channel + package from the first row whose ecosystem
      we know (no row count ambiguity -- they're all the same package).
    * Take the **largest** ``fixed`` event by version key (so log4j
      lands on 2.17.1, not 2.7.0).
    * Take the **smallest** ``introduced`` event (the lower bound of
      the affected range, used by the probe as the vulnerable version
      to install).

    Returns ``{ecosystem, channel, package, fix, introduced}`` -- all
    empty when no row carries a mapped ecosystem.
    """
    ecosystem = ""
    channel = ""
    package = ""
    fix_candidates: list[str] = []
    intro_candidates: list[str] = []
    for affected in envelope.get("affected", []) or []:
        pkg = affected.get("package") or {}
        eco = str(pkg.get("ecosystem", "") or "")
        name = str(pkg.get("name", "") or "")
        eco_root = eco.split(":")[0]
        ch = (
            _OSV_ECOSYSTEM_MAP.get(eco_root)
            or _OSV_ECOSYSTEM_MAP.get(eco)
        )
        if not ch:
            continue
        if not channel:
            channel = ch
            ecosystem = eco
            package = name
        # CRITICAL: only aggregate fix/introduced from rows matching
        # the FIRST chosen package. OSV advisories often list the
        # upstream package (e.g. ``org.apache.logging.log4j:log4j-core``)
        # plus unrelated wrapper / fork packages
        # (``org.ops4j.pax.logging:pax-logging-log4j2``,
        # ``org.xbib.elasticsearch:log4j``) -- pulling fix/introduced
        # from those poisons the version range. Pin to package.
        if name != package:
            continue
        for r in affected.get("ranges", []) or []:
            for ev in r.get("events", []) or []:
                if "fixed" in ev:
                    v = str(ev["fixed"])
                    if v:
                        fix_candidates.append(v)
                if "introduced" in ev:
                    v = str(ev["introduced"])
                    if v and v != "0":
                        intro_candidates.append(v)
    fix = max(fix_candidates, key=_version_key) if fix_candidates else ""
    introduced = (
        min(intro_candidates, key=_version_key) if intro_candidates else ""
    )
    return {
        "ecosystem": ecosystem, "channel": channel, "package": package,
        "fix": fix, "introduced": introduced,
    }


async def _pypi_latest_before(pkg: str, ceiling: str) -> str:
    """Return the largest PyPI release strictly less than ``ceiling``.

    Hits ``https://pypi.org/pypi/<pkg>/json`` and walks the
    ``releases`` map. Used when OSV/NVD give a fix version but no
    introduced version -- for the sandbox probe to install a real
    "vulnerable" version we need *some* concrete pre-fix release.
    Returns empty string on miss / network failure.
    """
    if not pkg or not ceiling:
        return ""
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return ""
            envelope = resp.json()
    except httpx.HTTPError:
        return ""
    releases = envelope.get("releases", {}) or {}
    ceiling_key = _version_key(ceiling)
    candidates = []
    for v, files in releases.items():
        if not files:  # yanked / no artifacts
            continue
        if any(f.get("yanked") for f in files):
            continue
        if "rc" in v or "beta" in v or "alpha" in v or "dev" in v:
            continue
        if _version_key(v) < ceiling_key:
            candidates.append(v)
    if not candidates:
        return ""
    return max(candidates, key=_version_key)


async def _maven_latest_before(coord: str, ceiling: str) -> str:
    """Return the largest Maven Central release strictly less than ``ceiling``.

    Reads ``maven-metadata.xml`` for ``group:artifact`` from
    repo1.maven.org and parses ``<versioning><versions>``. Filters
    out qualifier releases (rc / alpha / beta / SNAPSHOT). Returns
    empty string on miss / network failure.
    """
    if not coord or ":" not in coord or not ceiling:
        return ""
    group, artifact = coord.split(":", 1)
    group_path = group.replace(".", "/")
    url = (
        f"https://repo1.maven.org/maven2/{group_path}/{artifact}/maven-metadata.xml"
    )
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return ""
            body = resp.text
    except httpx.HTTPError:
        return ""
    import re as _re_m

    versions = _re_m.findall(r"<version>([^<]+)</version>", body)
    ceiling_key = _version_key(ceiling)
    candidates = [
        v for v in versions
        if not any(q in v.lower() for q in (
            "rc", "alpha", "beta", "snapshot", "milestone", "-m"
        ))
        and _version_key(v) < ceiling_key
    ]
    if not candidates:
        return ""
    return max(candidates, key=_version_key)


async def _query_osv(cve_id: str) -> dict[str, Any]:
    """Query OSV.dev for the canonical install ecosystem + fix info.

    OSV (Google + GitHub Security Lab) maintains a normalized schema
    that NVD lacks: every advisory has an explicit
    ``affected[].package.ecosystem`` plus typed fix events
    (``introduced`` / ``fixed`` / ``last_affected``). This is the
    canonical way to map a CVE to its install channel.

    Two-step query because OSV's CVE-keyed lookup is sparse: many CVE
    records have an empty ``affected`` array; the ecosystem-rich data
    lives under per-ecosystem alias ids (``GHSA-...`` for Python /
    npm / Go, ``PYSEC-...``, ``DSA-...``, ``RHSA-...`` etc.).

    1. GET ``/v1/vulns/<cve_id>`` -- envelope + alias list.
    2. If ``affected`` is empty, fan out across aliases until one
       returns ecosystem-tagged data.

    Returns ``{"channel": str, "package": str, "fix": str,
    "introduced": str, "ecosystem": str, "raw": dict}``. All keys
    empty on miss / network failure -- caller falls back to NVD-only.
    """
    out = {
        "channel": "", "package": "", "fix": "",
        "introduced": "", "ecosystem": "", "raw": {},
        "vulnerability_status": "",
    }
    if not cve_id:
        return out
    base = "https://api.osv.dev/v1/vulns"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await _retry_get(client, f"{base}/{cve_id}")
            if resp.status_code == 404:
                return out
            resp.raise_for_status()
            envelope = resp.json()
            out["raw"] = envelope
            # ``withdrawn`` timestamp is authoritative on the
            # CVE-keyed envelope (advisory was retracted). The
            # ``no_fix_published`` heuristic gets deferred to the
            # final aggregated state -- we don't want to flag based
            # on the sparse CVE-keyed envelope when the per-alias
            # GHSA / PYSEC envelope might carry the real fix data.
            if envelope.get("withdrawn"):
                out["vulnerability_status"] = "withdrawn"
            hit = _scan_osv_affected(envelope)
            if hit["channel"]:
                out.update({k: v for k, v in hit.items() if k != "raw"})
                return out
            # Step 2: fan out via aliases. We prefer GHSA / PYSEC
            # (registry-canonical ids) over distro-specific aliases.
            aliases = [
                a for a in envelope.get("aliases", []) or []
                if isinstance(a, str)
            ]
            aliases.sort(key=lambda a: 0 if a.startswith(("GHSA-", "PYSEC-")) else 1)
            for alias in aliases[:5]:
                try:
                    a_resp = await _retry_get(client, f"{base}/{alias}")
                    if a_resp.status_code != 200:
                        continue
                    a_env = a_resp.json()
                except (httpx.HTTPError, RuntimeError):
                    continue
                hit = _scan_osv_affected(a_env)
                if a_env.get("withdrawn") and not out.get("vulnerability_status"):
                    out["vulnerability_status"] = "withdrawn"
                if hit["channel"]:
                    out.update({k: v for k, v in hit.items() if k != "raw"})
                    return out
    except (httpx.HTTPError, RuntimeError):
        return out
    return out


# GHSA ecosystem slug (lowercase, GitHub Advisory schema) → channel slug.
_GHSA_ECOSYSTEM_MAP: dict[str, str] = {
    "pip":            "pip",
    "npm":            "npm",
    "rubygems":       "rubygems",
    "maven":          "maven",
    "composer":       "composer",
    "nuget":          "nuget",
    "go":             "go",
    "rust":           "cargo",
    "pub":            "pub",
    "erlang":         "hex",
    "swift":          "swift",
    "actions":        "github_actions",
}


async def _query_github_advisory(cve_id: str) -> dict[str, str]:
    """Resolve CVE → ecosystem + package via GitHub Security Advisory API.

    Backstop for OSV.dev whose CVE-keyed lookup is sparse: the OSV
    record often only exists under its GHSA alias. The public GHSA
    REST endpoint ``/advisories?cve_id=<CVE>`` indexes the same data
    by CVE id and returns ``ghsa_id`` plus first-affected-package
    metadata. We use this when ``_query_osv`` returns no channel.

    Returns ``{"channel": str, "package": str, "fix": str, "ghsa_id":
    str}`` -- empty values on miss / network failure.
    """
    out = {"channel": "", "package": "", "fix": "", "ghsa_id": ""}
    if not cve_id:
        return out
    url = "https://api.github.com/advisories"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as client:
            resp = await client.get(
                url, params={"cve_id": cve_id}, headers=headers,
                follow_redirects=True,
            )
            if resp.status_code != 200:
                return out
            items = resp.json() or []
            if not items:
                return out
            adv = items[0]
            out["ghsa_id"] = str(adv.get("ghsa_id", "") or "")
            for vuln in adv.get("vulnerabilities", []) or []:
                pkg = vuln.get("package") or {}
                eco = str(pkg.get("ecosystem", "") or "").lower()
                channel = _GHSA_ECOSYSTEM_MAP.get(eco, "")
                if not channel:
                    continue
                out["channel"] = channel
                out["package"] = str(pkg.get("name", "") or "")
                out["fix"] = str(vuln.get("first_patched_version", "") or "")
                return out
    except (httpx.HTTPError, RuntimeError):
        return out
    return out


def _product_from_description(desc: str) -> str:
    """Heuristic fallback when NVD has no CPE -- pull the first
    capitalized token from the advisory's English description.

    Many fresh CVEs have not been CPE-classified yet (e.g. CVE-2024-39705
    which targets NLTK but has zero CPEs as of this writing). The
    description's first capitalized word is a reasonable real-data
    candidate for the product name; downstream CMDB correlation is
    name-fuzzy enough that ``NLTK`` matches ``NLTK (Natural Language
    Toolkit)``. Returns empty string when nothing capitalized leads.
    """
    import re as _re

    m = _re.match(r"\s*([A-Z][A-Za-z0-9_+\-]{1,30})", desc.lstrip())
    return m.group(1) if m else ""


@tool(
    name=_NAME,
    namespace=_NAMESPACE,
    version=_VERSION,
    side_effects=SideEffects.read,
    requires_capability=_REQUIRED_CAPABILITY,
    description=(
        "Fetch CVE metadata from the NVD JSON 2.0 API. Returns a flat "
        "projection (description, cvss, cwe, vendor/product, references) "
        "plus the raw upstream envelope. Trusted source per the "
        "cve_remediation source-trust table."
    ),
)
async def fetch_advisory(*, cve_id: str) -> dict[str, Any]:
    """Fetch and project a CVE advisory from NVD.

    Parameters
    ----------
    cve_id
        Canonical CVE id (``CVE-YYYY-NNNN``). Case-insensitive on the
        wire; NVD canonicalizes.

    Returns
    -------
    dict[str, Any]
        Flat projection plus ``raw`` envelope. See module docstring for
        field list.

    Raises
    ------
    HarborRuntimeError
        Empty ``cve_id``, NVD returns 0 results, or non-2xx status.
    httpx.HTTPStatusError
        Network / HTTP failures (caller decides retry policy).
    """
    if not cve_id or not cve_id.strip():
        raise HarborRuntimeError(
            "fetch_advisory requires a non-empty cve_id",
        )
    cve_id = cve_id.strip()
    headers: dict[str, str] = {"Accept": "application/json"}
    if _API_KEY:
        headers["apiKey"] = _API_KEY

    # NVD rate limits: 5 req/30s without key, 50 req/30s with key. The
    # ``_retry_get`` helper handles 429 + 5xx + ReadTimeout transparently
    # with exponential backoff; the per-host circuit breaker short-circuits
    # subsequent calls when NVD is down hard.
    envelope: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=_NVD_TIMEOUT_S) as client:
        resp = await _retry_get(
            client, _NVD_BASE, params={"cveId": cve_id}, headers=headers,
        )
        resp.raise_for_status()
        envelope = resp.json()

    vulns = envelope.get("vulnerabilities", [])
    if not vulns:
        # NVD lag: many GitHub-Security-Advisory-indexed CVEs land on
        # GHSA before NVD ingests them. Fall back to GHSA + OSV for
        # the minimal advisory shape so downstream stages have a
        # channel + package + fix to act on instead of halting at
        # intake. Reference list stays empty (NVD's slot); the GHSA
        # GHSA_id is surfaced via osv_package_name + install_channel.
        ghsa_only = await _query_github_advisory(cve_id)
        osv_only = await _query_osv(cve_id)
        ghsa_channel = ghsa_only.get("channel", "")
        ghsa_pkg = ghsa_only.get("package", "")
        ghsa_fix = ghsa_only.get("fix", "")
        osv_channel = osv_only.get("channel", "")
        osv_pkg = osv_only.get("package", "")
        osv_fix = osv_only.get("fix", "")
        resolved_channel = ghsa_channel or osv_channel
        resolved_pkg = ghsa_pkg or osv_pkg
        resolved_fix = ghsa_fix or osv_fix
        if not (resolved_channel and resolved_pkg):
            raise HarborRuntimeError(
                f"NVD returned 0 results for {cve_id!r}; "
                f"GHSA/OSV fallback insufficient (channel={resolved_channel!r}, "
                f"pkg={resolved_pkg!r})",
                cve_id=cve_id,
            )
        return {
            "url": (
                f"https://github.com/advisories/{ghsa_only.get('ghsa_id','')}"
                if ghsa_only.get("ghsa_id") else
                f"https://osv.dev/vulnerability/{cve_id}"
            ),
            "description": (
                f"{cve_id}: advisory resolved via "
                f"{'GHSA' if ghsa_channel else 'OSV'} fallback "
                f"(NVD lag). Package={resolved_pkg}, "
                f"channel={resolved_channel}, fix={resolved_fix or 'unknown'}."
            ),
            "cvss": 0.0,
            "cwe": "",
            "vendor": "",
            "product": resolved_pkg,
            "candidate_products": [resolved_pkg],
            "fixed_version": resolved_fix,
            "exact_affected_versions": [],
            "affected_version_ranges": [],
            "install_channel": resolved_channel,
            "osv_package_name": resolved_pkg,
            "vulnerability_status": str(
                osv_only.get("vulnerability_status", "") or ""
            ),
            "references": [
                {"url": u, "tags": []}
                for u in [
                    f"https://github.com/advisories/{ghsa_only.get('ghsa_id','')}"
                    if ghsa_only.get("ghsa_id") else "",
                    f"https://osv.dev/vulnerability/{cve_id}",
                ]
                if u
            ],
            "cpe_uris": [],
            "raw": {
                "source": "ghsa+osv-fallback",
                "ghsa": ghsa_only,
                "osv_raw_keys": list(osv_only.get("raw", {}).keys()),
            },
        }
    item = vulns[0].get("cve", {})
    canonical_id = str(item.get("id") or cve_id)
    description = _english_description(item)
    cwe = _primary_cwe(item)
    cvss = _cvss_base_score(item)
    cpe_products = _all_cpe_products(item)
    cpe_uris = _all_cpe_uris(item)
    # Build the candidate product list in priority order:
    # 1. Distinct CPE product strings (deduped, in NVD order).
    # 2. The first capitalized token of the description as a fallback
    #    for CVEs that have not been CPE-classified yet.
    candidate_products: list[str] = []
    for _, prod in cpe_products:
        if prod and prod not in candidate_products:
            candidate_products.append(prod)
    # Description fallback only when CPE list is empty — a CPE-classified
    # advisory has authoritative (vendor, product) pairs; mixing in a
    # capitalized-token guess from the description leaks generic noise
    # words ("Multiple", "Improper", "Vulnerability") that downstream
    # CMDB scoring treats as real product terms and false-positives on
    # CIs whose display names contain those words.
    if not candidate_products:
        desc_fallback = _product_from_description(description)
        if desc_fallback:
            candidate_products.append(desc_fallback)
    primary_vendor = cpe_products[0][0] if cpe_products else ""
    primary_product = candidate_products[0] if candidate_products else ""
    references = [
        {"url": r.get("url", ""), "tags": list(r.get("tags", []))}
        for r in item.get("references", [])
    ]
    affected_ranges, fixed_version, exact_affected = _extract_version_data(
        item, primary_product
    )
    install_channel = _derive_install_channel(references)
    # OSV.dev secondary lookup: NVD references rarely include direct
    # PyPI / Maven URLs (most modern advisories link only to GitHub
    # Advisory pages), so reference-host matching whiffs. OSV exposes
    # ``ecosystem`` + typed fix events on every advisory -- the
    # canonical channel source. We let OSV override the NVD-derived
    # values when it has them; if OSV 404s or net-fails, NVD wins.
    osv = await _query_osv(canonical_id)
    if osv["channel"]:
        install_channel = osv["channel"]
    osv_package_name = ""
    if osv["package"]:
        # Promote OSV's package name to the head of candidate_products
        # (registry-canonical: "pillow" / "log4j-core" / "xz-utils" /
        # etc., as the upstream ecosystem names them). Also surface as
        # ``osv_package_name`` so the sandbox probe can use the full
        # registry coord (Maven needs ``group:artifact`` to construct
        # the jar URL; CMDB-matched product names usually drop the
        # group prefix).
        if osv["package"] not in candidate_products:
            candidate_products.insert(0, osv["package"])
        primary_product = osv["package"]
        osv_package_name = osv["package"]
    # GHSA fallback: OSV indexes most advisories under their GHSA alias,
    # so ``/v1/vulns/<CVE>`` returns 404 for many real advisories whose
    # GitHub Security Advisory does carry full ecosystem + package info.
    # Resolve via the GHSA REST API (``/advisories?cve_id=<CVE>``) when
    # OSV came back empty. Treat the ``github`` sentinel from
    # ``_derive_install_channel`` (only a GitHub Advisory URL was in the
    # NVD references) as unresolved -- it's a fallback marker, not a
    # real install channel.
    needs_channel_resolve = install_channel in ("", "github")
    if needs_channel_resolve or not osv_package_name:
        ghsa = await _query_github_advisory(canonical_id)
        if ghsa["channel"] and needs_channel_resolve:
            install_channel = ghsa["channel"]
        if ghsa["package"] and not osv_package_name:
            osv_package_name = ghsa["package"]
            if ghsa["package"] not in candidate_products:
                candidate_products.insert(0, ghsa["package"])
            if not primary_product:
                primary_product = ghsa["package"]
        if ghsa["fix"] and not fixed_version:
            fixed_version = ghsa["fix"]
    # OSV is the canonical source for ecosystem-tagged data: prefer its
    # fix version over NVD's CPE-derived value when both exist. NVD's
    # ``versionEndExcluding`` is often row-bound to a downstream
    # firmware vendor (Log4Shell has 166 CPE rows where only ~2
    # describe the upstream maven artifact); OSV tracks the upstream
    # package directly.
    if osv["fix"]:
        fixed_version = osv["fix"]
    if osv["introduced"] and osv["introduced"] not in exact_affected:
        exact_affected = [osv["introduced"]] + [
            v for v in exact_affected if v != osv["introduced"]
        ]
    # Final ``vulnerability_status`` resolution: if no upstream-
    # withdrawn flag fired AND we ended up with no fix version from
    # any source (NVD CPE versionEndExcluding + OSV ``fixed`` events),
    # the advisory has no published patch -- typical for tarball-
    # withdrawal vulns (CVE-2024-3094 xz, etc.). Mark mitigation_only
    # so the sandbox layer routes to HITL with explicit reason.
    vulnerability_status = str(osv.get("vulnerability_status", "") or "")
    # NVD is the authoritative lifecycle source. OSV bulk-withdrew tens
    # of thousands of NVD-mirror entries on 2026-05-04 for hardware /
    # proprietary CVEs they couldn't map to a package ecosystem -- those
    # advisories are *active* per NVD ``vulnStatus`` but OSV flags them
    # ``withdrawn``. NVD's vulnStatus is the source of truth for whether
    # the CVE itself was retracted; OSV's withdrawn flag only carries
    # weight when NVD agrees.
    nvd_status_raw = str(item.get("vulnStatus") or "").strip()
    nvd_status_lc = nvd_status_raw.lower()
    nvd_retracted = nvd_status_lc in ("rejected", "withdrawn")
    if vulnerability_status == "withdrawn" and not nvd_retracted:
        vulnerability_status = ""
    if not vulnerability_status and not fixed_version:
        # Only flag when at least ONE of the sources had data; pure
        # 404s from both NVD and OSV stay empty (caller already
        # surfaces those as raw fetch errors).
        if osv.get("raw") or affected_ranges:
            vulnerability_status = "no_fix_published"

    # When neither NVD CPE nor OSV gives a concrete vulnerable version
    # (introduced/exact), look up the registry's "latest release before
    # the fix version" -- a real package on the registry, real version
    # string, no fabrication. Covers Pillow-class advisories where
    # NVD only has versionEndIncluding and OSV omits ``introduced``.
    # Skipped when fix_version itself is missing (no ceiling to bound
    # the search).
    if not exact_affected and fixed_version:
        if install_channel == "pip" and primary_product:
            latest = await _pypi_latest_before(primary_product, fixed_version)
            if latest:
                exact_affected = [latest]
        elif install_channel == "maven" and osv_package_name:
            latest = await _maven_latest_before(osv_package_name, fixed_version)
            if latest:
                exact_affected = [latest]
    url = f"https://nvd.nist.gov/vuln/detail/{canonical_id}"

    return {
        "cve_id": canonical_id,
        "description": description,
        "url": url,
        "source_class": "trusted",  # NVD per source-trust table
        "cvss": cvss,
        "cwe": cwe,
        "kev_listed": False,  # KEV refresh node merges this from CISA feed
        "epss": 0.0,  # EnrichCveTrustedNode merges this from EPSS feed
        "vendor": primary_vendor,
        "product": primary_product,
        "candidate_products": candidate_products,
        "cpe_products": [
            {"vendor": v, "product": p} for v, p in cpe_products
        ],
        "cpe_uris": cpe_uris,
        # Version + install-channel signals -- consumed by the sandbox
        # probe to pick install spec (vulnerable_spec / patched_spec)
        # WITHOUT any per-CVE hardcoding. ``fixed_version`` is the first
        # ``versionEndExcluding`` for the primary product (the standard
        # NVD convention for "fixed in N+"). ``exact_affected`` is the
        # set of literal version strings appearing in CPE 2.3 criteria
        # (cve-2024-3094 has 5.6.0/5.6.1 here, for example).
        "fixed_version": fixed_version,
        "affected_version_ranges": affected_ranges,
        "exact_affected_versions": exact_affected,
        "install_channel": install_channel,
        "osv_package_name": osv_package_name,
        "vulnerability_status": vulnerability_status,
        "references": references,
        "raw": item,
        "__harbor_provenance__": {
            "origin": "tool",
            "source": "nvd",
            "external_id": canonical_id,
        },
    }
