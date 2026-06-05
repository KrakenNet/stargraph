// graph-data.jsx — cve-rem workgraph definition + simulated run content.
//
// Mirrors the demos.cve_remediation.graph.harbor.yaml topology, condensed to
// the 11 most representative nodes (one per harbor node-kind family). Each
// node maps onto one of the design's six view types:
//
//   source         → harbor `tool` over a feed/source (NVD/EPSS/Nautobot)
//   llm            → harbor `dspy` module (extractors, classifiers)
//   graph_traverse → harbor `tool` over a knowledge graph (KG / vec store)
//   agent_loop     → harbor `dspy` ReAct loop (planner, remediation discovery)
//   tool           → harbor `tool` / `broker` / `write_artifact`
//   decision       → harbor `passthrough` branch gate (SSVC tier)
//
// In `?run=<run_id>` mode the WS bridge overrides node statuses and clock
// from the live event stream; the simulated content below is the offline
// fallback used by the design page.

const WORKGRAPH = {
  name: "cve-rem.remediation",
  runId: "run_cve_log4j_2026_05_19",
  startedAt: "14:22:07",
  nodes: [
    { id: "start", type: "start", label: "start",
      subtitle: "run entered from cron · CVE-2021-44228",
      op: "trigger:cron",
      actor: "system" },

    { id: "intake_fetch", type: "source", label: "intake_fetch", duration: 4,
      subtitle: "fetch NVD JSON 2.0 + EPSS + KEV by cve_id",
      op: "GET · nvd.nist.gov/vuln/detail",
      actor: "feed · nvd+epss+kev",
      highlights: [
        "CVE-2021-44228 · published 2021-12-10 · KEV listed",
        "CVSS 10.0 · EPSS 0.97543 · 7 references",
      ],
      stats: () => [
        { k: "refs",   v: "7" },
        { k: "cvss",   v: "10.0" },
        { k: "epss",   v: ".975" },
        { k: "ms",     v: "182" },
      ] },

    { id: "extract_trusted", type: "llm", label: "extract_trusted", duration: 6,
      subtitle: "schema-constrained extractor on canonicalised text",
      op: "DSPy · ExtractCveFields",
      actor: "model · claude-haiku-4.5",
      highlights: [
        "cwe=CWE-502 (deserialization) · vector=network",
        "products[] = log4j-core · range = (,2.15.0)",
      ],
      stats: () => [
        { k: "in",   v: "2,140" },
        { k: "out",  v: "212" },
        { k: "conf", v: ".96" },
        { k: "t/s",  v: "118" },
      ] },

    { id: "correlate_assets", type: "graph_traverse", label: "correlate_assets", duration: 9,
      subtitle: "Nautobot + CMDB reachability via Nautilus broker",
      op: "BROKER · nautilus.broker_request",
      actor: "broker · nautilus@9100",
      highlights: [
        "12 hosts impacted · 3 reachable from internet",
        "primary cluster · prod-edge · runtime=java/11",
      ],
      stats: () => [
        { k: "hosts",  v: "12" },
        { k: "edge",   v: "3" },
        { k: "klass",  v: "java" },
        { k: "ms",     v: "612" },
      ] },

    { id: "ssvc_evaluate", type: "decision", label: "ssvc_evaluate", duration: 2,
      subtitle: "Fathom rule-eval (cache hit) | ML pre-classifier (miss)",
      op: "IF · ssvc.tier",
      actor: "rules · Fathom",
      highlights: ["evaluated act_auto → plan_template_lookup"],
      stats: () => [{ k: "tier", v: "act_auto" }] },

    { id: "plan_template_lookup", type: "graph_traverse", label: "plan_template_lookup", duration: 5,
      subtitle: "Plan-KG match cwe × asset-class × runtime",
      op: "KG · plan.match(cwe,class,runtime)",
      actor: "kg · plan-kg",
      highlights: [
        "template_lookup_hit · plan-tpl/java-deser-upgrade",
        "3 prior plans · 1 retro overlap (CVE-2021-45046)",
      ],
      stats: () => [
        { k: "tpls",  v: "3" },
        { k: "prior", v: "1" },
        { k: "score", v: ".91" },
        { k: "ms",    v: "84" },
      ] },

    { id: "remediation_discovery", type: "agent_loop", label: "remediation_discovery", duration: 32,
      subtitle: "4-source agentic search → LM JSON extraction",
      op: "AGENT · DSPy ReAct",
      actor: "agent · claude-sonnet-4.6",
      loop: true,
      iterations: [
        { n: 1, status: "done",    summary: "advisory refs · drafted upgrade 2.17.1",  ms: 7400 },
        { n: 2, status: "done",    summary: "registry probe · maven central confirms", ms: 9200 },
        { n: 3, status: "running", summary: "DDG + SearXNG cross-check · fetching",   ms: null },
        { n: 4, status: "pending" },
      ],
      currentAction: (t) => {
        if (t < 4)   return { verb: "fetching", target: "advisory refs · apache.org/log4j" };
        if (t < 10)  return { verb: "probing",  target: "registry · maven-central · log4j-core" };
        if (t < 18)  return { verb: "searching", target: "duckduckgo · log4j 2.17.1 fix" };
        if (t < 26)  return { verb: "searching", target: "searxng · CVE-2021-44228 patch" };
        return        { verb: "synthesizing", target: "LM extract → upgrade target=2.17.1" };
      },
      highlights: [
        "candidates · upgrade=2.17.1 (high) · downgrade=2.15.0 (med)",
        "4 sources cross-validated · no_fix_published=false",
      ],
      stats: () => [
        { k: "iter",  v: "3/4" },
        { k: "src",   v: "4" },
        { k: "tokens", v: "8.4k" },
        { k: "tools", v: "11" },
      ] },

    { id: "sandbox_run", type: "tool", label: "sandbox_run", duration: 12,
      subtitle: "docker exec patch verification in cve-rem-sandbox",
      op: "SH · docker run · cargonet/log4j-2.14",
      actor: "shell · cargonet sandbox",
      highlights: [
        "BEFORE · CVE reproduces · JNDI lookup fires",
        "AFTER  · patched to 2.17.1 · CVE blocked",
      ],
      stats: () => [
        { k: "before", v: "vuln" },
        { k: "after",  v: "clean" },
        { k: "drift",  v: "0" },
        { k: "ms",     v: "11,820" },
      ] },

    { id: "retro_analysis", type: "tool", label: "retro_analysis", duration: 5,
      subtitle: "detector signals + LM prevention suggestions (citation-bound)",
      op: "TOOL · retro.analyse_failure",
      actor: "tool · retro_analysis",
      highlights: [
        "signal · static_detection_skip · cargonet image-class miss",
        "suggestion · add lab profile java/11-corretto (cite: F12 outcomes log)",
      ],
      stats: () => [
        { k: "signals", v: "2" },
        { k: "sugg",    v: "3" },
        { k: "cites",   v: "3" },
        { k: "ms",      v: "612" },
      ] },

    { id: "open_change_request", type: "tool", label: "open_change_request", duration: 4,
      branchOf: "ssvc_evaluate", branchLabel: "act_auto",
      subtitle: "create ServiceNow CR · attach Doc+ + retro",
      op: "POST · servicenow.change_request",
      actor: "sn · oauth · krakntrust",
      highlights: [
        "→ CHG0041997 awaiting CAB · assigned platform-sec",
        "attachments · Doc+ vuln-summary · retro-suggestions.json",
      ],
      stats: () => [
        { k: "cr",  v: "CHG0041997" },
        { k: "att", v: "3" },
        { k: "cab", v: "queued" },
      ] },

    { id: "tier_terminal_track", type: "tool", label: "tier_terminal_track", duration: 3,
      branchOf: "ssvc_evaluate", branchLabel: "track",
      subtitle: "exposure-monitor only · no immediate action",
      op: "TOOL · exposure_monitor.enqueue",
      actor: "tool · exposure_monitor",
      highlights: ["would re-evaluate at next EPSS shift (not taken)"],
      stats: () => [{ k: "next", v: "+7d" }] },

    { id: "end", type: "end", label: "end",
      subtitle: "run complete · KG + Doc+ + retro persisted",
      op: "exit:0",
      actor: "system" },
  ],

  edges: [
    { from: "start",                to: "intake_fetch" },
    { from: "intake_fetch",         to: "extract_trusted" },
    { from: "extract_trusted",      to: "correlate_assets" },
    { from: "correlate_assets",     to: "ssvc_evaluate" },
    { from: "ssvc_evaluate",        to: "plan_template_lookup", label: "act_auto" },
    { from: "ssvc_evaluate",        to: "tier_terminal_track",  label: "track" },
    { from: "plan_template_lookup", to: "remediation_discovery" },
    { from: "remediation_discovery", to: "sandbox_run" },
    { from: "sandbox_run",          to: "retro_analysis" },
    { from: "retro_analysis",       to: "open_change_request" },
    { from: "remediation_discovery", to: "remediation_discovery", label: "retry", kind: "loop" },
    { from: "open_change_request",  to: "end" },
    { from: "tier_terminal_track",  to: "end" },
  ],
};

// Compute cumulative start/end so the simulated clock can map t → currentNode.
(() => {
  let t = 0;
  for (const n of WORKGRAPH.nodes) {
    n.startAt = t;
    n.endAt = t + (n.duration || 0);
    t = n.endAt;
  }
  WORKGRAPH.totalDuration = t;
})();

// ─── Per-node "live" content (drives node-views) ────────────────────────────

const NODE_CONTENT = {
  intake_fetch: {
    system: "https://services.nvd.nist.gov/rest/json/cves/2.0",
    role: "readonly_bot",
    query:
`GET /rest/json/cves/2.0?cveId=CVE-2021-44228
  Accept: application/json
  X-Source-Trust: trusted

merged feeds:
  ↳ EPSS  https://api.first.org/data/v1/epss?cve=CVE-2021-44228
  ↳ KEV   https://www.cisa.gov/known-exploited-vulnerabilities-catalog`,
    rows: [
      { id: "CVE-2021-44228", subject: "Log4Shell · JNDI lookup remote code execution",
        priority: "critical", plan: "kev-listed", email: "secure@apache.org",
        attachments: 7 },
    ],
    stats: [
      { k: "refs",      v: "7" },
      { k: "cvss",      v: "10.0" },
      { k: "epss",      v: "0.97543" },
      { k: "kev",       v: "yes" },
    ],
    cvss: 10.0,
    epss: 0.97543,
    kevListed: true,
    published: "2021-12-10",
    feeds: [
      { id: "nvd",  label: "NVD",  url: "services.nvd.nist.gov", doneAt: 0.30, value: "CVSS 10.0" },
      { id: "epss", label: "EPSS", url: "api.first.org",         doneAt: 0.60, value: "0.97543" },
      { id: "kev",  label: "KEV",  url: "cisa.gov",              doneAt: 0.85, value: "listed" },
    ],
  },

  extract_trusted: {
    model: "claude-haiku-4.5",
    promptTokens: 2140,
    systemPreview:
      "You are a CVE field extractor for the Kraken Networks cve-rem pipeline. " +
      "Given a canonicalised NVD record + advisory text, output JSON validated " +
      "against ExtractedCve: { cwe, vector, products[], version_ranges[], " +
      "summary, source_confidence }. Do not invent fields not present in the source.",
    streamingResponse:
`{
  "cwe": "CWE-502",
  "vector": "network",
  "products": [
    { "vendor": "apache", "product": "log4j-core" }
  ],
  "version_ranges": [
    { "lt": "2.15.0", "vulnerable": true }
  ],
  "summary": "Lookup parsing in log4j-core allows attacker-controlled JNDI URIs
              (ldap://, rmi://) leading to remote class loading and RCE.",
  "source_confidence": 0.96
}`,
    reasoning: [
      "Title and description mention \"JNDI lookup\" → CWE-502 (deserialization of untrusted data).",
      "Network vector confirmed by CVSS AV:N and references to remote LDAP/RMI servers.",
      "Affected version range from NVD CPE: < 2.15.0 (2.15.0 partial, 2.17.1 full).",
      "Trusted source (NVD + Apache advisory) — emitting watermark clean=true.",
    ],
    extracted: {
      cwe: "CWE-502",
      vector: "network",
      products: ["log4j-core"],
      versionRange: "< 2.15.0",
      confidence: 0.96,
    },
    signature: {
      inputs: ["canonical_text: str"],
      outputs: ["cwe: str", "vector: str", "products: Product[]", "version_ranges: Range[]", "summary: str", "source_confidence: float"],
    },
  },

  correlate_assets: {
    root: "nautilus://broker/correlate_assets",
    indexedAt: "live",
    visited: [
      { path: "nautobot://devices/prod-edge-01",      score: 0.98, hit: "java/11-corretto · log4j 2.14.1", sourceType: "nautobot" },
      { path: "nautobot://devices/prod-edge-02",      score: 0.97, hit: "java/11-corretto · log4j 2.14.1", sourceType: "nautobot" },
      { path: "nautobot://devices/prod-edge-03",      score: 0.96, hit: "java/11-corretto · log4j 2.14.1", sourceType: "nautobot" },
      { path: "cmdb://services/order-api",            score: 0.81, hit: "transitive log4j 2.13.3 via spring-boot 2.5", sourceType: "cmdb" },
      { path: "cmdb://services/billing-worker",       score: 0.74, hit: "transitive log4j 2.14.0 via kafka-clients", sourceType: "cmdb" },
      { path: "nautilus://reachability/edge→billing", score: 0.62, hit: "south-bound TLS 443 · attestation ed25519:7c91", sourceType: "reachability" },
    ],
    edgesFollowed: 28,
    nodesExpanded: 41,
    totalHosts: 5,
    exposedCount: 3,
    assetClasses: ["nautobot device", "cmdb service"],
    attestation: "ed25519:7c91…ab8d",
  },

  ssvc_evaluate: {
    condition:
`ssvc.tier(
  exploitation = "active",         /* KEV listed */
  exposure     = "open",            /* reachable from internet */
  utility      = "super-effective", /* PoC widely available */
  human_impact = "very-high"        /* RCE on prod cluster */
) → act_auto`,
    evaluated: "act_auto",
    branches: [
      { label: "act_auto",  target: "plan_template_lookup", taken: true },
      { label: "act_supervised", target: "hitl_remediation_review", taken: false },
      { label: "track",     target: "tier_terminal_track",  taken: false },
      { label: "defer",     target: "tier_terminal_defer",  taken: false },
    ],
    dimensions: [
      { name: "exploitation", value: "active",          level: 1.0 },
      { name: "exposure",     value: "open",            level: 1.0 },
      { name: "utility",      value: "super-effective", level: 1.0 },
      { name: "human_impact", value: "very-high",       level: 1.0 },
    ],
    evalMethod: "fathom_cache_hit",
    evalRule: "ssvc_v2.clp",
    evalLatencyMs: 2,
    evalConfidence: 1.00,
  },

  plan_template_lookup: {
    root: "kg://plan-kg/templates",
    indexedAt: "32 min ago",
    visited: [
      { path: "plan-tpl/java-deser-upgrade",          score: 0.91, hit: "MATCH (t)-[:APPLIES_TO]->(c:Cwe{id:'CWE-502'})", t: 1, isRetro: false },
      { path: "plan-tpl/log4j-family-upgrade",        score: 0.88, hit: "product=log4j-core · range_lt=2.17.1", t: 2, isRetro: false },
      { path: "retro://CVE-2021-45046/plan",          score: 0.83, hit: "PRIOR · upgrade=2.16.0 → drift detected, bumped", t: 3, isRetro: true },
      { path: "plan-tpl/transitive-bom-bump",         score: 0.71, hit: "spring-boot.parent → log4j-bom 2.17.1", t: 4, isRetro: false },
    ],
    edgesFollowed: 12,
    nodesExpanded: 19,
    queryInputs: { cwe: "CWE-502", assetClass: "java", runtime: "corretto-11" },
    query: "MATCH (t:PlanTemplate)-[:APPLIES_TO]->(:Cwe{id:'CWE-502'})-[:ON]->(:AssetClass{id:'java'})-[:RUNTIME]->(:Runtime{id:'corretto-11'}) RETURN t ORDER BY t.score DESC LIMIT 5",
    winner: { path: "plan-tpl/java-deser-upgrade", score: 0.91 },
    retroOverlap: { cve: "CVE-2021-45046", insight: "upgrade=2.16.0 → drift detected, bumped to 2.17.1" },
  },

  remediation_discovery: {
    iteration: 3,
    maxIterations: 4,
    elapsed: 26,
    tokens: 8420,
    file: "candidates.json",
    sources: [
      { id: "advisory", label: "advisory refs", sublabel: "apache.org",     confirmedAt: 3,  version: "2.17.1", detail: "recommends 2.17.1 (full fix)" },
      { id: "registry", label: "registry",      sublabel: "maven-central",  confirmedAt: 7,  version: "2.17.1", detail: "latest=2.17.1 published 2021-12-28" },
      { id: "ddg",      label: "DDG",           sublabel: "web search",     confirmedAt: 12, version: "2.17.1", detail: "3/6 results corroborate" },
      { id: "searxng",  label: "SearXNG",       sublabel: "meta-search",    confirmedAt: 18, version: "2.17.1", detail: "4/5 top results converge" },
    ],
    consensus: { version: "2.17.1", confidence: "high", sourceCount: 4 },
    thoughts: [
      { t: 0,  k: "plan",  text: "Need a deterministic upgrade target for log4j-core < 2.15.0. Strategy: probe 4 sources (advisory refs, maven registry, DDG, SearXNG) → LM extract." },
      { t: 3,  k: "read",  text: "Fetched apache.org/log4j/security.html → recommends 2.17.1 (full fix, includes 2.16 / 2.17 fixes)." },
      { t: 7,  k: "read",  text: "maven-central API · log4j-core latest=2.17.1 published 2021-12-28, satisfies range." },
      { t: 12, k: "read",  text: "DDG search 'log4j 2.17.1 fix CVE-2021-44228' → 6 results, 3 corroborate 2.17.1 as recommended." },
      { t: 18, k: "read",  text: "SearXNG cross-check · 4/5 top results converge on 2.17.1; one outlier suggests 2.16.0 (incomplete fix)." },
      { t: 23, k: "plan",  text: "Cross-source confidence high. LM extract: upgrade=2.17.1 (high), fallback=downgrade-not-applicable (no_fix_published=false)." },
      { t: 26, k: "write", text: "Promoted upgrade target=2.17.1 with citations · fixed_version field set on CVE record." },
    ],
    code:
`// candidates.json                                                  [emitted]
{
  "cve_id": "CVE-2021-44228",
  "candidates": [
    {
      "kind": "upgrade",
      "target_version": "2.17.1",
      "confidence": "high",
      "sources": [
        "https://logging.apache.org/log4j/2.x/security.html",
        "https://search.maven.org/artifact/org.apache.logging.log4j/log4j-core/2.17.1",
        "https://duckduckgo.com/?q=log4j+2.17.1+fix+CVE-2021-44228",
        "https://searx.be/search?q=CVE-2021-44228+patch"
      ],
      "rationale": "All four sources converge on 2.17.1 as the full fix.
                    2.15.0 is partial; 2.16.0 still vulnerable to CVE-2021-45046."
    },
    {
      "kind": "downgrade",
      "target_version": null,
      "no_fix_published": false
    }
  ],
  "promoted": { "fixed_version": "2.17.1" }
}█`,
    tests: [
      { name: "advisory_refs.fetch ok",                                status: "pass", ms: 280 },
      { name: "registry.latest_version ok",                            status: "pass", ms: 410 },
      { name: "ddg.search returns ≥3 corroborating results",           status: "pass", ms: 620 },
      { name: "searxng.search returns ≥3 corroborating results",       status: "pass", ms: 580 },
      { name: "lm_extract.upgrade_target == 2.17.1",                   status: "running", ms: null },
    ],
    logs: [
      "[14:22:23] tool/fetch_advisory  apache.org/log4j/security.html",
      "[14:22:25] tool/probe_registry  maven-central/log4j-core",
      "[14:22:31] tool/ddg_search      'log4j 2.17.1 CVE-2021-44228'",
      "[14:22:38] tool/searxng_search  'CVE-2021-44228 patch'",
      "[14:22:44] tool/lm_extract       schema=RemediationCandidate",
      "[14:22:49] result: upgrade=2.17.1 (conf=high)",
      "[14:22:50] promote · fixed_version=2.17.1",
    ],
  },

  sandbox_run: {
    cmd: "docker run --rm cargonet/log4j-2.14:cve-2021-44228 -- /sandbox/verify.sh 2.17.1",
    image: "cargonet/log4j-2.14:cve-2021-44228",
    imageSha: "7c91…ab8d",
    phases: [
      { id: "before", label: "BEFORE", result: "VULNERABLE", detail: "JNDI lookup FIRED · RCE class loaded", expected: true, doneAt: 0.30 },
      { id: "apply",  label: "APPLY",  result: "2.14.1 → 2.17.1", detail: "rebuild ok · 1 module · 4.2s", doneAt: 0.60 },
      { id: "after",  label: "AFTER",  result: "CLEAN", detail: "lookups disabled · no JNDI fired", doneAt: 0.90 },
    ],
    divergence: { drift: 0, sig: "cargonet ↔ prod ed25519:7c91 match" },
    output:
`[sandbox] image cargonet/log4j-2.14:cve-2021-44228 (sha256:7c91…ab8d)
[sandbox] phase=BEFORE
   ↳ launching JNDI test harness · ldap://attacker:1389/Exploit
   ↳ log4j-core 2.14.1 · JNDI lookup FIRED · RCE class loaded
   ↳ result: VULNERABLE (expected)

[sandbox] phase=APPLY_PATCH target=2.17.1
   ↳ mvn dependency:tree → log4j-core 2.14.1 → 2.17.1
   ↳ rebuild ok · 1 module · 4.2s

[sandbox] phase=AFTER
   ↳ launching JNDI test harness · ldap://attacker:1389/Exploit
   ↳ log4j-core 2.17.1 · lookups disabled · no JNDI fired
   ↳ result: CLEAN

[sandbox] divergence_check: 0 prod drift (cargonet ↔ prod ed25519:7c91 match)
[sandbox] exit 0 · 11820ms`,
    exitCode: 0,
  },

  retro_analysis: {
    tool: "retro.analyse_failure",
    args: {
      cve_id: "CVE-2021-44228",
      observable_state: {
        sandbox_skipped: false,
        static_detection_status: "ok",
        framework_mapping_status: "ok",
        graph_prior_hits: 1,
      },
      detector_signals: [
        "static_detection_skip · false",
        "retro_template_lookup_hit · true (CVE-2021-45046)",
      ],
    },
    response: null,
  },

  open_change_request: {
    tool: "servicenow.change_request",
    args: {
      table: "change_request",
      short_description: "[cve-rem] CVE-2021-44228 · upgrade log4j-core to 2.17.1",
      assignment_group: "platform-sec",
      cmdb_ci: ["prod-edge-01", "prod-edge-02", "prod-edge-03"],
      attachments: [
        "doc+://collection/vuln-summaries/CVE-2021-44228",
        "artifact://retro/CVE-2021-44228/suggestions.json",
        "artifact://sandbox/CVE-2021-44228/verify.log",
      ],
      work_notes: "Auto-opened by cve-rem run_cve_log4j_2026_05_19 (signed ed25519:7c91…)",
    },
    response: null,
  },

  tier_terminal_track: {
    tool: "exposure_monitor.enqueue",
    args: {
      cve_id: "CVE-2021-44228",
      reevaluate_at: "+7d",
      reason: "tier=track · no reachable exposure",
    },
    response: null,
  },
};

// Map real cve-rem graph node ids (70 of them, from harbor.yaml) onto the
// 10 design stages so the watcher reads a live TransitionEvent stream and
// advances the matching stage card. Unmapped ids are ignored (the clock
// only ever advances forward).
const REAL_TO_STAGE = {
  halt_new_gate: "intake_fetch",
  intake_fetch: "intake_fetch",
  source_trust_gate: "intake_fetch",

  canonicalize_trusted: "extract_trusted",
  extract_trusted: "extract_trusted",
  enrich_cve_trusted: "extract_trusted",
  source_trust_audit: "extract_trusted",
  canonicalize_untrusted: "extract_trusted",
  emit_quarantine_artifact: "extract_trusted",
  extract_untrusted: "extract_trusted",
  injection_classify: "extract_trusted",
  critique_extracted: "extract_trusted",
  enrich_cve_untrusted: "extract_trusted",
  hitl_ingest_review: "extract_trusted",
  branch_resp_ingest: "extract_trusted",

  correlate_assets: "correlate_assets",
  suppress_not_applicable: "correlate_assets",

  ssvc_evaluate: "ssvc_evaluate",
  tier_terminal_track: "tier_terminal_track",
  tier_terminal_defer: "tier_terminal_track",

  plan_template_lookup: "plan_template_lookup",

  mcp_retrieval_dispatch: "remediation_discovery",
  vec_search_retros: "remediation_discovery",
  graph_prior_remediations: "remediation_discovery",
  graph_blast_radius: "remediation_discovery",
  framework_mapping: "remediation_discovery",
  cargonet_lab_telemetry: "remediation_discovery",
  planner: "remediation_discovery",
  remediation_discovery: "remediation_discovery",
  code_writer: "remediation_discovery",
  emit_remediation_bundle: "remediation_discovery",
  critic: "remediation_discovery",
  hitl_plan_review: "remediation_discovery",
  branch_resp_plan: "remediation_discovery",
  validate_dispatch: "remediation_discovery",
  judge_safety: "remediation_discovery",
  judge_lint: "remediation_discovery",
  validate_plan_join: "remediation_discovery",
  plan_quarantine_gate: "remediation_discovery",

  sandbox_dispatch: "sandbox_run",
  sandbox_run: "sandbox_run",
  sandbox_skip: "sandbox_run",
  emit_sandbox_evidence: "sandbox_run",

  write_retrospective: "retro_analysis",
  retro_dispatch: "retro_analysis",
  retro_join: "retro_analysis",
  emit_retro_payload: "retro_analysis",
  kg_run_writeback: "retro_analysis",
  render_docx: "retro_analysis",
  emit_docx_archive: "retro_analysis",
  publish_docplus: "retro_analysis",
  cargonet_writeback: "retro_analysis",
  plan_kg_writeback: "retro_analysis",
  krakntrust_attest: "retro_analysis",
  run_outcome_persist: "retro_analysis",
  cr_self_validate: "retro_analysis",
  emit_proof_report: "retro_analysis",
  hitl_retrospective_review: "retro_analysis",
  branch_resp_retro: "retro_analysis",

  create_change_request: "open_change_request",
  emit_evidence_bundle: "open_change_request",
  attach_all_artifacts: "open_change_request",
  hitl_change_approval: "open_change_request",
  branch_resp_change: "open_change_request",
  progressive_execute: "open_change_request",
  partial_apply_rollback: "open_change_request",
  verify_immediate: "open_change_request",
  divergence_quarantine: "open_change_request",
  drift_watch_spawn: "open_change_request",

  action_done: "end",
};

Object.assign(window, { WORKGRAPH, NODE_CONTENT, REAL_TO_STAGE });
