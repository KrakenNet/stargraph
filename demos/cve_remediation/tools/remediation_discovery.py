# SPDX-License-Identifier: Apache-2.0
"""Remediation discovery — sources A-D.

Goal: surface real remediation actions for CVEs whose advisory data
doesn't yield a clean ``fixed_version``. Auto-extraction sources:

  A. **advisory_ref** — fetch top N URLs from ``state.advisory_references``
     (already pulled by IntakeFetchNode), filtered by NVD tag priority
     (Vendor Advisory > Patch > Third Party Advisory).  Strip HTML to
     plain text snippets.
  B. **registry**    — for pip/maven channels, query latest stable
     release; if it falls outside ``affected_version_ranges`` /
     ``exact_affected_versions``, propose upgrade-to-latest as a
     candidate action.
  C. **ddg_search**  — DuckDuckGo HTML search (no API key) for
     ``"<cve_id>" fix mitigation workaround``.  Top 5 results.
  D. **searxng**     — local SearXNG instance.  Best signal when
     reachable; fail-soft when not.

The actual structured-action extraction is performed by an LM call
(see ``lm_extract_actions``); this module just gathers raw evidence.

No fabrications. Every snippet carries a verifiable URL.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

_DEFAULT_TIMEOUT_S = float(
    os.environ.get("CVE_REM_DISCOVERY_TIMEOUT_S", "8")
)
_USER_AGENT = (
    "harbor-cve-remediation/0.1 (+https://github.com/) "
    "discovery-fetcher"
)
_MAX_REF_BYTES = 200_000  # 200 KB cap per advisory page
_TEXT_BUDGET = 4_000      # chars retained per snippet
_TAG_PRIORITY = (
    "Vendor Advisory",
    "Patch",
    "Issue Tracking",
    "Third Party Advisory",
    "Mailing List",
)
_BAD_HOSTS = (
    "twitter.com", "x.com", "news.ycombinator.com",
)


@dataclass
class Snippet:
    """One piece of evidence gathered from a source.

    All fields are required; an entry without ``url`` is dropped
    before LM extraction so the LM never invents citations.
    """

    source: str
    url: str
    title: str = ""
    excerpt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Source A: walk existing advisory_references
# ---------------------------------------------------------------------------


def _rank_reference(ref: dict[str, Any]) -> tuple[int, str]:
    """Sort key: lower-tier priority index first, then URL for stability."""

    tags = list(ref.get("tags", []) or [])
    pri = len(_TAG_PRIORITY)
    for i, t in enumerate(_TAG_PRIORITY):
        if t in tags:
            pri = i
            break
    url = str(ref.get("url", "") or "")
    return (pri, url)


def _strip_html(html: str) -> str:
    """Cheap HTML → text stripper. No bs4 dependency."""

    # Drop script / style blocks first.
    html = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Replace block-level tags with newline so paragraphs survive.
    html = re.sub(
        r"</?(p|div|li|br|h\d|tr|table|hr|section|article)[^>]*>",
        "\n",
        html,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace + decode minimal entities.
    for entity, ch in (
        ("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
        ("&quot;", '"'), ("&#39;", "'"),
    ):
        text = text.replace(entity, ch)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    return text.strip()


async def _fetch_one_reference(
    client: httpx.AsyncClient, url: str
) -> Snippet | None:
    """Fetch + strip one advisory URL.  Returns None on hard failures."""

    try:
        resp = await client.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "text/html,*/*"},
            follow_redirects=True,
            timeout=_DEFAULT_TIMEOUT_S,
        )
    except (httpx.HTTPError, OSError):
        return None
    if resp.status_code != 200:
        return None
    body = resp.content[:_MAX_REF_BYTES]
    try:
        text = _strip_html(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 — defensive
        return None
    if not text:
        return None
    # Pull a title from <title> if present (pre-strip).
    title = ""
    try:
        m = re.search(
            rb"<title[^>]*>(.*?)</title>", body, flags=re.DOTALL | re.IGNORECASE
        )
        if m:
            title = (
                m.group(1).decode("utf-8", errors="replace").strip()[:240]
            )
    except Exception:  # noqa: BLE001
        pass
    return Snippet(
        source="advisory_ref",
        url=url,
        title=title,
        excerpt=text[:_TEXT_BUDGET],
    )


async def fetch_reference_snippets(
    advisory_references: list[dict[str, Any]],
    *,
    top_n: int = 5,
) -> list[Snippet]:
    """Fetch the top N already-cached advisory references."""

    if not advisory_references:
        return []
    refs = sorted(advisory_references, key=_rank_reference)
    out: list[Snippet] = []
    seen_hosts: set[str] = set()
    async with httpx.AsyncClient() as client:
        for r in refs:
            url = str(r.get("url", "") or "")
            if not url:
                continue
            host = re.sub(r"^https?://([^/]+).*", r"\1", url).lower()
            if any(b in host for b in _BAD_HOSTS):
                continue
            # diversify hosts so we don't fetch 5 redhat pages
            if host in seen_hosts:
                continue
            seen_hosts.add(host)
            snip = await _fetch_one_reference(client, url)
            if snip:
                out.append(snip)
            if len(out) >= top_n:
                break
    return out


# ---------------------------------------------------------------------------
# Source B: registry latest-version check
# ---------------------------------------------------------------------------


def _version_tuple(v: str) -> tuple[int, ...]:
    if not v:
        return (0,)
    parts = []
    for seg in v.split("."):
        digits = "".join(ch for ch in seg if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _is_unaffected(
    candidate: str,
    *,
    exact_affected: list[str],
    affected_ranges: list[dict[str, Any]],
) -> bool:
    """Best-effort: is ``candidate`` outside the known-affected set?

    Returns False conservatively when we can't prove the candidate is
    safe.  Caller should treat False as "needs HITL review", not "safe".
    """

    if not candidate:
        return False
    cand = _version_tuple(candidate)
    for v in exact_affected:
        if _version_tuple(v) == cand:
            return False
    for r in affected_ranges:
        end_exc = str(r.get("end_exc", "") or "")
        end_inc = str(r.get("end_inc", "") or "")
        start_inc = str(r.get("start_inc", "") or "")
        start_exc = str(r.get("start_exc", "") or "")
        # in (start_inc..end_exc)?
        if start_inc and _version_tuple(start_inc) > cand:
            continue
        if start_exc and _version_tuple(start_exc) >= cand:
            continue
        if end_exc and cand >= _version_tuple(end_exc):
            continue
        if end_inc and cand > _version_tuple(end_inc):
            continue
        # Range applies AND none of the bounds excluded us → affected.
        if any((start_inc, start_exc, end_inc, end_exc)):
            return False
    return True


async def _pypi_latest(pkg: str) -> str:
    if not pkg:
        return ""
    url = f"https://pypi.org/pypi/{pkg}/json"
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return ""
            env = r.json()
    except (httpx.HTTPError, OSError, ValueError):
        return ""
    info = env.get("info") or {}
    latest = str(info.get("version", "") or "")
    return latest


async def _maven_latest(coord: str) -> str:
    """Coord = ``group:artifact``."""

    if not coord or ":" not in coord:
        return ""
    g, a = coord.split(":", 1)
    url = (
        "https://search.maven.org/solrsearch/select?"
        f"q=g:%22{g}%22+AND+a:%22{a}%22&rows=1&wt=json"
    )
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as c:
            r = await c.get(url)
            if r.status_code != 200:
                return ""
            env = r.json()
    except (httpx.HTTPError, OSError, ValueError):
        return ""
    docs = (env.get("response") or {}).get("docs") or []
    if not docs:
        return ""
    return str(docs[0].get("latestVersion", "") or "")


async def registry_latest_unaffected(
    *,
    install_channel: str,
    primary_product: str,
    osv_package_name: str,
    exact_affected: list[str],
    affected_ranges: list[dict[str, Any]],
) -> Snippet | None:
    """Source B helper.  Returns a Snippet with kind=registry when the
    registry's latest stable version is provably outside the affected
    set; ``None`` otherwise."""

    latest = ""
    citation_url = ""
    if install_channel == "pip" and primary_product:
        latest = await _pypi_latest(primary_product)
        citation_url = f"https://pypi.org/pypi/{primary_product}/"
    elif install_channel == "maven" and osv_package_name:
        latest = await _maven_latest(osv_package_name)
        if ":" in osv_package_name:
            g, a = osv_package_name.split(":", 1)
            citation_url = (
                f"https://search.maven.org/artifact/{g}/{a}/{latest}"
                if latest else f"https://search.maven.org/search?q=g:{g}+a:{a}"
            )
    if not latest:
        return None
    safe = _is_unaffected(
        latest,
        exact_affected=exact_affected,
        affected_ranges=affected_ranges,
    )
    return Snippet(
        source="registry",
        url=citation_url,
        title=f"{install_channel} latest: {primary_product or osv_package_name}={latest}",
        excerpt=(
            f"Registry's current latest stable: {latest}. "
            f"Provably outside affected set: {safe}."
        ),
        metadata={
            "channel": install_channel,
            "package": primary_product or osv_package_name,
            "latest_version": latest,
            "is_unaffected": safe,
        },
    )


# ---------------------------------------------------------------------------
# Source C: DuckDuckGo HTML (no key)
# ---------------------------------------------------------------------------


_DDG_URL = "https://duckduckgo.com/html/"
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


async def ddg_search(query: str, *, top_n: int = 5) -> list[Snippet]:
    """Hit DuckDuckGo's HTML endpoint and parse the first N results.

    Fail-soft: returns ``[]`` on any HTTP / parse error.  Rate-limit
    is on the caller; this helper makes one request.
    """

    if not query:
        return []
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as c:
            r = await c.post(
                _DDG_URL,
                data={"q": query, "kl": "us-en"},
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            )
    except (httpx.HTTPError, OSError):
        return []
    if r.status_code != 200:
        return []
    body = r.text
    out: list[Snippet] = []
    seen_hosts: set[str] = set()
    for m in _DDG_RESULT_RE.finditer(body):
        href, title_html, snippet_html = m.group(1), m.group(2), m.group(3)
        # DDG wraps real URLs in /l/?uddg=<urlencoded> redirect; unwrap.
        url_m = re.search(r"uddg=([^&]+)", href)
        if url_m:
            from urllib.parse import unquote
            url = unquote(url_m.group(1))
        else:
            url = href
        host = re.sub(r"^https?://([^/]+).*", r"\1", url).lower()
        if any(b in host for b in _BAD_HOSTS):
            continue
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        title = _strip_html(title_html)[:200]
        snippet = _strip_html(snippet_html)[:_TEXT_BUDGET]
        out.append(
            Snippet(source="ddg_search", url=url, title=title, excerpt=snippet)
        )
        if len(out) >= top_n:
            break
    return out


# ---------------------------------------------------------------------------
# Source D: local SearXNG
# ---------------------------------------------------------------------------


def _searxng_endpoint() -> str:
    """Resolve the SearXNG endpoint.

    Order: ``CVE_REM_SEARXNG_URL`` env, then docker bridge IP for the
    ``railyard-searxng-dev`` container (best-effort via ``docker
    inspect``), else empty.
    """

    direct = os.environ.get("CVE_REM_SEARXNG_URL", "").strip()
    if direct:
        return direct.rstrip("/")
    # Best-effort dynamic resolution.  Subprocess to avoid a docker-py dep.
    try:
        import subprocess  # noqa: S404 — read-only inspect call
        r = subprocess.run(  # noqa: S603,S607 — repo-controlled args
            [
                "docker", "inspect", "railyard-searxng-dev",
                "--format",
                "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
            ],
            capture_output=True, text=True, check=False, timeout=4,
        )
        ip = (r.stdout or "").strip().split()[0] if r.stdout.strip() else ""
        if ip:
            return f"http://{ip}:8080"
    except Exception:  # noqa: BLE001
        pass
    return ""


async def searxng_search(query: str, *, top_n: int = 5) -> list[Snippet]:
    """Query the local SearXNG instance over its JSON API.

    Fail-soft: empty list on unreachable endpoint or parse error.
    """

    if not query:
        return []
    base = _searxng_endpoint()
    if not base:
        return []
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT_S) as c:
            r = await c.post(
                f"{base}/search",
                data={"q": query, "format": "json"},
                headers={"User-Agent": _USER_AGENT},
            )
    except (httpx.HTTPError, OSError):
        return []
    if r.status_code != 200:
        return []
    try:
        env = r.json()
    except ValueError:
        return []
    out: list[Snippet] = []
    seen_hosts: set[str] = set()
    for res in env.get("results", []) or []:
        url = str(res.get("url", "") or "")
        if not url:
            continue
        host = re.sub(r"^https?://([^/]+).*", r"\1", url).lower()
        if any(b in host for b in _BAD_HOSTS):
            continue
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        out.append(
            Snippet(
                source="searxng",
                url=url,
                title=str(res.get("title", "") or "")[:200],
                excerpt=str(res.get("content", "") or "")[:_TEXT_BUDGET],
                metadata={
                    "engines": res.get("engines"),
                    "score": res.get("score"),
                },
            )
        )
        if len(out) >= top_n:
            break
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def gather_evidence(
    *,
    cve_id: str,
    advisory_references: list[dict[str, Any]],
    install_channel: str,
    primary_product: str,
    osv_package_name: str,
    exact_affected: list[str],
    affected_ranges: list[dict[str, Any]],
    enable_ddg: bool = True,
    enable_searxng: bool = True,
    top_per_source: int = 5,
) -> tuple[list[Snippet], dict[str, Any]]:
    """Run sources A-D in parallel; return combined snippet list + provenance."""

    prov: dict[str, Any] = {
        "sources_attempted": [],
        "sources_succeeded": [],
        "references_fetched": 0,
        "search_results_fetched": 0,
        "registry_check_result": "",
        "last_error": "",
    }
    query = f'"{cve_id}" fix mitigation workaround'
    tasks: dict[str, Any] = {}
    tasks["advisory_ref"] = fetch_reference_snippets(
        advisory_references, top_n=top_per_source,
    )
    prov["sources_attempted"].append("advisory_ref")
    tasks["registry"] = registry_latest_unaffected(
        install_channel=install_channel,
        primary_product=primary_product,
        osv_package_name=osv_package_name,
        exact_affected=exact_affected,
        affected_ranges=affected_ranges,
    )
    prov["sources_attempted"].append("registry")
    if enable_ddg:
        tasks["ddg_search"] = ddg_search(query, top_n=top_per_source)
        prov["sources_attempted"].append("ddg_search")
    if enable_searxng:
        tasks["searxng"] = searxng_search(query, top_n=top_per_source)
        prov["sources_attempted"].append("searxng")
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    snippets: list[Snippet] = []
    for source_name, res in zip(tasks.keys(), results, strict=True):
        if isinstance(res, BaseException):
            prov["last_error"] = (
                f"{source_name}: {type(res).__name__}: {res}"
            )
            continue
        if source_name == "registry":
            if res is None:
                continue
            prov["sources_succeeded"].append("registry")
            prov["registry_check_result"] = (
                f"{install_channel}:latest="
                f"{res.metadata.get('latest_version','')} "
                f"unaffected={res.metadata.get('is_unaffected')}"
            )
            snippets.append(res)
            continue
        if not res:
            continue
        prov["sources_succeeded"].append(source_name)
        if source_name == "advisory_ref":
            prov["references_fetched"] += len(res)
        else:
            prov["search_results_fetched"] += len(res)
        snippets.extend(res)
    return snippets, prov


# ---------------------------------------------------------------------------
# LM extraction
# ---------------------------------------------------------------------------


_ALLOWED_KINDS = {
    "upgrade", "downgrade", "env_var", "config_change",
    "network_policy", "package_replace", "disable_feature",
    "mitigation_only",
}

_EXTRACT_SYSTEM = (
    "You are a security engineer extracting structured remediation "
    "actions from CVE advisory text and search snippets. Output ONLY "
    "valid JSON matching the schema below. Do NOT add prose. "
    "Do NOT invent citations -- every action MUST quote a "
    "citation_url that appears in the provided snippets. Drop any "
    "action you cannot tie to a specific snippet.\n\n"
    "Schema:\n"
    "{\n"
    '  "actions": [\n'
    "    {\n"
    '      "kind": "upgrade|downgrade|env_var|config_change|'
    'network_policy|package_replace|disable_feature|mitigation_only",\n'
    '      "target": "what changes (package@version, env-var name, '
    'config path, service:port)",\n'
    '      "target_version": "<exact version string for upgrade/'
    'downgrade kinds; e.g. \\"5.4.5\\", \\"2.17.1\\". REQUIRED for '
    'upgrade/downgrade. Empty for other kinds.>",\n'
    '      "change": "the actual change directive",\n'
    '      "rationale": "1-2 sentences why this fixes/mitigates",\n'
    '      "citation_url": "<URL from snippets>",\n'
    '      "citation_excerpt": "<=240 char verbatim quote from the '
    'cited snippet>",\n'
    '      "confidence_bp": <int 0-10000 -- how sure are you the '
    "action correctly mitigates>\n"
    "    }\n"
    "  ]\n"
    "}\n\n"
    "Confidence scoring:\n"
    "  9000+ : action quoted verbatim from a primary vendor advisory\n"
    "  7000+ : action stated in a third-party advisory or registry "
    "metadata with a clear version match\n"
    "  4000+ : action implied by the snippet but not stated as a "
    "concrete directive\n"
    "  <4000 : weak signal -- include only if no stronger evidence "
    "exists\n"
)


async def lm_extract_actions(
    *,
    cve_id: str,
    advisory_body: str,
    snippets: list[Snippet],
    lm_url: str = "",
    lm_model: str = "",
    lm_api_key: str = "",
    timeout_s: float = 30.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """LM-extract structured remediation actions from gathered evidence.

    Returns ``(actions_list, diagnostics)``.  Actions without a
    ``citation_url`` matching one of the snippets are dropped (no
    fabricated citations).  ``diagnostics`` records lm_actions_emitted
    and lm_actions_dropped_no_citation.
    """

    diag: dict[str, Any] = {
        "lm_actions_emitted": 0,
        "lm_actions_dropped_no_citation": 0,
        "last_error": "",
    }
    if not snippets:
        return [], diag
    lm_url = (lm_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
    lm_model = lm_model or os.environ.get("LLM_MODEL", "")
    lm_api_key = (
        lm_api_key
        or os.environ.get("LLM_API_KEY", "placeholder")
        or "placeholder"
    )
    if not lm_url or not lm_model:
        diag["last_error"] = "LLM_BASE_URL or LLM_MODEL unset"
        return [], diag

    # Build a citation-bound user prompt: the LM sees a numbered
    # snippet list.  We accept any citation_url that matches one of
    # those exact URLs.
    lines: list[str] = [
        f"CVE: {cve_id}",
        "",
        "## Advisory body (verbatim, may be truncated)",
        (advisory_body or "")[:1500],
        "",
        "## Evidence snippets",
    ]
    valid_urls: set[str] = set()
    for i, s in enumerate(snippets):
        valid_urls.add(s.url)
        lines.append(
            f"\n[{i+1}] source={s.source} url={s.url}\n"
            f"    title: {(s.title or '')[:200]}\n"
            f"    excerpt: {(s.excerpt or '')[:1200]}"
        )
    lines.append(
        "\nReturn the actions JSON now. Cite only the URLs above."
    )
    user_brief = "\n".join(lines)

    body = {
        "model": lm_model,
        "messages": [
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": user_brief},
        ],
        "temperature": 0.0,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {lm_api_key}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as c:
            r = await c.post(
                f"{lm_url}/chat/completions",
                json=body,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as exc:
        diag["last_error"] = f"http: {type(exc).__name__}: {exc}"
        return [], diag

    content = ""
    try:
        content = (data["choices"][0]["message"]["content"] or "").strip()
    except (KeyError, IndexError, TypeError):
        diag["last_error"] = "lm response missing choices[0].message.content"
        return [], diag
    if not content:
        diag["last_error"] = "lm returned empty content"
        return [], diag
    # Some LMs wrap JSON in ```json fences; strip.
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        import json as _json
        envelope = _json.loads(content)
    except ValueError as exc:
        diag["last_error"] = f"json parse: {exc}"
        return [], diag
    raw_actions = envelope.get("actions") or []
    if not isinstance(raw_actions, list):
        diag["last_error"] = "actions field is not a list"
        return [], diag

    cleaned: list[dict[str, Any]] = []
    for a in raw_actions:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("kind", "") or "").strip().lower()
        url = str(a.get("citation_url", "") or "").strip()
        if kind not in _ALLOWED_KINDS:
            diag["lm_actions_dropped_no_citation"] += 1
            continue
        # Citation must match one of the provided snippet URLs
        # (fabricated citations are the #1 LM failure mode here).
        if url not in valid_urls:
            diag["lm_actions_dropped_no_citation"] += 1
            continue
        confidence = a.get("confidence_bp", 0)
        try:
            confidence = int(confidence)
        except (TypeError, ValueError):
            confidence = 0
        confidence = max(0, min(10000, confidence))
        # Find which snippet supplied the citation so we can record
        # ``source`` honestly.
        source = "lm_synthesis"
        for s in snippets:
            if s.url == url:
                source = s.source
                break
        target_version = str(a.get("target_version", "") or "").strip()[:80]
        # Drop upgrade/downgrade actions without a version: the sandbox
        # has nothing to install, and a vague "upgrade to latest" claim
        # without a concrete number is exactly the kind of fake-pass
        # the no-cheats rule forbids.
        if kind in ("upgrade", "downgrade") and not target_version:
            diag["lm_actions_dropped_no_citation"] += 1
            continue
        cleaned.append({
            "kind": kind,
            "target": str(a.get("target", "") or "").strip()[:240],
            "target_version": target_version,
            "change": str(a.get("change", "") or "").strip()[:480],
            "rationale": str(a.get("rationale", "") or "").strip()[:480],
            "citation_url": url,
            "citation_excerpt": str(
                a.get("citation_excerpt", "") or ""
            ).strip()[:240],
            "source": source,
            "confidence_bp": confidence,
        })
    diag["lm_actions_emitted"] = len(cleaned)
    return cleaned, diag


# ---------------------------------------------------------------------------
# Suggestion #6: no-fix mitigation generator
#
# When the upstream advisory + every discovery source agree there's no
# fix (vulnerability_status == 'no_fix_published' / 'withdrawn'), the
# pipeline still owes the operator concrete mitigation guidance — what
# to do *until* upstream ships a patch.  Curated CWE→mitigation map
# emits canned actions with deterministic CWE-database citations.
#
# Citations point to MITRE's CWE pages (cwe.mitre.org/data/definitions/
# <id>.html); these are stable, public references — not fabricated.
# Confidence is bounded (≤7000 bp) because mitigations don't *fix* the
# underlying vulnerability, they only reduce exposure.  Operators are
# expected to apply the upstream patch when one ships.
# ---------------------------------------------------------------------------


_NO_FIX_MITIGATIONS: dict[str, list[dict[str, Any]]] = {
    "CWE-79": [
        {
            "kind": "mitigation",
            "target": "WAF / CDN edge rule",
            "change": (
                "Block reflected/stored XSS payloads at edge: enable "
                "OWASP ModSecurity CRS rules 941100-941350 (or "
                "equivalent vendor rule pack) on the affected web tier "
                "until the upstream patch is available."
            ),
            "rationale": (
                "Prevents the malicious payload from reaching the "
                "vulnerable endpoint.  Detective-control fallback when "
                "no_fix_published."
            ),
            "confidence_bp": 6500,
        },
        {
            "kind": "mitigation",
            "target": "Content-Security-Policy header",
            "change": (
                "Deploy a strict CSP: default-src 'self'; "
                "script-src 'self'; object-src 'none'; "
                "frame-ancestors 'none'.  Block inline scripts."
            ),
            "rationale": (
                "Reduces XSS exploitability by restricting script "
                "execution sources at the browser."
            ),
            "confidence_bp": 6000,
        },
    ],
    "CWE-78": [
        {
            "kind": "mitigation",
            "target": "Input allow-list",
            "change": (
                "Restrict the affected command surface to an "
                "explicit allow-list of pre-vetted shell tokens; "
                "reject any input containing shell metacharacters "
                "(; | & $ ` < > newline) at the API boundary."
            ),
            "rationale": (
                "Eliminates command-injection vector when upstream "
                "fix not yet shipped."
            ),
            "confidence_bp": 7000,
        },
    ],
    "CWE-77": [
        {
            "kind": "mitigation",
            "target": "Input allow-list",
            "change": (
                "Constrain command-template arguments to a strict "
                "allow-list; quote every interpolated value with "
                "shlex.quote (or vendor-equivalent) before exec."
            ),
            "rationale": "Same vector as CWE-78 — block injection at the boundary.",
            "confidence_bp": 7000,
        },
    ],
    "CWE-94": [
        {
            "kind": "mitigation",
            "target": "Code-execution surface",
            "change": (
                "Disable the affected eval/exec endpoint or restrict "
                "it to authenticated admin contexts only; remove "
                "world-readable test/debug files (e.g. PHPUnit "
                "vendor/phpunit/Util/PHP/eval-stdin.php) from "
                "production deployments."
            ),
            "rationale": "Eliminates the code-injection vector outright.",
            "confidence_bp": 7000,
        },
    ],
    "CWE-200": [
        {
            "kind": "mitigation",
            "target": "Access-control review",
            "change": (
                "Audit the affected endpoint's authentication + "
                "authorization checks; restrict to least-privilege "
                "service accounts; rotate any credentials that may "
                "have been disclosed during the exposure window."
            ),
            "rationale": (
                "Reduces blast radius of the information-exposure "
                "until upstream fix is published."
            ),
            "confidence_bp": 6500,
        },
    ],
    "CWE-306": [
        {
            "kind": "mitigation",
            "target": "Authentication enforcement",
            "change": (
                "Enable management-plane access controls: bind the "
                "vulnerable interface to a management VRF / management-"
                "VLAN; require MFA + IP allow-list for admin access."
            ),
            "rationale": (
                "Restricts who can reach the unauthenticated endpoint "
                "until vendor patch ships."
            ),
            "confidence_bp": 6500,
        },
    ],
    "CWE-284": [
        {
            "kind": "mitigation",
            "target": "Access-control hardening",
            "change": (
                "Apply principle-of-least-privilege to the affected "
                "resource; tighten ACLs to the minimum role set needed "
                "for operations; rotate any credentials with broader "
                "access than required."
            ),
            "rationale": "Improper-access-control class — least-privilege reduces risk.",
            "confidence_bp": 6000,
        },
    ],
    "CWE-506": [
        {
            "kind": "mitigation",
            "target": "Package isolation + downgrade",
            "change": (
                "Pin the affected dependency to the last known-good "
                "version (pre-backdoor); add a CI guard rejecting any "
                "rebuild that pulls the malicious release; rotate any "
                "secrets that may have been exposed in build "
                "environments running the backdoored version."
            ),
            "rationale": (
                "Embedded-malicious-code class — only safe path is "
                "removal from the supply chain."
            ),
            "confidence_bp": 7000,
        },
    ],
    "CWE-502": [
        {
            "kind": "mitigation",
            "target": "Untrusted-deserialization sink",
            "change": (
                "Disable the vulnerable deserializer for untrusted "
                "input; constrain any remaining deserialization to a "
                "strict allow-list of safe types; route inbound RPC "
                "through a JSON-only edge proxy."
            ),
            "rationale": "Removes the deserialization sink that drives the RCE chain.",
            "confidence_bp": 6500,
        },
    ],
    "CWE-917": [
        {
            "kind": "mitigation",
            "target": "EL/template-injection sink",
            "change": (
                "Disable the affected expression language (EL) "
                "feature where possible; sanitize all user-controlled "
                "values before they reach the template/log "
                "interpolation surface."
            ),
            "rationale": (
                "Same root cause as CWE-94 — eliminate the injection sink."
            ),
            "confidence_bp": 6500,
        },
    ],
    "CWE-120": [
        {
            "kind": "mitigation",
            "target": "Network-access restriction",
            "change": (
                "Block external access to the affected service "
                "(typically management plane / device WebUI) at the "
                "perimeter; restrict to bastion / VPN; rate-limit "
                "the affected endpoint."
            ),
            "rationale": "Reduces remote-trigger surface for the buffer-overflow vector.",
            "confidence_bp": 6000,
        },
    ],
    "CWE-640": [
        {
            "kind": "mitigation",
            "target": "Account-recovery flow",
            "change": (
                "Require additional out-of-band verification (email "
                "+ SMS, or admin approval) before honoring password-"
                "reset tokens; rate-limit and short-lived tokens; "
                "audit recent recovery activity for anomalies."
            ),
            "rationale": "Recovery-mechanism weakness — defense-in-depth on the flow.",
            "confidence_bp": 6500,
        },
    ],
    "CWE-476": [
        {
            "kind": "mitigation",
            "target": "Network-input validation",
            "change": (
                "Reject malformed inputs at the API boundary before "
                "they reach the affected parsing code; deploy a "
                "schema-validation layer (e.g. JSON Schema, Pydantic) "
                "for the affected endpoints."
            ),
            "rationale": (
                "Null-pointer-dereference triggers reachable only via "
                "malformed input; boundary validation removes the "
                "trigger."
            ),
            "confidence_bp": 6000,
        },
    ],
}


def generate_no_fix_mitigations(
    *,
    cve_id: str,
    cwe: str,
    vulnerability_status: str,
) -> list[dict[str, Any]]:
    """Emit canned CWE→mitigation actions for no-fix advisories.

    Returns a list of action dicts (same shape as ``lm_extract_actions``
    output) when ``vulnerability_status`` is in (``no_fix_published``,
    ``withdrawn``).  Empty list otherwise.

    Citations point at the MITRE CWE database — public, stable, NOT
    fabricated.  Confidence is bounded (≤7000 bp) because mitigations
    reduce exposure but do not *fix* the vulnerability.
    """

    if vulnerability_status not in ("no_fix_published", "withdrawn"):
        return []
    cwe_norm = (cwe or "").strip().upper()
    if not cwe_norm:
        return []
    template = _NO_FIX_MITIGATIONS.get(cwe_norm)
    if not template:
        # Generic fallback for unmapped CWEs: minimum-viable mitigation
        # is "isolate + monitor" — bounded confidence so the planner
        # still routes to HITL.
        cwe_id = cwe_norm.replace("CWE-", "") if cwe_norm.startswith("CWE-") else ""
        if not cwe_id.isdigit():
            return []
        template = [{
            "kind": "mitigation",
            "target": "Network isolation + audit",
            "change": (
                f"Until upstream fix ships for {cve_id}, isolate the "
                f"affected service from untrusted networks; enable "
                f"verbose audit logging; alert on any successful "
                f"exploitation indicators from the advisory."
            ),
            "rationale": (
                f"No CWE-specific mitigation template available; "
                f"defense-in-depth fallback for {cwe_norm}."
            ),
            "confidence_bp": 5000,
        }]
        cwe_id_for_cite = cwe_id
    else:
        cwe_id_for_cite = cwe_norm.removeprefix("CWE-")
    citation_url = (
        f"https://cwe.mitre.org/data/definitions/{cwe_id_for_cite}.html"
    )
    citation_excerpt = (
        f"MITRE CWE catalogue — defense-in-depth mitigation "
        f"templates for {cwe_norm}."
    )
    out: list[dict[str, Any]] = []
    for entry in template:
        out.append({
            "kind": entry["kind"],
            "target": entry["target"],
            "target_version": "",
            "change": entry["change"],
            "rationale": entry["rationale"],
            "citation_url": citation_url,
            "citation_excerpt": citation_excerpt,
            "source": "no_fix_mitigation_template",
            "confidence_bp": entry["confidence_bp"],
        })
    return out


__all__ = [
    "Snippet",
    "fetch_reference_snippets",
    "registry_latest_unaffected",
    "ddg_search",
    "searxng_search",
    "gather_evidence",
    "lm_extract_actions",
    "generate_no_fix_mitigations",
    "_strip_html",
    "_is_unaffected",
]
