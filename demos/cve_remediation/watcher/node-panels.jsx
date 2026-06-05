// node-panels.jsx — per-node panel dispatcher + shared constants.
//
// Dispatch order (D1): priority-id → cargonet-id → family → OutcomePanel fallback.
// All panels receive uniform 7-prop shape (D2):
//   {node, profile, status, delta, events, timing, runState, runTerminal}
// Exposed on window.* for buildless React (no module imports).

// ─── ID sets ──────────────────────────────────────────────────────────────

const PRIORITY_IDS = new Set([
  "intake_fetch",
  "canonicalize_trusted",
  "canonicalize_untrusted",
  "extract_trusted",
  "extract_untrusted",
  "enrich_cve_trusted",
  "enrich_cve_untrusted",
  "emit_quarantine_artifact",
  "injection_classify",
  "critique_extracted",
  "hitl_ingest_review",
  "hitl_plan_review",
  "hitl_change_approval",
  "hitl_retrospective_review",
  "correlate_assets",
  "graph_blast_radius",
  "framework_mapping",
  "cargonet_lab_telemetry",
  "planner",
  "code_writer",
  "emit_remediation_bundle",
  "validate_dispatch",
  "judge_safety",
  "judge_lint",
  "sandbox_skip",
  "emit_sandbox_evidence",
  "ssvc_evaluate",
  "vec_search_retros",
  "graph_prior_remediations",
  "sandbox_run",
  "create_change_request",
  "write_retrospective",
  "krakntrust_attest",
  "drift_watch_spawn",
  "partial_apply_rollback",
  "divergence_quarantine",
  "kg_run_writeback",
  "emit_retro_payload",
  "render_docx",
  "emit_docx_archive",
  "publish_docplus",
  "cargonet_writeback",
  "plan_kg_writeback",
  "run_outcome_persist",
  "sandbox_dispatch",
  "verify_immediate",
  "retro_dispatch",
  "emit_proof_report",
  "progressive_execute",
]);

const CARGONET_IDS = new Set([
  "cargonet_lab_telemetry",
  "emit_sandbox_evidence",
  "cargonet_writeback",
]);

// ─── Micro-components ────────────────────────────────────────────────────

/** PanelCard — local card wrapper using .panel CSS classes. */
function PanelCard({ title, right, children, className = "", scroll = false, mono = false, max = "" }) {
  return (
    <section className={"panel " + className}>
      {(title || right) && (
        <header className="panel-h">
          <span className="panel-t">{title}</span>
          {right && <span className="panel-r">{right}</span>}
        </header>
      )}
      <div className={"panel-b " + (scroll ? "is-scroll " : "") + (mono ? "is-mono" : "") + (max=="40" ? "max-40" : "") }>
        {children}
      </div>
    </section>
  );
}

/** KV — key-value list using .kv dl/dt/dd pattern. */
function KV({ pairs }) {
  const filtered = pairs.filter(([, v]) => v != null && v !== "" && v !== undefined);
  if (filtered.length === 0) return null;
  return (
    <dl className="kv">
      {filtered.map(([label, value, opts], i) => (
        <React.Fragment key={i}>
          <dt>{label}</dt>
          <dd className={opts?.mono !== false ? "mono" : ""} data-field={opts?.field}>{
            typeof value === "object" ? JSON.stringify(value) : String(value)
          }</dd>
        </React.Fragment>
      ))}
    </dl>
  );
}

/** ErrorBanner — consistent error display. */
function ErrorBanner({ field, value }) {
  if (!value) return null;
  return (
    <div className="panel" style={{ borderColor: "var(--err)" }}>
      <div className="panel-b" style={{ color: "var(--err)", fontSize: "12.5px" }}>
        {field && <strong>{field}: </strong>}{value}
      </div>
    </div>
  );
}

/** StatCards — stats grid using .statsbar. */
function StatCards({ stats }) {
  const filtered = stats.filter(s => s.v != null);
  if (filtered.length === 0) return null;
  return (
    <div className="statsbar">
      {filtered.map((s, i) => (
        <div className="stat" key={i}>
          <div className="stat-v mono">{s.v}</div>
          <div className="stat-k">{s.k}</div>
        </div>
      ))}
    </div>
  );
}

/** cellText — coerce arbitrary cell values to renderable strings. */
function cellText(v) {
  if (v == null) return "";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") return String(v);
  try { return JSON.stringify(v); } catch { return String(v); }
}

/** MarkdownView — render markdown via marked + DOMPurify. */
function MarkdownView({ source }) {
  const html = React.useMemo(() => {
    if (!source) return "";
    if (typeof window === "undefined" || !window.marked || !window.DOMPurify) {
      return ""; // libs not loaded; caller falls back to raw text
    }
    try {
      const raw = window.marked.parse(String(source), { gfm: true, breaks: true });
      return window.DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
    } catch (e) {
      return "";
    }
  }, [source]);
  if (!html) {
    return <pre className="code" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{source || ""}</pre>;
  }
  return <div className="md" dangerouslySetInnerHTML={{ __html: html }} />;
}

/** DocxPreview — fetch a DOCX artifact ref, render via mammoth.js (lazy). */
function DocxPreview({ artifactRef }) {
  const [state, setState] = React.useState({ status: "idle", html: "", err: "" });
  React.useEffect(() => {
    if (!artifactRef) { setState({ status: "empty", html: "", err: "" }); return; }
    let cancelled = false;
    (async () => {
      setState({ status: "loading", html: "", err: "" });
      try {
        if (!window.mammoth) {
          await new Promise((resolve, reject) => {
            const s = document.createElement("script");
            s.src = "https://cdn.jsdelivr.net/npm/mammoth@1.8.0/mammoth.browser.min.js";
            s.integrity = "sha384-/cXAMbzovUIKbBERjPmR3SnPTh8siWr5lsvFYj1Uq4XP0yaJUZJmsh0YXyGv5P0y";
            s.crossOrigin = "anonymous";
            s.onload = resolve;
            s.onerror = () => reject(new Error("mammoth load failed"));
            document.head.appendChild(s);
          });
        }
        const res = await fetch(window.apiUrl("/watch/api/artifact?ref=" + encodeURIComponent(artifactRef)));
        if (!res.ok) throw new Error("fetch " + res.status);
        const buf = await res.arrayBuffer();
        const result = await window.mammoth.convertToHtml({ arrayBuffer: buf });
        const safe = window.DOMPurify
          ? window.DOMPurify.sanitize(result.value, { USE_PROFILES: { html: true } })
          : result.value;
        if (!cancelled) setState({ status: "ok", html: safe, err: "" });
      } catch (e) {
        if (!cancelled) setState({ status: "err", html: "", err: String(e?.message || e) });
      }
    })();
    return () => { cancelled = true; };
  }, [artifactRef]);

  if (state.status === "empty") return <div className="muted">no DOCX artifact</div>;
  if (state.status === "loading") return <div className="muted">loading DOCX…</div>;
  if (state.status === "err") return <div style={{ color: "var(--err)", fontSize: 12 }}>DOCX render failed: {state.err}</div>;
  return <div className="docx-md" dangerouslySetInnerHTML={{ __html: state.html }} />;
}

/** PendingState — consistent pending/running_empty/done_empty displays. */
function PendingState({ family, lifecycle, panelId }) {
  return (
    <div data-panel-id={panelId}>
      <div className="nv-pending">
        <div className="nv-pending-card">
          <div className="nv-pending-icon">◌</div>
          <div className="nv-pending-title">{emptyCopy(family, lifecycle)}</div>
        </div>
      </div>
    </div>
  );
}

/** Pill — inline badge. */
function Pill({ tone, children }) {
  return <span className={"pill pill-" + tone}>{children}</span>;
}

/** DataTable — reusable table with header row and data rows. */
function DataTable({ headers, rows, dataAttrs }) {
  return (
    <table className="nv-tbl" style={{ width: "100%", borderCollapse: "collapse", fontSize: "12.5px" }}>
      {headers && (
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th key={i} style={{ textAlign: "left", padding: "6px 8px", borderBottom: "1px solid var(--line-2)", color: "var(--fg-3)", fontSize: "11px", textTransform: "uppercase", letterSpacing: ".04em" }}>{h}</th>
            ))}
          </tr>
        </thead>
      )}
      <tbody {...dataAttrs}>
        {rows}
      </tbody>
    </table>
  );
}

// ─── Stub panels ──────────────────────────────────────────────────────────

function UnimplementedPanel({ node }) {
  return <div data-panel-id={node.id}>panel for {node.id} not yet wired</div>;
}

// ─── NFR-4 perf instrumentation ───────────────────────────────────────────

// Playwright reads via performance.getEntriesByName for NFR-4 <100ms swap budget
function usePanelMountMark(node) {
  const { useEffect } = window.React;
  useEffect(() => { performance.mark(`panel.${node.id}.mounted`); }, []);
}


// ─── IntakeFetchPanel (FR-P1) — SourceView style ─────────────────────────

function IntakeFetchPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const [rawOpen, setRawOpen] = React.useState(false);
  const [refsOpen, setRefsOpen] = React.useState(false);

  if (status === "pending") return <PendingState family="source" lifecycle="pending" panelId="intake_fetch" />;
  if (status === "running" && !delta) return <PendingState family="source" lifecycle="running_empty" panelId="intake_fetch" />;
  if (status === "failed") return <div data-panel-id="intake_fetch"><ErrorBanner field="intake" value={runState.last_intake_error || emptyCopy("source", "failed")} /></div>;

  const rawBody = typeof runState.raw_source_body === "string" ? runState.raw_source_body : "";
  const description = rawBody.split("\n\n")[0] || "";
  const cvssScore = runState.cvss_score_bp != null ? runState.cvss_score_bp / 100
    : ((rawBody.match(/CVSS:\s*([\d.]+)/) || [])[1] ? parseFloat(rawBody.match(/CVSS:\s*([\d.]+)/)[1]) : null);
  const cweId = runState.cwe_class || (rawBody.match(/(CWE-\d+)/) || [])[1] || "";
  const cpeUris = runState.advisory_cpe_uris || runState.cpe_uris || [];
  const refs = runState.advisory_references || [];
  const products = runState.candidate_products || [];
  const versions = runState.exact_affected_versions || [];
  const ranges = runState.affected_version_ranges || [];
  const vulnStatus = runState.vulnerability_status || "";
  const kevListed = runState.kev_listed === true;
  const epssScore = runState.epss_score;
  const sourceUrl = runState.raw_source_url || "";

  const sev = cvssScore >= 9 ? { label: "CRITICAL", bg: "rgba(239,106,106,.15)", border: "rgba(239,106,106,.4)", color: "var(--err)" }
            : cvssScore >= 7 ? { label: "HIGH",     bg: "rgba(245,181,74,.12)",  border: "rgba(245,181,74,.35)", color: "var(--warn)" }
            : cvssScore >= 4 ? { label: "MEDIUM",   bg: "rgba(122,182,255,.10)", border: "rgba(122,182,255,.3)", color: "var(--info)" }
            : cvssScore != null ? { label: "LOW", bg: "rgba(95,207,144,.10)", border: "rgba(95,207,144,.3)", color: "var(--ok)" }
            : null;

  return (
    <div data-panel-id="intake_fetch" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      {runState.last_intake_error && <ErrorBanner value={runState.last_intake_error} />}

      {/* ── Hero: CVSS badge + CVE identity ── */}
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        {sev && (
          <div style={{
            minWidth: 88, textAlign: "center", padding: "14px 12px 10px",
            background: sev.bg, border: "1px solid " + sev.border, borderRadius: 10,
          }}>
            <div className="mono" style={{ fontSize: 32, fontWeight: 700, lineHeight: 1, color: sev.color }}>{cvssScore.toFixed(1)}</div>
            <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".12em", color: sev.color, marginTop: 5 }}>{sev.label}</div>
          </div>
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span className="mono" style={{ fontSize: 15, fontWeight: 600, color: "var(--fg-0)" }}>
              {runState.cve_vendor || ""}{runState.cve_vendor && runState.cve_product ? " / " : ""}{runState.cve_product || ""}
            </span>
            {vulnStatus && <Pill tone={vulnStatus === "no_fix_published" ? "warn" : "ok"}>{vulnStatus.replace(/_/g, " ")}</Pill>}
          </div>
          {description && (
            <p style={{ margin: "6px 0 0", color: "var(--fg-1)", fontSize: 12.5, lineHeight: 1.5,
              display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>
              {description}
            </p>
          )}
          <div className="mono muted" style={{ fontSize: 11, marginTop: 6 }}>
            {runState.install_channel && <span>{runState.install_channel}</span>}
            {runState.fixed_version && <span> · fix: {runState.fixed_version}</span>}
            {sourceUrl && (
              <> · <a href={sourceUrl} target="_blank" rel="noopener" style={{ color: "var(--info)", textDecoration: "none" }}>
                {sourceUrl.replace(/^https?:\/\//, "").split("/")[0]}
              </a></>
            )}
          </div>
        </div>
      </div>

      {/* ── Stat strip ── */}
      <div style={{ display: "flex", gap: 6 }}>
        {[
          cweId ? { label: "CWE", value: cweId, mono: true } : null,
          epssScore != null ? { label: "EPSS", value: String(epssScore), mono: true } : null,
          { label: "KEV", value: kevListed ? "YES" : "—", color: kevListed ? "var(--err)" : "var(--fg-3)" },
          { label: "products", value: String(products.length), mono: true },
          { label: "refs", value: String(refs.length), mono: true },
        ].filter(Boolean).map(s => (
          <div key={s.label} style={{
            flex: 1, background: "var(--bg-3)", border: "1px solid var(--line-1)",
            borderRadius: 6, padding: "8px 10px", textAlign: "center",
          }}>
            <div className={s.mono ? "mono" : ""} style={{ fontSize: 15, fontWeight: 600, color: s.color || "var(--fg-0)", lineHeight: 1.2 }}>{s.value}</div>
            <div style={{ fontSize: 9.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", marginTop: 3 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* ── Affected products + versions ── */}
      {(products.length > 0 || versions.length > 0 || ranges.length > 0) && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, padding: "10px 12px" }}>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 8 }}>
            affected scope
          </div>
          {products.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginBottom: versions.length > 0 || ranges.length > 0 ? 8 : 0 }}>
              {products.map((p, i) => <Pill key={i} tone="info">{p}</Pill>)}
            </div>
          )}
          {versions.length > 0 && (
            <div style={{ marginBottom: ranges.length > 0 ? 8 : 0 }}>
              <span className="muted" style={{ fontSize: 11 }}>versions: </span>
              <span className="mono" style={{ fontSize: 11.5, color: "var(--fg-0)" }}>{versions.join(", ")}</span>
            </div>
          )}
          {ranges.length > 0 && (
            <div>
              <span className="muted" style={{ fontSize: 11 }}>ranges: </span>
              {ranges.map((r, i) => {
                const from = r.from || r.introduced || r.start_inc || r.exact || "";
                const to = r.to || r.fixed || r.end_exc || "";
                return <span key={i} className="mono" style={{ fontSize: 11.5, color: "var(--fg-0)" }}>
                  {from}{to ? " → " + to : ""}{i < ranges.length - 1 ? ", " : ""}
                </span>;
              })}
            </div>
          )}
        </div>
      )}

      {/* ── CPE URIs ── */}
      {cpeUris.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, padding: "10px 12px" }}>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 6 }}>
            CPE URIs <span className="mono muted" style={{ fontSize: 10, textTransform: "none" }}>({cpeUris.length})</span>
          </div>
          <pre className="code" style={{ margin: 0, fontSize: 11, maxHeight: 80, overflow: "auto" }}>{cpeUris.join("\n")}</pre>
        </div>
      )}

      {/* ── References ── */}
      {refs.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <button
            onClick={() => setRefsOpen(!refsOpen)}
            style={{
              display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center",
              padding: "8px 12px", background: "none", border: "none", color: "var(--fg-2)", fontSize: 11,
            }}
          >
            <span style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, fontSize: 10.5 }}>references</span>
            <span className="mono">{refsOpen ? "▾" : "▸"} {refs.length}</span>
          </button>
          {refsOpen && (
            <ul className="retrieved" style={{ padding: "0 12px 10px", maxHeight: 200, overflow: "auto" }}>
              {refs.map((ref, i) => {
                const url = typeof ref === "string" ? ref : (ref.url || ref.href || "");
                const tag = typeof ref === "object" ? (ref.source || (ref.tags && ref.tags[0]) || "") : "";
                return url ? <li key={i}>{tag && <span className="retrieved-score mono">{tag}</span>}<a href={url} target="_blank" rel="noopener" className="retrieved-path mono" style={{ color: "var(--info)", textDecoration: "none" }}>{url}</a></li> : null;
              })}
            </ul>
          )}
        </div>
      )}

      {/* ── Raw advisory ── */}
      {rawBody && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <button
            onClick={() => setRawOpen(!rawOpen)}
            style={{
              display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center",
              padding: "8px 12px", background: "none", border: "none", color: "var(--fg-2)", fontSize: 11,
            }}
          >
            <span style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, fontSize: 10.5 }}>raw advisory</span>
            <span className="mono">{rawOpen ? "▾ collapse" : "▸ " + rawBody.length.toLocaleString() + " chars"}</span>
          </button>
          {rawOpen && <pre className="code" style={{ margin: "0 12px 10px", maxHeight: 400, overflow: "auto" }}>{rawBody}</pre>}
        </div>
      )}
    </div>
  );
}

// ─── ExtractPanel (FR-P1b) — DSPy schema-constrained extraction ─────────

function ExtractPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const [jsonOpen, setJsonOpen] = React.useState(false);

  if (status === "pending") return <PendingState family="llm" lifecycle="pending" panelId={node.id} />;
  if (status === "running" && !delta) return <PendingState family="llm" lifecycle="running_empty" panelId={node.id} />;

  const ext = runState.extract || {};
  const extJson = typeof ext === "string" ? ext : JSON.stringify(ext, null, 2);
  const isTrusted = node.id.includes("trusted") && !node.id.includes("untrusted");
  const canonBody = typeof runState.canonical_body === "string" ? runState.canonical_body : "";

  const cvss = ext.cvss_score_bp != null ? (ext.cvss_score_bp / 100).toFixed(1) : null;
  const epss = ext.epss_score_bp != null ? (ext.epss_score_bp / 10000).toFixed(4) : null;
  const cwe = ext.cwe_class || runState.cwe_class || "";
  const vulnClass = ext.vuln_class || runState.vuln_class || "";
  const products = ext.affected_products || [];
  const versions = ext.affected_versions || [];
  const cpeUris = ext.cpe_uris || [];
  const kevListed = ext.kev_listed;
  const refs = ext.references || [];
  const elapsedMs = timing?.elapsed_ms;

  const fields = [
    ["cve_id", ext.cve_id],
    ["cwe_class", cwe],
    ["vuln_class", vulnClass],
    ["cvss", cvss],
    ["epss", epss],
    ["kev_listed", kevListed != null ? String(kevListed) : null],
    ["products", products.length > 0 ? products.join(", ") : null],
    ["versions", versions.length > 0 ? versions.join(", ") : null],
    ["refs", refs.length > 0 ? refs.length + " references" : null],
  ];
  const populated = fields.filter(([, v]) => v);
  const gaps = fields.filter(([, v]) => !v);

  return (
    <div data-panel-id={node.id} style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>

      {/* ── Header ── */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <Pill tone={isTrusted ? "ok" : "warn"}>{isTrusted ? "trusted" : "untrusted"}</Pill>
        <span className="mono muted" style={{ fontSize: 11.5 }}>
          DSPy · ExtractCveFields
          {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
        </span>
      </div>

      {/* ── Two-column: input text | extracted fields ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, minHeight: 0 }}>

        {/* Left: input text */}
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between",
          }}>
            <span>input</span>
            <span className="mono" style={{ textTransform: "none", fontWeight: 400 }}>{canonBody.length.toLocaleString()} chars</span>
          </div>
          <pre className="code" style={{
            margin: 0, padding: 12, fontSize: 11, whiteSpace: "pre-wrap", overflow: "auto",
            flex: 1, minHeight: 0, maxHeight: 400, color: "var(--fg-2)", lineHeight: 1.5,
          }}>{canonBody || "—"}</pre>
        </div>

        {/* Right: extracted fields */}
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column", minHeight: 0 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between",
          }}>
            <span>extracted fields</span>
            <span className="mono" style={{ textTransform: "none", fontWeight: 400 }}>{populated.length}/{fields.length}</span>
          </div>
          <div style={{ padding: 12, flex: 1, minHeight: 0, overflow: "auto" }}>
            {/* Populated fields */}
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 14px", fontSize: 12 }}>
              {populated.map(([label, val]) => (
                <React.Fragment key={label}>
                  <span style={{ color: "var(--fg-3)" }}>{label}</span>
                  <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 500 }}>{val}</span>
                </React.Fragment>
              ))}
            </div>

            {/* Gaps */}
            {gaps.length > 0 && (
              <>
                <div style={{ borderTop: "1px dashed var(--line-2)", margin: "10px 0 8px" }} />
                <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 14px", fontSize: 11.5 }}>
                  {gaps.map(([label]) => (
                    <React.Fragment key={label}>
                      <span style={{ color: "var(--fg-3)", opacity: 0.5 }}>{label}</span>
                      <span style={{ color: "var(--fg-3)", opacity: 0.4 }}>—</span>
                    </React.Fragment>
                  ))}
                </div>
              </>
            )}

            {/* CPE URIs inline */}
            {cpeUris.length > 0 && (
              <>
                <div style={{ borderTop: "1px solid var(--line-2)", margin: "10px 0 8px" }} />
                <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 4 }}>
                  CPE <span className="mono" style={{ textTransform: "none", fontWeight: 400 }}>({cpeUris.length})</span>
                </div>
                {cpeUris.map((uri, i) => (
                  <div key={i} className="mono" style={{ fontSize: 10.5, color: "var(--fg-2)", lineHeight: 1.6, wordBreak: "break-all" }}>{uri}</div>
                ))}
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── Raw extract JSON (collapsed) ── */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <button
          onClick={() => setJsonOpen(!jsonOpen)}
          style={{
            display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center",
            padding: "8px 12px", background: "none", border: "none", color: "var(--fg-2)", fontSize: 11,
          }}
        >
          <span style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, fontSize: 10.5 }}>raw extract JSON</span>
          <span className="mono">{jsonOpen ? "▾ collapse" : "▸ expand"}</span>
        </button>
        {jsonOpen && <pre className="code" style={{ margin: "0 12px 10px", maxHeight: 400, overflow: "auto" }}>{extJson}</pre>}
      </div>
    </div>
  );
}

// ─── CorrelateAssetsPanel (FR-P2) — SourceView broker variant ────────────

function CorrelateAssetsPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const [traceOpen, setTraceOpen] = React.useState(false);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId="correlate_assets" />;

  const hosts = runState.affected_host_names || [];
  const disposition = runState.disposition || "";
  const isApplicable = disposition === "applicable";
  const cmdbName = runState.cmdb_software_name || runState.matched_candidate_product || "";
  const cmdbQuality = runState.cmdb_match_quality || "";
  const cmdbScore = runState.cmdb_match_score;
  const cargonetCount = runState.cargonet_node_count || 0;
  const cargonetLab = runState.cargonet_lab_ref || "";
  const correlationMap = runState.cargonet_correlation_map || {};
  const agentTrace = runState.correlate_agent_trace;
  const elapsedMs = (typeof timing?.elapsed_ms === "number") ? timing.elapsed_ms : null;

  // Parse agent trace for search summary
  const traceArr = Array.isArray(agentTrace) ? agentTrace : [];
  const searchEntries = traceArr.map(t => ({
    vendor: t.vendor || "",
    product: t.product || "",
    variants: t.variants_tried || [],
    candidates: (t.candidates || []).map(c => ({ name: c.name || "", score: c.score, quality: c.quality || "" })),
  }));

  const dispBanner = isApplicable
    ? { label: "APPLICABLE", bg: "rgba(239,106,106,.15)", border: "rgba(239,106,106,.4)", color: "var(--err)" }
    : { label: disposition.toUpperCase().replace(/_/g, " ") || "UNKNOWN", bg: "rgba(255,255,255,.04)", border: "var(--line-2)", color: "var(--fg-3)" };

  return (
    <div data-panel-id="correlate_assets" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      {runState.last_cmdb_error && <ErrorBanner field="CMDB" value={runState.last_cmdb_error} />}
      {runState.last_cargonet_error && <ErrorBanner field="CargoNet" value={runState.last_cargonet_error} />}

      {/* ── Header ── */}
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Nautilus broker · cve_rem.correlate_assets
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* ── Disposition banner ── */}
      <div style={{
        padding: "12px 16px", borderRadius: 8,
        background: dispBanner.bg, border: "1px solid " + dispBanner.border,
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: ".06em", color: dispBanner.color }}>{dispBanner.label}</span>
        <span className="mono" style={{ fontSize: 12, color: "var(--fg-2)" }}>
          {cmdbName && <>cmdb: {cmdbName} · </>}
          {cmdbQuality && <>{cmdbQuality} · </>}
          {hosts.length} host{hosts.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* ── Two-column: search | results ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, minHeight: 0 }}>

        {/* Left: search */}
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column" }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          }}>search</div>
          <div style={{ padding: 12, overflow: "auto", maxHeight: 350 }}>
            {searchEntries.length > 0 ? searchEntries.map((s, i) => (
              <div key={i} style={{ marginBottom: i < searchEntries.length - 1 ? 12 : 0 }}>
                <div style={{ fontSize: 12 }}>
                  <span style={{ color: "var(--fg-3)" }}>vendor </span>
                  <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 500 }}>{s.vendor}</span>
                  <span style={{ color: "var(--fg-3)", marginLeft: 10 }}>product </span>
                  <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 500 }}>{s.product}</span>
                </div>
                {s.variants.length > 0 && (
                  <div style={{ marginTop: 4, fontSize: 11, color: "var(--fg-3)" }}>
                    variants: <span className="mono" style={{ color: "var(--fg-2)" }}>{s.variants.join(", ")}</span>
                  </div>
                )}
                {s.candidates.length > 0 && (
                  <div style={{ marginTop: 6 }}>
                    {s.candidates.map((c, j) => (
                      <div key={j} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 11.5, padding: "2px 0" }}>
                        <span className="mono" style={{ color: "var(--fg-1)", flex: 1 }}>{c.name}</span>
                        <span className="mono muted" style={{ fontSize: 10.5 }}>{c.score}</span>
                        <Pill tone={c.quality === "high" ? "ok" : c.quality === "medium" ? "warn" : "info"}>{c.quality}</Pill>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )) : (
              <div className="muted" style={{ fontSize: 12 }}>no search trace</div>
            )}
          </div>
        </div>

        {/* Right: results */}
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column" }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          }}>results</div>
          <div style={{ padding: 12, overflow: "auto", maxHeight: 350 }}>
            {/* Summary fields */}
            <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
              {[
                ["disposition", disposition],
                ["cmdb software", cmdbName],
                ["cmdb quality", cmdbQuality],
                ["cmdb score", cmdbScore != null ? String(cmdbScore) : null],
                ["hosts", String(hosts.length)],
                ["cargonet nodes", cargonetCount > 0 ? String(cargonetCount) : null],
              ].filter(([, v]) => v).map(([label, val]) => (
                <React.Fragment key={label}>
                  <span style={{ color: "var(--fg-3)" }}>{label}</span>
                  <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 500 }}>{val}</span>
                </React.Fragment>
              ))}
            </div>

            {/* Host list */}
            {hosts.length > 0 && (
              <>
                <div style={{ borderTop: "1px solid var(--line-2)", margin: "10px 0 8px" }} />
                <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 6 }}>
                  affected hosts
                </div>
                {hosts.map((h, i) => {
                  const cMap = typeof correlationMap === "object" && correlationMap[h];
                  return (
                    <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0", fontSize: 12 }}>
                      <span style={{ color: "var(--err)", fontSize: 11 }}>●</span>
                      <span className="mono" style={{ color: "var(--fg-0)" }}>{h}</span>
                      {cMap && <span className="mono muted" style={{ fontSize: 10 }}>cn:{cMap.node_id?.slice(0, 8)}</span>}
                    </div>
                  );
                })}
              </>
            )}

            {/* CargoNet lab */}
            {cargonetLab && (
              <>
                <div style={{ borderTop: "1px solid var(--line-2)", margin: "10px 0 8px" }} />
                <div style={{ fontSize: 11 }}>
                  <span style={{ color: "var(--fg-3)" }}>cargonet lab </span>
                  <span className="mono" style={{ color: "var(--fg-2)", fontSize: 10.5 }}>{cargonetLab}</span>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ── Raw agent trace (collapsed) ── */}
      {agentTrace && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <button
            onClick={() => setTraceOpen(!traceOpen)}
            style={{
              display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center",
              padding: "8px 12px", background: "none", border: "none", color: "var(--fg-2)", fontSize: 11,
            }}
          >
            <span style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, fontSize: 10.5 }}>raw agent trace</span>
            <span className="mono">{traceOpen ? "▾ collapse" : "▸ expand"}</span>
          </button>
          {traceOpen && (
            <pre className="code" style={{ margin: "0 12px 10px", maxHeight: 400, overflow: "auto", fontSize: 11 }}>
              {typeof agentTrace === "string" ? agentTrace : JSON.stringify(agentTrace, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ─── EmitQuarantinePanel — quarantine artifact emit ─────────────────────

function EmitQuarantinePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const [contentOpen, setContentOpen] = React.useState(false);
  const [refOpen, setRefOpen] = React.useState(false);

  if (status === "pending") return <PendingState family="artifact" lifecycle="pending" panelId={node.id} />;

  const artRef = runState.quarantine_artifact_ref || "";
  const artHash = runState.canonicalization_quarantine_id || artRef.split("/").pop()?.replace(".json", "") || "";
  const hashShort = artHash.length > 16 ? artHash.slice(0, 8) + "…" + artHash.slice(-6) : artHash;
  const untrusted = runState.untrusted_text_suspected;
  const canonBody = typeof runState.canonical_body === "string" ? runState.canonical_body : "";
  const cveId = runState.cve_id || "";
  const sourceTrust = runState.source_trust || "";
  const injectionClass = runState.injection_class || "";
  const elapsedMs = timing?.elapsed_ms;

  return (
    <div data-panel-id={node.id} style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>

      {/* ── Header ── */}
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        EmitQuarantineArtifactNode
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* ── Quarantine banner ── */}
      <div style={{
        padding: "12px 16px", borderRadius: 8,
        background: "rgba(245,181,74,.12)", border: "1px solid rgba(245,181,74,.35)",
        display: "flex", justifyContent: "space-between", alignItems: "center",
      }}>
        <span style={{ fontSize: 16, fontWeight: 700, letterSpacing: ".06em", color: "var(--warn)" }}>QUARANTINED</span>
        <span className="mono" style={{ fontSize: 12, color: "var(--fg-2)" }}>
          {untrusted ? "untrusted text suspected" : "persisted for audit"}
        </span>
      </div>

      {/* ── Context fields ── */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, padding: "10px 12px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
          {[
            ["cve_id", cveId],
            ["source_trust", sourceTrust],
            ["untrusted_text", untrusted != null ? String(untrusted) : null],
            ["injection_class", injectionClass || null],
            ["artifact hash", hashShort],
          ].filter(([, v]) => v).map(([label, val]) => (
            <React.Fragment key={label}>
              <span style={{ color: "var(--fg-3)" }}>{label}</span>
              <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 500 }}>{val}</span>
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* ── Quarantined content (collapsed) ── */}
      {canonBody && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <button
            onClick={() => setContentOpen(!contentOpen)}
            style={{
              display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center",
              padding: "8px 12px", background: "none", border: "none", color: "var(--fg-2)", fontSize: 11,
            }}
          >
            <span style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, fontSize: 10.5 }}>quarantined content</span>
            <span className="mono">{contentOpen ? "▾" : "▸"} {canonBody.length.toLocaleString()} chars</span>
          </button>
          {contentOpen && <pre className="code" style={{ margin: "0 12px 10px", maxHeight: 300, overflow: "auto", whiteSpace: "pre-wrap" }}>{canonBody}</pre>}
        </div>
      )}

      {/* ── Full artifact ref (collapsed) ── */}
      {artRef && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <button
            onClick={() => setRefOpen(!refOpen)}
            style={{
              display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center",
              padding: "8px 12px", background: "none", border: "none", color: "var(--fg-2)", fontSize: 11,
            }}
          >
            <span style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 600, fontSize: 10.5 }}>artifact ref</span>
            <span className="mono">{refOpen ? "▾" : "▸"} expand</span>
          </button>
          {refOpen && <pre className="code" style={{ margin: "0 12px 10px", overflow: "auto", wordBreak: "break-all", whiteSpace: "pre-wrap", fontSize: 11 }}>{artRef}</pre>}
        </div>
      )}
    </div>
  );
}

// ─── InjectionClassifyPanel — verdict badge ─────────────────────────────

function InjectionClassifyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle) return <div className="nv-grid"><PanelCard title="injection classification" className="span-3"><span style={{ color: "var(--fg-3)" }}>{emptyCopy("llm", lifecycle)}</span></PanelCard></div>;

  const d = delta?.fields || {};
  const cls = (d.injection_class || "unknown").toLowerCase();

  const VERDICT = {
    clean:          { color: "var(--ok)",  bg: "var(--ok-dim)",                         icon: "✓", label: "CLEAN",          desc: "No prompt-injection detected in untrusted text." },
    suspicious:     { color: "var(--warn)", bg: "rgba(255,193,7,.12)",                  icon: "⚠", label: "SUSPICIOUS",     desc: "Possible injection pattern — quarantine recommended." },
    attack_pattern: { color: "var(--err)",  bg: "rgba(239,83,80,.12)",                  icon: "✖", label: "ATTACK PATTERN", desc: "Active attack pattern detected — text quarantined." },
  };
  const v = VERDICT[cls] || { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)", icon: "?", label: cls.toUpperCase(), desc: "Unknown classification result." };

  return (
    <div className="nv-grid">
      <PanelCard className="span-3">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "48px 24px 32px" }}>
          <div style={{
            width: 80, height: 80, borderRadius: "50%",
            background: v.bg, border: `2px solid ${v.color}`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 36, color: v.color, fontWeight: 700,
          }}>{v.icon}</div>
          <div style={{ marginTop: 16, fontSize: 22, fontWeight: 700, color: v.color, letterSpacing: "0.05em" }}>{v.label}</div>
          <div style={{ marginTop: 8, color: "var(--fg-3)", fontSize: 13, textAlign: "center", maxWidth: 400 }}>{v.desc}</div>
        </div>
      </PanelCard>
    </div>
  );
}

// ─── CritiqueExtractedPanel — verdict + attempt history ─────────────────

function CritiqueExtractedPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle) return <div className="nv-grid"><PanelCard title="critic verdict" className="span-3"><span style={{ color: "var(--fg-3)" }}>{emptyCopy("llm", lifecycle)}</span></PanelCard></div>;

  const d = delta?.fields || {};
  const verdict = (d.critic_verdict || "unknown").toLowerCase();
  const attempt = d.critic_attempt || 1;
  const history = d.critic_history || [];

  const VERDICT = {
    approved: { color: "var(--ok)",   bg: "var(--ok-dim)",           icon: "✓", label: "APPROVED" },
    revise:   { color: "var(--warn)", bg: "rgba(255,193,7,.12)",     icon: "↻", label: "REVISE"   },
    rejected: { color: "var(--err)",  bg: "rgba(239,83,80,.12)",     icon: "✖", label: "REJECTED" },
  };
  const v = VERDICT[verdict] || { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)", icon: "?", label: verdict.toUpperCase() };

  const [histOpen, setHistOpen] = React.useState(false);

  return (
    <div className="nv-grid">
      <PanelCard className="span-3">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "40px 24px 24px" }}>
          <div style={{
            width: 80, height: 80, borderRadius: "50%",
            background: v.bg, border: `2px solid ${v.color}`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 36, color: v.color, fontWeight: 700,
          }}>{v.icon}</div>
          <div style={{ marginTop: 16, fontSize: 22, fontWeight: 700, color: v.color, letterSpacing: "0.05em" }}>{v.label}</div>
          <div style={{ marginTop: 8, color: "var(--fg-3)", fontSize: 13 }}>
            Attempt {attempt}{history.length > 1 ? ` of ${history.length}` : ""}
          </div>
        </div>
      </PanelCard>

      {history.length > 0 && (
        <PanelCard title="attempt history" className="span-3"
          right={<button onClick={() => setHistOpen(!histOpen)} style={{ background: "none", border: "none", color: "var(--fg-3)", cursor: "pointer", fontSize: 12 }}>{histOpen ? "▾ collapse" : `▸ ${history.length} attempt${history.length > 1 ? "s" : ""}`}</button>}>
          {histOpen && history.map((h, i) => {
            const hv = VERDICT[(h.verdict || "").toLowerCase()] || { color: "var(--fg-3)", icon: "?" };
            return (
              <div key={i} style={{ padding: "8px 0", borderTop: i > 0 ? "1px solid var(--line-1)" : "none" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ color: hv.color, fontWeight: 700 }}>{hv.icon}</span>
                  <span style={{ color: hv.color, fontWeight: 600, fontSize: 13 }}>{(h.verdict || "unknown").toUpperCase()}</span>
                  <span style={{ color: "var(--fg-3)", fontSize: 12 }}>attempt {h.attempt || i + 1}</span>
                </div>
                {h.feedback_text && <div style={{ marginTop: 4, color: "var(--fg-2)", fontSize: 12.5 }}>{h.feedback_text}</div>}
                {h.veto_flags && h.veto_flags.length > 0 && (
                  <div style={{ marginTop: 4, display: "flex", gap: 4, flexWrap: "wrap" }}>
                    {h.veto_flags.map((f, j) => <span key={j} style={{ background: "rgba(239,83,80,.15)", color: "var(--err)", fontSize: 11, padding: "1px 6px", borderRadius: 3 }}>{f}</span>)}
                  </div>
                )}
              </div>
            );
          })}
        </PanelCard>
      )}
    </div>
  );
}

// ─── HitlAwaitingPanel — interactive HITL gate (approve/reject/escalate) ───

function HitlAwaitingPanel({ node, runState, events }) {
  const [submitting, setSubmitting] = React.useState(false);
  const [errMsg, setErrMsg] = React.useState("");
  const [note, setNote] = React.useState("");
  const [doneDecision, setDoneDecision] = React.useState(null);

  const runId = runState?.run_id || (new URLSearchParams(window.location.search)).get("run") || "";
  const waitingEv = (events || []).find((e) => e.type === "waiting_for_input");
  const prompt = waitingEv?.prompt || "";
  const cveId = runState?.cve_id || "";

  const GATE_CONTEXT = {
    hitl_ingest_review:        { title: "Ingest review",        question: "Should this CVE proceed through the pipeline?" },
    hitl_plan_review:          { title: "Plan review",          question: "Is the remediation plan safe to execute?" },
    hitl_change_approval:      { title: "Change approval",      question: "Approve the change request for production?" },
    hitl_retrospective_review: { title: "Retrospective review", question: "Accept the retrospective analysis?" },
  };
  const gateCtx = GATE_CONTEXT[node.id] || { title: "Review", question: "Approve this gate?" };

  const submit = async (decision) => {
    if (submitting) return;
    setSubmitting(true);
    setErrMsg("");
    try {
      const body = {
        response: {
          decision,
          actor: "watcher-ui",
          note: note || "",
          at: new Date().toISOString(),
        },
      };
      const apiUrlFn = window.apiUrl || ((p) => p);
      const res = await fetch(apiUrlFn(`/v1/runs/${encodeURIComponent(runId)}/respond`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`${res.status} ${txt.slice(0, 200)}`);
      }
      setDoneDecision(decision);
    } catch (e) {
      setErrMsg(String(e.message || e));
    } finally {
      setSubmitting(false);
    }
  };

  const DECISIONS = [
    { id: "approve",  label: "Approve",  color: "var(--ok)",   bg: "var(--ok-dim)",        icon: "✓" },
    { id: "reject",   label: "Reject",   color: "var(--err)",  bg: "rgba(239,83,80,.12)",  icon: "✖" },
    { id: "escalate", label: "Escalate", color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "⬆" },
  ];

  if (doneDecision) {
    return (
      <div className="nv-grid">
        <PanelCard className="span-3">
          <div style={{ padding: "32px 24px", textAlign: "center", color: "var(--fg-1)" }}>
            <div style={{ fontSize: 18, color: "var(--ok)", marginBottom: 8 }}>✓ Submitted: {doneDecision}</div>
            <div style={{ color: "var(--fg-3)", fontSize: 12 }}>Run is resuming. Watcher will catch up via WebSocket.</div>
          </div>
        </PanelCard>
      </div>
    );
  }

  return (
    <div className="nv-grid">
      <PanelCard title="awaiting your review" className="span-3">
        <div style={{ padding: "8px 0", display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
            <span className="mono pill pill-warn">PAUSED</span>
            <span style={{ color: "var(--fg-0)", fontSize: 14, fontWeight: 600 }}>{gateCtx.title}</span>
            {cveId && <span className="mono" style={{ color: "var(--fg-3)", fontSize: 11 }}>· {cveId}</span>}
          </div>
          <div style={{ color: "var(--fg-2)", fontSize: 13, fontStyle: "italic" }}>{gateCtx.question}</div>
          {prompt && (
            <div className="mono" style={{ background: "var(--bg-2)", border: "1px solid var(--line-1)", borderRadius: 4, padding: "8px 10px", fontSize: 11.5, color: "var(--fg-2)", whiteSpace: "pre-wrap" }}>
              {prompt}
            </div>
          )}
        </div>
      </PanelCard>

      <PanelCard title="decision" className="span-3">
        <div style={{ padding: "8px 0", display: "flex", flexDirection: "column", gap: 12 }}>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="optional note (audit trail)"
            disabled={submitting}
            style={{
              width: "100%", minHeight: 60, resize: "vertical",
              background: "var(--bg-2)", color: "var(--fg-0)",
              border: "1px solid var(--line-3)", borderRadius: 6,
              padding: "8px 10px", fontFamily: "var(--mono)", fontSize: 12,
              outline: "none",
            }}
          />
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
            {DECISIONS.map((d) => (
              <button
                key={d.id}
                onClick={() => submit(d.id)}
                disabled={submitting}
                style={{
                  padding: "12px 14px",
                  border: `1px solid ${d.color}`,
                  background: d.bg,
                  color: d.color,
                  borderRadius: 6,
                  fontSize: 13,
                  fontWeight: 600,
                  cursor: submitting ? "not-allowed" : "pointer",
                  display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                  opacity: submitting ? 0.5 : 1,
                }}
              >
                <span style={{ fontSize: 16 }}>{d.icon}</span>
                {d.label}
              </button>
            ))}
          </div>
          {errMsg && (
            <div style={{ color: "var(--err)", fontSize: 12, fontFamily: "var(--mono)" }}>
              {errMsg}
            </div>
          )}
          <div style={{ color: "var(--fg-3)", fontSize: 10.5, fontFamily: "var(--mono)" }}>
            POST /v1/runs/{runId}/respond · actor=watcher-ui
          </div>
        </div>
      </PanelCard>
    </div>
  );
}

// ─── HitlReviewPanel — shared HITL gate verdict ─────────────────────────

function HitlReviewPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  // Awaiting reviewer input — show approval action panel.
  if (status === "waiting") {
    return <HitlAwaitingPanel node={node} runState={runState} events={events} />;
  }

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle) return <div className="nv-grid"><PanelCard title="human review" className="span-3"><span style={{ color: "var(--fg-3)" }}>{emptyCopy("hitl", lifecycle)}</span></PanelCard></div>;

  const d = delta?.fields || {};
  const resp = d.response || {};
  const gates = d.hitl_gates || {};
  const decision = (resp.decision || "unknown").toLowerCase();

  const VERDICT = {
    approve:  { color: "var(--ok)",   bg: "var(--ok-dim)",       icon: "✓", label: "APPROVED" },
    reject:   { color: "var(--err)",  bg: "rgba(239,83,80,.12)", icon: "✖", label: "REJECTED" },
    escalate: { color: "var(--warn)", bg: "rgba(255,193,7,.12)", icon: "⬆", label: "ESCALATED" },
  };
  const v = VERDICT[decision] || { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)", icon: "?", label: decision.toUpperCase() };

  // Compute wait duration from gate data
  const gateKey = Object.keys(gates)[0];
  const gate = gateKey ? gates[gateKey] : null;
  let waitStr = "";
  if (gate?.waiting_since && resp.at) {
    const ms = new Date(resp.at) - new Date(gate.waiting_since);
    if (ms < 1000) waitStr = `${ms}ms`;
    else if (ms < 60000) waitStr = `${(ms/1000).toFixed(1)}s`;
    else waitStr = `${(ms/60000).toFixed(1)}m`;
  }

  // Pull full state at this checkpoint for review context
  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const cveId = allState.cve_id || d.cve_id || "";
  const vendor = allState.cve_vendor || "";
  const product = allState.cve_product || "";
  const sourceTrust = allState.source_trust || "";
  const injClass = allState.injection_class || "";
  const ssvcTier = allState.ssvc_tier || "";
  const criticVerdict = allState.critic_verdict || "";
  const untrustedInfluenced = allState.untrusted_text_influenced;
  const vulnClass = allState.vuln_class || "";
  const cweClass = allState.cwe_class || "";
  const disposition = allState.disposition || "";
  const rawDesc = allState.raw_source_body || "";
  const fixedVersion = allState.fixed_version || "";
  const extract = allState.extract || {};
  const cpeCount = (extract.cpe_uris || allState.advisory_cpe_uris || []).length;
  const hostCount = (allState.affected_host_names || []).length;

  const GATE_CONTEXT = {
    hitl_ingest_review:          { title: "Ingest review", question: "Should this CVE proceed through the pipeline?" },
    hitl_plan_review:            { title: "Plan review",   question: "Is the remediation plan safe to execute?" },
    hitl_change_approval:        { title: "Change approval", question: "Approve the change request for production?" },
    hitl_retrospective_review:   { title: "Retrospective review", question: "Accept the retrospective analysis?" },
  };
  const gateCtx = GATE_CONTEXT[node.id] || { title: "Review", question: "Approve this gate?" };

  const [descOpen, setDescOpen] = React.useState(false);

  return (
    <div className="nv-grid">
      {/* Verdict badge */}
      <PanelCard className="span-3">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "32px 24px 20px" }}>
          <div style={{
            width: 72, height: 72, borderRadius: "50%",
            background: v.bg, border: `2px solid ${v.color}`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 32, color: v.color, fontWeight: 700,
          }}>{v.icon}</div>
          <div style={{ marginTop: 14, fontSize: 20, fontWeight: 700, color: v.color, letterSpacing: "0.05em" }}>{v.label}</div>
          <div style={{ marginTop: 6, color: "var(--fg-3)", fontSize: 12 }}>{gateCtx.title}</div>
        </div>
      </PanelCard>

      {/* Review prompt — what the reviewer was asked */}
      <PanelCard title="review prompt" className="span-3">
        <div style={{ padding: "8px 0 10px", color: "var(--fg-2)", fontSize: 13, fontStyle: "italic", borderBottom: "1px dashed var(--line-2)", marginBottom: 10 }}>
          {gateCtx.question}
        </div>
        <KV pairs={[
          ["CVE", cveId],
          ["vendor / product", vendor && product ? `${vendor} / ${product}` : vendor || product || null],
          ["CWE", cweClass],
          ["vuln class", vulnClass],
          ["source trust", sourceTrust],
          ["injection class", injClass],
          ["critic verdict", criticVerdict],
          ["untrusted influenced", untrustedInfluenced != null ? String(untrustedInfluenced) : null],
          ["disposition", disposition],
          ["SSVC tier", ssvcTier],
          ["affected CPEs", cpeCount > 0 ? String(cpeCount) : null],
          ["affected hosts", hostCount > 0 ? String(hostCount) : null],
          ["fixed version", fixedVersion],
        ]} />
        {rawDesc && (
          <div style={{ marginTop: 10 }}>
            <button onClick={() => setDescOpen(!descOpen)} style={{ background: "none", border: "none", color: "var(--fg-3)", cursor: "pointer", fontSize: 11, padding: 0 }}>
              {descOpen ? "▾ description" : "▸ description"}
            </button>
            {descOpen && <div style={{ marginTop: 6, color: "var(--fg-2)", fontSize: 12, lineHeight: 1.5 }}>{rawDesc}</div>}
          </div>
        )}
      </PanelCard>

      {/* Decision */}
      <PanelCard title="decision" className="span-3">
        <KV pairs={[
          ["actor", resp.actor || "—"],
          ["note", resp.note || "—", { mono: false }],
          ["decided at", resp.at ? new Date(resp.at).toLocaleString() : "—"],
          ...(waitStr ? [["wait duration", waitStr]] : []),
          ...(gate ? [["gate", gateKey], ["auto-approved", resp.actor?.includes("auto") ? "yes" : "no"]] : []),
        ]} />
      </PanelCard>
    </div>
  );
}

// ─── SsvcEvaluatePanel — SSVC tier verdict ──────────────────────────────

function SsvcEvaluatePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle) return <div className="nv-grid"><PanelCard title="SSVC tier" className="span-3"><span style={{ color: "var(--fg-3)" }}>{emptyCopy("decision", lifecycle)}</span></PanelCard></div>;

  const d = delta?.fields || {};
  const tier = (d.ssvc_tier || runState.ssvc_tier || "unknown").toLowerCase();

  const TIER = {
    act_auto:          { color: "var(--ok)",   bg: "var(--ok-dim)",           icon: "⚡", label: "ACT (AUTO)",     desc: "Proceed automatically — no human gate required." },
    act_hitl_required: { color: "var(--warn)", bg: "rgba(255,193,7,.12)",     icon: "⚡", label: "ACT (HITL)",     desc: "Proceed with human approval at each gate." },
    attend:            { color: "var(--info)", bg: "rgba(122,182,255,.10)",   icon: "◎", label: "ATTEND",         desc: "Monitor closely — act if exposure changes." },
    track:             { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)",   icon: "◉", label: "TRACK",          desc: "Exposure-monitor only; re-evaluate at +7d." },
    defer:             { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)",   icon: "⏸", label: "DEFER",          desc: "Low priority — tier_re_eval will revisit." },
  };
  const v = TIER[tier] || { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)", icon: "?", label: tier.toUpperCase().replace(/_/g, " "), desc: "" };

  // Pull context from full state
  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const cveId = allState.cve_id || "";
  const disposition = allState.disposition || "";
  const vulnClass = allState.vuln_class || "";
  const cweClass = allState.cwe_class || "";
  const hostCount = (allState.affected_host_names || []).length;
  const kevListed = allState.known_exploited;

  return (
    <div className="nv-grid">
      <PanelCard className="span-3">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "32px 24px 20px" }}>
          <div style={{ width: 80, height: 80, borderRadius: "50%", background: v.bg, border: `2px solid ${v.color}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 36, color: v.color, fontWeight: 700 }}>{v.icon}</div>
          <div style={{ marginTop: 14, fontSize: 22, fontWeight: 700, color: v.color, letterSpacing: "0.05em" }}>{v.label}</div>
          {v.desc && <div style={{ marginTop: 8, color: "var(--fg-3)", fontSize: 12.5, textAlign: "center", maxWidth: 400 }}>{v.desc}</div>}
        </div>
      </PanelCard>

      {cveId && (
        <PanelCard title="decision inputs" className="span-3">
          <KV pairs={[
            ["CVE", cveId],
            ["disposition", disposition],
            ["vuln class", vulnClass],
            ["CWE", cweClass],
            ["affected hosts", String(hostCount)],
            ["KEV listed", kevListed != null ? (kevListed ? "yes" : "no") : null],
          ]} />
        </PanelCard>
      )}
    </div>
  );
}

// ─── VecSearchRetrosPanel — prior retro retrieval ───────────────────────

function VecSearchRetrosPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle) return <div className="nv-grid"><PanelCard title="retro search" className="span-3"><span style={{ color: "var(--fg-3)" }}>{emptyCopy("kg", lifecycle)}</span></PanelCard></div>;

  const d = delta?.fields || {};
  const retroCount = d.prior_retro_count ?? 0;
  const outcomes = d.prior_retro_outcomes || {};
  const retrievalStatus = d.prior_retro_retrieval_status || "";
  const mode = d.prior_retro_retrieval_mode || "";
  const suggestions = d.prior_retro_suggestions || [];
  const pgCount = d.prior_retros_pg_count ?? 0;
  const pgLastSeen = d.prior_retros_pg_last_seen || "";

  const OUTCOME_COLORS = { patched: "var(--ok)", rollback: "var(--warn)", not_applicable: "var(--fg-3)", failed: "var(--err)" };

  const [expanded, setExpanded] = React.useState(new Set());
  const toggle = (i) => setExpanded(prev => { const n = new Set(prev); n.has(i) ? n.delete(i) : n.add(i); return n; });

  return (
    <div className="nv-grid">
      {/* Header strip */}
      <PanelCard className="span-3">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ background: retrievalStatus === "ok" ? "var(--ok-dim)" : "rgba(239,83,80,.12)", color: retrievalStatus === "ok" ? "var(--ok)" : "var(--err)", fontSize: 11, padding: "2px 8px", borderRadius: 3, fontWeight: 600 }}>{retrievalStatus || "unknown"}</span>
          {mode && <span style={{ background: "rgba(122,182,255,.10)", color: "var(--info)", fontSize: 11, padding: "2px 8px", borderRadius: 3 }}>{mode}</span>}
          <span style={{ color: "var(--fg-2)", fontSize: 12 }}><b className="mono" style={{ color: "var(--fg-0)" }}>{retroCount}</b> prior retros</span>
          {pgCount > 0 && <span style={{ color: "var(--fg-3)", fontSize: 11 }}>· {pgCount} pgvector rows</span>}
        </div>
      </PanelCard>

      {/* Outcome breakdown */}
      {Object.keys(outcomes).length > 0 && (
        <PanelCard title="outcome distribution" className="span-3">
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {Object.entries(outcomes).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
              <div key={k} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 10px", borderRadius: 4, border: `1px solid ${OUTCOME_COLORS[k] || "var(--line-2)"}`, background: (OUTCOME_COLORS[k] || "var(--fg-3)") + "15" }}>
                <span className="mono" style={{ fontWeight: 700, color: OUTCOME_COLORS[k] || "var(--fg-2)", fontSize: 16 }}>{v}</span>
                <span style={{ color: "var(--fg-2)", fontSize: 11 }}>{k.replace(/_/g, " ")}</span>
              </div>
            ))}
          </div>
        </PanelCard>
      )}

      {/* Suggestions */}
      {suggestions.length > 0 && (
        <PanelCard title="suggestions from similar CVEs" className="span-3" right={<span className="muted mono" style={{ fontSize: 11 }}>{suggestions.length}</span>}>
          {suggestions.map((s, i) => {
            const similarity = s.dist != null ? Math.round((1 - s.dist) * 100) : null;
            const isExpanded = expanded.has(i);
            const text = s.suggestion_text || "";
            const short = text.length > 120 ? text.slice(0, 120) + "…" : text;
            return (
              <div key={i} style={{ padding: "8px 0", borderTop: i > 0 ? "1px solid var(--line-1)" : "none" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span className="mono" style={{ fontSize: 11, color: "var(--info)" }}>{s.source_cve_id || "?"}</span>
                  {s.source_cwe && <span style={{ fontSize: 10, color: "var(--fg-3)" }}>{s.source_cwe}</span>}
                  {similarity != null && (
                    <div style={{ display: "flex", alignItems: "center", gap: 4, marginLeft: "auto" }}>
                      <div style={{ width: 50, height: 4, background: "var(--line-1)", borderRadius: 2, overflow: "hidden" }}>
                        <div style={{ width: `${similarity}%`, height: "100%", background: similarity >= 70 ? "var(--ok)" : similarity >= 40 ? "var(--warn)" : "var(--fg-3)", borderRadius: 2 }} />
                      </div>
                      <span className="mono" style={{ fontSize: 10, color: "var(--fg-3)" }}>{similarity}%</span>
                    </div>
                  )}
                </div>
                <div style={{ color: "var(--fg-1)", fontSize: 12, lineHeight: 1.5, cursor: text.length > 120 ? "pointer" : "default" }} onClick={() => text.length > 120 && toggle(i)}>
                  {isExpanded ? text : short}
                </div>
              </div>
            );
          })}
        </PanelCard>
      )}
    </div>
  );
}

// ─── GraphPriorRemediationsPanel — KG prior actions ─────────────────────

function GraphPriorRemediationsPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle) return <div className="nv-grid"><PanelCard title="prior actions" className="span-3"><span style={{ color: "var(--fg-3)" }}>{emptyCopy("kg", lifecycle)}</span></PanelCard></div>;

  const d = delta?.fields || {};
  const actions = d.graph_prior_actions || [];
  const retrievalStatus = d.graph_prior_retrieval_status || "";

  const LANE_HINTS = {
    product_overlap: "Matched by shared affected product(s)",
    cwe_overlap:     "Matched by shared CWE classification",
    vendor_overlap:  "Matched by shared vendor",
    cpe_overlap:     "Matched by overlapping CPE URIs",
    package_overlap: "Matched by shared package name",
  };
  const KIND_COLORS = { upgrade: "var(--ok)", downgrade: "var(--warn)", mitigation: "var(--info)", workaround: "var(--fg-2)", patch: "var(--ok)" };

  // Cross-check vs remediation_discovery
  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const recommended = allState.recommended_actions || [];
  let crossCheck = null;
  if (actions.length > 0 && recommended.length > 0) {
    const graphKinds = new Set(actions.map(a => a.kind));
    const recKinds = new Set(recommended.map(a => a.kind));
    const graphTargets = new Set(actions.map(a => a.target_version).filter(Boolean));
    const recTargets = new Set(recommended.map(a => a.target_version).filter(Boolean));
    const kindMatch = [...graphKinds].some(k => recKinds.has(k));
    const targetMatch = [...graphTargets].some(t => recTargets.has(t));
    if (kindMatch && targetMatch) crossCheck = { color: "var(--ok)", label: "AGREES with discovery", icon: "✓" };
    else if (kindMatch) crossCheck = { color: "var(--warn)", label: "kind agrees, target differs", icon: "≈" };
    else crossCheck = { color: "var(--err)", label: "DIVERGED from discovery", icon: "≠" };
  }

  // Consensus within graph actions
  let consensus = null;
  if (actions.length >= 2) {
    const kinds = new Set(actions.map(a => a.kind));
    const targets = new Set(actions.map(a => a.target_version).filter(Boolean));
    if (kinds.size === 1 && targets.size <= 1) consensus = { color: "var(--ok)", label: "CONSENSUS" };
    else if (kinds.size === 1) consensus = { color: "var(--warn)", label: "MIXED TARGETS" };
    else consensus = { color: "var(--err)", label: "DIVERGED" };
  }

  const isEmpty = retrievalStatus === "empty_graph" || actions.length === 0;

  return (
    <div className="nv-grid">
      {/* Header strip */}
      <PanelCard className="span-3">
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span style={{ background: retrievalStatus === "ok" ? "var(--ok-dim)" : retrievalStatus === "empty_graph" ? "rgba(255,255,255,.06)" : "rgba(239,83,80,.12)", color: retrievalStatus === "ok" ? "var(--ok)" : retrievalStatus === "empty_graph" ? "var(--fg-3)" : "var(--err)", fontSize: 11, padding: "2px 8px", borderRadius: 3, fontWeight: 600 }}>{retrievalStatus || "unknown"}</span>
          <span style={{ color: "var(--fg-2)", fontSize: 12 }}><b className="mono" style={{ color: "var(--fg-0)" }}>{actions.length}</b> prior action{actions.length === 1 ? "" : "s"}</span>
          {consensus && <span style={{ background: consensus.color + "22", color: consensus.color, fontSize: 11, padding: "2px 8px", borderRadius: 3, fontWeight: 600 }}>{consensus.label}</span>}
          {crossCheck && <span style={{ background: crossCheck.color + "22", color: crossCheck.color, fontSize: 11, padding: "2px 8px", borderRadius: 3, fontWeight: 600, marginLeft: "auto" }}>{crossCheck.icon} {crossCheck.label}</span>}
        </div>
      </PanelCard>

      {/* Actions */}
      {isEmpty ? (
        <PanelCard className="span-3">
          <div style={{ padding: "32px 16px", textAlign: "center", color: "var(--fg-3)", fontSize: 12 }}>
            {retrievalStatus === "empty_graph" ? "Graph is empty — no prior actions recorded yet" : "No prior actions found for this CVE"}
          </div>
        </PanelCard>
      ) : (
        <PanelCard title="prior actions" className="span-3">
          {actions.map((a, i) => {
            const kindColor = KIND_COLORS[a.kind] || "var(--fg-3)";
            const laneHint = LANE_HINTS[a.lane] || a.lane;
            const heavy = (a.freq || 0) >= 3;
            return (
              <div key={i} style={{ padding: "10px 0", borderTop: i > 0 ? "1px solid var(--line-1)" : "none" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
                  <span style={{ background: kindColor + "22", color: kindColor, fontSize: 11, padding: "2px 8px", borderRadius: 3, fontWeight: 600, textTransform: "uppercase" }}>{a.kind || "unknown"}</span>
                  {a.target_version && <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>→ {a.target_version}</span>}
                  {a.freq != null && (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, color: heavy ? "var(--accent)" : "var(--fg-3)", fontWeight: heavy ? 700 : 400 }}>
                      <span className="mono">{a.freq}×</span>
                      <span>applied</span>
                    </span>
                  )}
                  {a.advisory_ref && (
                    <a href={`https://nvd.nist.gov/vuln/detail/${a.advisory_ref.replace(/^advisory:/, "")}`} target="_blank" rel="noopener" className="mono" style={{ color: "var(--info)", fontSize: 11, marginLeft: "auto", textDecoration: "none" }}>
                      {a.advisory_ref.replace(/^advisory:/, "")} ↗
                    </a>
                  )}
                </div>
                {a.lane && (
                  <div style={{ marginTop: 4, fontSize: 11, color: "var(--fg-3)" }}>
                    <span className="mono" style={{ background: "rgba(255,255,255,.04)", padding: "1px 6px", borderRadius: 3, marginRight: 6 }} title={laneHint}>{a.lane}</span>
                    <span style={{ fontStyle: "italic" }}>{laneHint}</span>
                  </div>
                )}
              </div>
            );
          })}
        </PanelCard>
      )}
    </div>
  );
}

// ─── GraphBlastRadiusPanel ─────────────────────────────────────────────

function GraphBlastRadiusPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId="graph_blast_radius" />;

  // Full state lookup — blast_radius is on correlated, but co-located fields live on the parent state.
  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const correlated = allState.correlated || runState.correlated || {};
  const radius = correlated.blast_radius_node_count != null ? correlated.blast_radius_node_count : 0;
  const disposition = correlated.disposition || "";
  const reconciliationAnomaly = !!correlated.reconciliation_anomaly;
  const cmdbSet = correlated.cmdb_match_set || [];
  const nautobotSet = correlated.nautobot_match_set || [];

  const extract = allState.extract || runState.extract || {};
  const kev = !!extract.kev_listed;
  const cvssBp = extract.cvss_score_bp || 0;
  const cvssDisplay = cvssBp ? (cvssBp / 100).toFixed(1) : null;

  const hosts = allState.affected_host_names || runState.affected_host_names || [];
  const corrMap = allState.cargonet_correlation_map || runState.cargonet_correlation_map || {};
  const labRef = allState.cargonet_lab_ref || runState.cargonet_lab_ref || "";

  // Severity tier derivation — mirrors graph/real_nodes.py:GraphBlastRadiusNode
  let tier;
  if (radius >= 250) tier = { label: "MAX BLAST", reason: "KEV-listed (actively exploited)", color: "var(--err)", bg: "rgba(239,106,106,.15)" };
  else if (radius >= 100) tier = { label: "HIGH BLAST", reason: "CVSS ≥ 9.0 (critical)", color: "var(--err)", bg: "rgba(239,106,106,.12)" };
  else if (radius >= 25) tier = { label: "MEDIUM BLAST", reason: "CVSS ≥ 7.0 (high)", color: "var(--warn)", bg: "rgba(255,193,7,.12)" };
  else tier = { label: "LOW BLAST", reason: "CVSS < 7.0", color: "var(--fg-3)", bg: "rgba(255,255,255,.04)" };

  const highBlast = radius >= 100;

  return (
    <div data-panel-id="graph_blast_radius" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Header */}
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        RyuGraph · cve_rem.graph_blast_radius
        {typeof timing?.elapsed_ms === "number" && <> · {(timing.elapsed_ms / 1000).toFixed(2)}s</>}
      </div>

      {/* Hero verdict */}
      <div style={{
        padding: "16px 20px", borderRadius: 8,
        background: tier.bg, border: "1px solid " + tier.color + "55",
        display: "flex", alignItems: "center", gap: 20,
      }}>
        <div style={{ fontSize: 42, fontWeight: 700, color: tier.color, fontFamily: "var(--font-mono, monospace)", lineHeight: 1 }}>{radius}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: ".06em", color: tier.color }}>{tier.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{tier.reason}</div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--fg-3)" }}>
            {kev && <span style={{ color: "var(--err)" }}>KEV ✓ </span>}
            {cvssDisplay && <>cvss {cvssDisplay}</>}
          </div>
        </div>
      </div>

      {/* High blast veto warning */}
      {highBlast && (
        <div style={{
          padding: "10px 14px", borderRadius: 6,
          background: "rgba(239,106,106,.08)", border: "1px solid rgba(239,106,106,.3)",
          display: "flex", alignItems: "center", gap: 10,
        }}>
          <span style={{ color: "var(--err)", fontSize: 14 }}>⚠</span>
          <div style={{ fontSize: 12, color: "var(--fg-1)", lineHeight: 1.5 }}>
            <b style={{ color: "var(--err)" }}>Critic will veto auto-apply</b> — high blast radius triggers <span className="mono" style={{ background: "rgba(255,255,255,.06)", padding: "1px 5px", borderRadius: 3 }}>high_blast</span> veto flag unless runtime is <span className="mono">ansible</span> or <span className="mono">vendor_cli</span>. Routes through HITL change approval.
          </div>
        </div>
      )}

      {/* Heuristic explainer */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <div style={{
          padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
          letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
        }}>how blast radius is scored</div>
        <div style={{ padding: 12, fontSize: 11.5 }}>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr auto", gap: "4px 12px", alignItems: "center" }}>
            {[
              { hit: kev, label: "KEV-listed", desc: "actively exploited in wild", val: 250 },
              { hit: !kev && cvssBp >= 900, label: "CVSS ≥ 9.0", desc: "critical severity", val: 100 },
              { hit: !kev && cvssBp >= 700 && cvssBp < 900, label: "CVSS ≥ 7.0", desc: "high severity", val: 25 },
              { hit: !kev && cvssBp < 700, label: "CVSS < 7.0", desc: "medium/low", val: 0 },
            ].map((row, i) => (
              <React.Fragment key={i}>
                <span style={{ color: row.hit ? "var(--ok)" : "var(--fg-3)", fontSize: 11 }}>{row.hit ? "●" : "○"}</span>
                <span style={{ color: row.hit ? "var(--fg-0)" : "var(--fg-3)", fontWeight: row.hit ? 600 : 400 }}>
                  {row.label} <span style={{ color: "var(--fg-3)", fontWeight: 400 }}>— {row.desc}</span>
                </span>
                <span className="mono" style={{ color: row.hit ? "var(--fg-0)" : "var(--fg-3)", fontWeight: row.hit ? 700 : 400 }}>→ {row.val}</span>
              </React.Fragment>
            ))}
          </div>
          <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px solid var(--line-1)", color: "var(--fg-3)", fontSize: 11, fontStyle: "italic" }}>
            Severity-derived proxy — not a graph BFS. Production swaps in actual RyuGraph node traversal.
          </div>
        </div>
      </div>

      {/* Affected hosts */}
      {hosts.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span>affected hosts</span>
            <span className="mono" style={{ fontSize: 10.5, color: "var(--fg-2)", textTransform: "none", letterSpacing: 0 }}>
              {hosts.length} host{hosts.length !== 1 ? "s" : ""}
              {labRef && <> · lab {labRef}</>}
            </span>
          </div>
          <div style={{ padding: 4 }}>
            {hosts.map((h, i) => {
              const m = corrMap[h] || {};
              return (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "1fr auto auto", gap: 12, alignItems: "center",
                  padding: "6px 10px", fontSize: 12,
                  borderBottom: i < hosts.length - 1 ? "1px solid var(--line-1)" : "none",
                }}>
                  <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 500 }}>{h}</span>
                  {m.lab_id && <span className="mono muted" style={{ fontSize: 11 }}>lab: {m.lab_id}</span>}
                  {m.node_id && <span className="mono muted" style={{ fontSize: 11 }}>node: {m.node_id}</span>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Reconciliation */}
      {(cmdbSet.length > 0 || nautobotSet.length > 0) && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <span>source reconciliation</span>
            <span style={{
              fontSize: 10.5, padding: "2px 8px", borderRadius: 3, fontWeight: 600, textTransform: "none", letterSpacing: 0,
              background: reconciliationAnomaly ? "rgba(239,106,106,.15)" : "var(--ok-dim)",
              color: reconciliationAnomaly ? "var(--err)" : "var(--ok)",
            }}>{reconciliationAnomaly ? "ANOMALY" : "RECONCILED"}</span>
          </div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
            <span style={{ color: "var(--fg-3)" }}>CMDB matches</span>
            <span className="mono" style={{ color: "var(--fg-0)" }}>{cmdbSet.length}</span>
            <span style={{ color: "var(--fg-3)" }}>Nautobot matches</span>
            <span className="mono" style={{ color: "var(--fg-0)" }}>{nautobotSet.length}</span>
            <span style={{ color: "var(--fg-3)" }}>disposition</span>
            <span className="mono" style={{ color: disposition === "applicable" ? "var(--err)" : "var(--fg-2)" }}>{disposition || "—"}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── TransformPanel — before/after for transform-family nodes ───────────

const TRANSFORM_FIELDS = {
  canonicalize_trusted:   { inField: "raw_source_body", outField: "canonical_body", mode: "text", inLabel: "raw advisory body", outLabel: "canonical body" },
  canonicalize_untrusted: { inField: "raw_source_body", outField: "canonical_body", mode: "text", inLabel: "raw advisory body", outLabel: "canonical body",
                            extras: [{ key: "untrusted_confidence_bp", label: "confidence", fmt: (v) => (v / 100).toFixed(1) + "%" },
                                     { key: "untrusted_text_suspected", label: "suspected", fmt: (v) => v ? "yes" : "no" }] },
  enrich_cve_trusted:     { inField: "extract", outField: "extract", mode: "struct-diff", priorCheckpoint: true, inLabel: "pre-enrichment", outLabel: "enriched" },
  enrich_cve_untrusted:   { inField: "extract", outField: "extract", mode: "struct-diff", priorCheckpoint: true, inLabel: "pre-enrichment", outLabel: "enriched",
                            extras: [{ key: "untrusted_text_influenced", label: "untrusted influence", fmt: (v) => v ? "YES" : "no" }] },
};

function _findCheckpoints(runState, nodeId) {
  const cps = runState?.checkpoints || [];
  let thisCp = null, priorCp = null;
  for (let i = 0; i < cps.length; i++) {
    if (cps[i].last_node === nodeId) {
      thisCp = cps[i].state || {};
      // Walk backwards for previous checkpoint with different node
      for (let j = i - 1; j >= 0; j--) {
        if (cps[j].last_node !== nodeId) { priorCp = cps[j].state || {}; break; }
      }
      break;
    }
  }
  return { thisCp, priorCp };
}

function TextDiffView({ before, after, beforeLabel, afterLabel, unified }) {
  const b = before == null ? "" : String(before);
  const a = after == null ? "" : String(after);
  const bLen = b.length, aLen = a.length;
  const bLines = b.split("\n").length, aLines = a.split("\n").length;
  const delta = aLen - bLen;
  const deltaColor = delta === 0 ? "var(--fg-3)" : delta > 0 ? "var(--ok)" : "var(--warn)";

  if (unified) {
    const bLinesArr = b.split("\n");
    const aLinesArr = a.split("\n");
    const max = Math.max(bLinesArr.length, aLinesArr.length);
    const rows = [];
    for (let i = 0; i < max; i++) {
      const bl = bLinesArr[i];
      const al = aLinesArr[i];
      if (bl === al) {
        rows.push({ kind: "eq", text: bl ?? "" });
      } else {
        if (bl !== undefined) rows.push({ kind: "del", text: bl });
        if (al !== undefined) rows.push({ kind: "add", text: al });
      }
    }
    return (
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, fontFamily: "var(--mono)", fontSize: 11, overflow: "auto", maxHeight: 500 }}>
        {rows.map((r, i) => (
          <div key={i} style={{
            padding: "1px 10px",
            background: r.kind === "add" ? "rgba(95,207,144,.08)" : r.kind === "del" ? "rgba(239,106,106,.08)" : "transparent",
            color: r.kind === "add" ? "var(--ok)" : r.kind === "del" ? "var(--err)" : "var(--fg-2)",
            whiteSpace: "pre-wrap",
          }}>
            <span style={{ width: 12, display: "inline-block", color: "var(--fg-3)" }}>{r.kind === "add" ? "+" : r.kind === "del" ? "-" : " "}</span>
            {r.text}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
          <span>{beforeLabel}</span>
          <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-2)" }}>{bLen} ch · {bLines} ln</span>
        </div>
        <pre style={{ margin: 0, padding: 10, fontFamily: "var(--mono)", fontSize: 11, color: "var(--fg-1)", whiteSpace: "pre-wrap", overflow: "auto", maxHeight: 500 }}>{b || <span style={{ color: "var(--fg-3)", fontStyle: "italic" }}>empty</span>}</pre>
      </div>
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column", minHeight: 0 }}>
        <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
          <span>{afterLabel}</span>
          <span className="mono" style={{ textTransform: "none", letterSpacing: 0 }}>
            <span style={{ color: "var(--fg-2)" }}>{aLen} ch · {aLines} ln</span>
            <span style={{ marginLeft: 8, color: deltaColor, fontWeight: 600 }}>{delta >= 0 ? "+" : ""}{delta} ch</span>
          </span>
        </div>
        <pre style={{ margin: 0, padding: 10, fontFamily: "var(--mono)", fontSize: 11, color: "var(--fg-0)", whiteSpace: "pre-wrap", overflow: "auto", maxHeight: 500 }}>{a || <span style={{ color: "var(--fg-3)", fontStyle: "italic" }}>empty</span>}</pre>
      </div>
    </div>
  );
}

function StructDiffView({ before, after, beforeLabel, afterLabel }) {
  const b = (before && typeof before === "object") ? before : {};
  const a = (after && typeof after === "object") ? after : {};
  const allKeys = Array.from(new Set([...Object.keys(b), ...Object.keys(a)])).sort();
  const rows = allKeys.map(k => {
    const bv = b[k], av = a[k];
    const bs = bv === undefined ? null : (typeof bv === "string" ? bv : JSON.stringify(bv));
    const as = av === undefined ? null : (typeof av === "string" ? av : JSON.stringify(av));
    let kind;
    if (bv === undefined) kind = "added";
    else if (av === undefined) kind = "removed";
    else if (bs !== as) kind = "changed";
    else kind = "unchanged";
    return { key: k, kind, before: bs, after: as };
  });
  const changedCount = rows.filter(r => r.kind === "changed").length;
  const addedCount = rows.filter(r => r.kind === "added").length;
  const removedCount = rows.filter(r => r.kind === "removed").length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 6, fontSize: 11 }}>
        <span style={{ background: "rgba(95,207,144,.12)", color: "var(--ok)", padding: "2px 8px", borderRadius: 3, fontWeight: 600 }}>+{addedCount} added</span>
        <span style={{ background: "rgba(255,193,7,.12)", color: "var(--warn)", padding: "2px 8px", borderRadius: 3, fontWeight: 600 }}>~{changedCount} changed</span>
        {removedCount > 0 && <span style={{ background: "rgba(239,106,106,.12)", color: "var(--err)", padding: "2px 8px", borderRadius: 3, fontWeight: 600 }}>-{removedCount} removed</span>}
        <span style={{ background: "rgba(255,255,255,.04)", color: "var(--fg-3)", padding: "2px 8px", borderRadius: 3 }}>{rows.length - changedCount - addedCount - removedCount} unchanged</span>
      </div>
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, overflow: "auto", maxHeight: 500 }}>
        <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "grid", gridTemplateColumns: "180px 1fr 1fr", gap: 10 }}>
          <span>field</span><span>{beforeLabel}</span><span>{afterLabel}</span>
        </div>
        {rows.map((r, i) => {
          const bg = r.kind === "added" ? "rgba(95,207,144,.04)" : r.kind === "changed" ? "rgba(255,193,7,.04)" : r.kind === "removed" ? "rgba(239,106,106,.04)" : "transparent";
          const keyColor = r.kind === "unchanged" ? "var(--fg-3)" : "var(--fg-0)";
          const beforeColor = r.kind === "removed" ? "var(--err)" : r.kind === "changed" ? "var(--fg-3)" : r.kind === "added" ? "var(--fg-3)" : "var(--fg-2)";
          const afterColor = r.kind === "added" ? "var(--ok)" : r.kind === "changed" ? "var(--warn)" : r.kind === "removed" ? "var(--fg-3)" : "var(--fg-2)";
          return (
            <div key={r.key} style={{ display: "grid", gridTemplateColumns: "180px 1fr 1fr", gap: 10, padding: "5px 12px", fontSize: 11.5, background: bg, borderBottom: i < rows.length - 1 ? "1px solid var(--line-1)" : "none" }}>
              <span className="mono" style={{ color: keyColor, fontWeight: r.kind !== "unchanged" ? 600 : 400 }}>{r.key}</span>
              <span className="mono" style={{ color: beforeColor, wordBreak: "break-all" }}>{r.before == null ? <span style={{ fontStyle: "italic", color: "var(--fg-3)" }}>—</span> : (r.before.length > 100 ? r.before.slice(0, 100) + "…" : r.before)}</span>
              <span className="mono" style={{ color: afterColor, wordBreak: "break-all" }}>{r.after == null ? <span style={{ fontStyle: "italic", color: "var(--fg-3)" }}>—</span> : (r.after.length > 100 ? r.after.slice(0, 100) + "…" : r.after)}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TextToStructView({ inputText, outputStruct, inLabel, outLabel }) {
  const txt = inputText == null ? "" : String(inputText);
  const struct = (outputStruct && typeof outputStruct === "object") ? outputStruct : {};
  const entries = Object.entries(struct).filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && v.length === 0));

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
          <span>{inLabel}</span>
          <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-2)" }}>{txt.length} ch</span>
        </div>
        <pre style={{ margin: 0, padding: 10, fontFamily: "var(--mono)", fontSize: 11, color: "var(--fg-1)", whiteSpace: "pre-wrap", overflow: "auto", maxHeight: 500 }}>{txt}</pre>
      </div>
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
          <span>{outLabel}</span>
          <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-2)" }}>{entries.length} fields</span>
        </div>
        <div style={{ padding: 10, overflow: "auto", maxHeight: 500, display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 12px", fontSize: 11.5 }}>
          {entries.map(([k, v]) => (
            <React.Fragment key={k}>
              <span style={{ color: "var(--fg-3)" }}>{k}</span>
              <span className="mono" style={{ color: "var(--fg-0)", wordBreak: "break-all" }}>{typeof v === "string" ? v : JSON.stringify(v)}</span>
            </React.Fragment>
          ))}
          {entries.length === 0 && <span className="muted" style={{ gridColumn: "1 / -1", fontStyle: "italic" }}>no fields extracted</span>}
        </div>
      </div>
    </div>
  );
}

function TransformPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const cfg = TRANSFORM_FIELDS[node.id];

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId={node.id} />;

  const [unified, setUnified] = React.useState(false);
  const { thisCp, priorCp } = _findCheckpoints(runState, node.id);

  // Pull before/after values
  let beforeVal, afterVal;
  if (cfg.mode === "struct-diff") {
    beforeVal = (priorCp || {})[cfg.inField];
    afterVal = (thisCp || runState || {})[cfg.outField];
  } else if (cfg.mode === "text-to-struct") {
    beforeVal = (thisCp || runState || {})[cfg.inField];
    afterVal = (thisCp || runState || {})[cfg.outField];
  } else {
    // text mode: input is in prior checkpoint OR current state, output is current
    beforeVal = (priorCp || thisCp || runState || {})[cfg.inField];
    afterVal = (thisCp || runState || {})[cfg.outField];
  }

  const extras = (cfg.extras || []).map(e => {
    const v = (thisCp || runState || {})[e.key];
    if (v === undefined || v === null) return null;
    return { label: e.label, value: e.fmt ? e.fmt(v) : String(v), key: e.key };
  }).filter(Boolean);

  return (
    <div data-panel-id={node.id} style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="mono muted" style={{ fontSize: 11.5, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>
          Transform · {node.id}
          {typeof timing?.elapsed_ms === "number" && <> · {(timing.elapsed_ms / 1000).toFixed(2)}s</>}
        </span>
        {cfg.mode === "text" && (
          <div style={{ display: "flex", gap: 4 }}>
            <button
              onClick={() => setUnified(false)}
              style={{ padding: "3px 10px", fontSize: 11, background: !unified ? "var(--accent-dim)" : "transparent", color: !unified ? "var(--accent)" : "var(--fg-3)", border: "1px solid " + (!unified ? "var(--accent)" : "var(--line-2)"), borderRadius: 3, cursor: "pointer" }}
            >side-by-side</button>
            <button
              onClick={() => setUnified(true)}
              style={{ padding: "3px 10px", fontSize: 11, background: unified ? "var(--accent-dim)" : "transparent", color: unified ? "var(--accent)" : "var(--fg-3)", border: "1px solid " + (unified ? "var(--accent)" : "var(--line-2)"), borderRadius: 3, cursor: "pointer" }}
            >unified diff</button>
          </div>
        )}
      </div>

      {extras.length > 0 && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          {extras.map(e => (
            <span key={e.key} style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", padding: "4px 10px", borderRadius: 4, fontSize: 11 }}>
              <span style={{ color: "var(--fg-3)" }}>{e.label} </span>
              <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{e.value}</span>
            </span>
          ))}
        </div>
      )}

      {cfg.mode === "text" && (
        <TextDiffView before={beforeVal} after={afterVal} beforeLabel={cfg.inLabel} afterLabel={cfg.outLabel} unified={unified} />
      )}
      {cfg.mode === "text-to-struct" && (
        <TextToStructView inputText={beforeVal} outputStruct={afterVal} inLabel={cfg.inLabel} outLabel={cfg.outLabel} />
      )}
      {cfg.mode === "struct-diff" && (
        <StructDiffView before={beforeVal} after={afterVal} beforeLabel={cfg.inLabel} afterLabel={cfg.outLabel} />
      )}
    </div>
  );
}

// ─── FrameworkMappingPanel ──────────────────────────────────────────────

function FrameworkMappingPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="kg" lifecycle={lifecycle} panelId="framework_mapping" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const controls = allState.framework_controls || runState.framework_controls || [];
  const patterns = allState.attack_patterns || runState.attack_patterns || [];
  const fmStatus = allState.framework_mapping_status || runState.framework_mapping_status || "";
  const fmError = allState.last_framework_mapping_error || runState.last_framework_mapping_error || "";
  const extract = allState.extract || runState.extract || {};
  const cwe = extract.cwe_class || "";
  const elapsedMs = timing?.elapsed_ms;

  const STATUS_INFO = {
    ok:                    { color: "var(--ok)",   bg: "var(--ok-dim)",          icon: "✓", label: "MAPPED",        desc: "Doctrine retrieved from RyuGraph" },
    empty:                 { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)",  icon: "○", label: "NO MAPPINGS",   desc: "CWE has no entries in doctrine subgraph" },
    no_cwe:                { color: "var(--warn)", bg: "rgba(255,193,7,.12)",    icon: "⊘", label: "SKIPPED",       desc: "No CWE classification in extract — cannot query doctrine" },
    neo4j_creds_unset:     { color: "var(--err)",  bg: "rgba(239,106,106,.12)",  icon: "✕", label: "CREDS UNSET",   desc: "RYUGRAPH_URL / USERNAME / PASSWORD env vars not configured" },
    neo4j_driver_missing:  { color: "var(--err)",  bg: "rgba(239,106,106,.12)",  icon: "✕", label: "DRIVER MISSING", desc: "neo4j Python driver not installed" },
    error:                 { color: "var(--err)",  bg: "rgba(239,106,106,.12)",  icon: "✕", label: "ERROR",         desc: "Query failed — see error below" },
  };
  const si = STATUS_INFO[fmStatus] || { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "?", label: fmStatus || "UNKNOWN", desc: "" };

  const totalCount = controls.length + patterns.length;

  return (
    <div data-panel-id="framework_mapping" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      {fmError && <ErrorBanner field="framework_mapping" value={fmError} />}

      <div className="mono muted" style={{ fontSize: 11.5 }}>
        RyuGraph · neo4j · cve_rem.framework_mapping
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* Verdict */}
      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: si.bg, border: "1px solid " + si.color + "55",
        display: "flex", alignItems: "center", gap: 16,
      }}>
        <div style={{ fontSize: 26, color: si.color, lineHeight: 1, fontWeight: 700 }}>{si.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: si.color }}>{si.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{si.desc}</div>
        </div>
        {fmStatus === "ok" && (
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 22, color: "var(--fg-0)", fontWeight: 700 }}>{totalCount}</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>doctrine refs</div>
          </div>
        )}
      </div>

      {/* Provenance */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 11.5 }}>
        <span style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", padding: "4px 10px", borderRadius: 4 }}>
          <span style={{ color: "var(--fg-3)" }}>source CWE </span>
          {cwe ? (
            <span
              onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "extract_trusted" } }))}
              className="mono"
              style={{ color: "var(--info)", cursor: "pointer", fontWeight: 600 }}
              title="Jump to extract_trusted (source of cwe_class)"
            >{cwe} ↗</span>
          ) : <span className="mono" style={{ color: "var(--fg-3)" }}>—</span>}
        </span>
        <span style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", padding: "4px 10px", borderRadius: 4 }}>
          <span style={{ color: "var(--fg-3)" }}>schema </span>
          <span className="mono" style={{ color: "var(--fg-1)" }}>(Control)-[:MAPS_TO]→(CWE)</span>
          <span style={{ color: "var(--fg-3)" }}> · </span>
          <span className="mono" style={{ color: "var(--fg-1)" }}>(Capec)-[:WEAKNESS]→(CWE)</span>
        </span>
      </div>

      {/* Results — only when ok */}
      {fmStatus === "ok" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          {/* NIST controls */}
          <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column" }}>
            <div style={{
              padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
              letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
              display: "flex", justifyContent: "space-between",
            }}>
              <span>NIST 800-53 controls</span>
              <span className="mono" style={{ color: "var(--fg-2)", textTransform: "none", letterSpacing: 0 }}>{controls.length}</span>
            </div>
            <div style={{ padding: 4, maxHeight: 400, overflow: "auto" }}>
              {controls.length === 0 ? (
                <div className="muted" style={{ padding: 10, fontSize: 12, fontStyle: "italic" }}>no controls mapped</div>
              ) : controls.map((c, i) => {
                const id = c.id || "";
                const name = c.name || "";
                return (
                  <a
                    key={i}
                    href={id ? `https://csrc.nist.gov/projects/risk-management/sp800-53-controls/release-search#!/control?version=5.1&number=${encodeURIComponent(id)}` : undefined}
                    target="_blank"
                    rel="noopener"
                    style={{
                      display: "grid", gridTemplateColumns: "70px 1fr", gap: 10, alignItems: "center",
                      padding: "6px 10px", fontSize: 12, textDecoration: "none",
                      borderBottom: i < controls.length - 1 ? "1px solid var(--line-1)" : "none",
                    }}
                  >
                    <span className="mono" style={{ color: "var(--info)", fontWeight: 600 }}>{id}</span>
                    <span style={{ color: "var(--fg-1)" }}>{name}</span>
                  </a>
                );
              })}
            </div>
          </div>

          {/* CAPEC patterns */}
          <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", flexDirection: "column" }}>
            <div style={{
              padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
              letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
              display: "flex", justifyContent: "space-between",
            }}>
              <span>CAPEC attack patterns</span>
              <span className="mono" style={{ color: "var(--fg-2)", textTransform: "none", letterSpacing: 0 }}>{patterns.length}</span>
            </div>
            <div style={{ padding: 4, maxHeight: 400, overflow: "auto" }}>
              {patterns.length === 0 ? (
                <div className="muted" style={{ padding: 10, fontSize: 12, fontStyle: "italic" }}>no patterns mapped</div>
              ) : patterns.map((p, i) => {
                const id = p.id || "";
                const name = p.name || "";
                const num = id.replace(/^CAPEC-/, "");
                return (
                  <a
                    key={i}
                    href={num ? `https://capec.mitre.org/data/definitions/${encodeURIComponent(num)}.html` : undefined}
                    target="_blank"
                    rel="noopener"
                    style={{
                      display: "grid", gridTemplateColumns: "90px 1fr", gap: 10, alignItems: "center",
                      padding: "6px 10px", fontSize: 12, textDecoration: "none",
                      borderBottom: i < patterns.length - 1 ? "1px solid var(--line-1)" : "none",
                    }}
                  >
                    <span className="mono" style={{ color: "var(--info)", fontWeight: 600 }}>{id}</span>
                    <span style={{ color: "var(--fg-1)" }}>{name}</span>
                  </a>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* No-CWE explainer */}
      {fmStatus === "no_cwe" && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, padding: 14, fontSize: 12, color: "var(--fg-2)" }}>
          Doctrine retrieval requires a CWE classification from upstream extract. Verify <span
            onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "extract_trusted" } }))}
            className="mono"
            style={{ color: "var(--info)", cursor: "pointer" }}
          >extract_trusted ↗</span> or <span
            onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "extract_untrusted" } }))}
            className="mono"
            style={{ color: "var(--info)", cursor: "pointer" }}
          >extract_untrusted ↗</span> populated cwe_class.
        </div>
      )}

      {/* Infra hint for creds/driver */}
      {(fmStatus === "neo4j_creds_unset" || fmStatus === "neo4j_driver_missing") && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, padding: 14, fontSize: 11.5, color: "var(--fg-2)", fontFamily: "var(--mono)" }}>
          {fmStatus === "neo4j_creds_unset" ? (
            <>Set <span style={{ color: "var(--fg-0)" }}>RYUGRAPH_URL</span>, <span style={{ color: "var(--fg-0)" }}>RYUGRAPH_USERNAME</span>, <span style={{ color: "var(--fg-0)" }}>RYUGRAPH_PASSWORD</span> (NEO4J_* aliases also accepted).</>
          ) : (
            <>Install: <span style={{ color: "var(--fg-0)" }}>uv add neo4j</span></>
          )}
        </div>
      )}
    </div>
  );
}

// ─── CargonetLabTelemetryPanel ─────────────────────────────────────────

function CargonetLabTelemetryPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId="cargonet_lab_telemetry" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const hosts = allState.affected_host_names || runState.affected_host_names || [];
  const corrMap = allState.cargonet_correlation_map || runState.cargonet_correlation_map || {};
  const labRef = allState.cargonet_lab_ref || runState.cargonet_lab_ref || "";
  const cargonetCount = allState.cargonet_node_count || runState.cargonet_node_count || 0;
  const env = allState.broker_request_envelope || runState.broker_request_envelope || {};
  const retrievals = env.retrievals || [];
  const elapsedMs = timing?.elapsed_ms;

  const hostsCovered = hosts.filter(h => corrMap[h]).length;

  return (
    <div data-panel-id="cargonet_lab_telemetry" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        CargoNet broker · cve_rem.cargonet_lab_telemetry
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* Hero — telemetry coverage */}
      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: hostsCovered > 0 ? "var(--ok-dim)" : "rgba(255,255,255,.04)",
        border: "1px solid " + (hostsCovered > 0 ? "var(--ok)" : "var(--line-2)") + "55",
        display: "flex", alignItems: "center", gap: 20,
      }}>
        <div style={{ fontSize: 32, color: hostsCovered > 0 ? "var(--ok)" : "var(--fg-3)", fontWeight: 700, fontFamily: "var(--mono)", lineHeight: 1 }}>
          {hostsCovered}<span style={{ fontSize: 16, color: "var(--fg-3)", fontWeight: 400 }}>/{hosts.length}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: hostsCovered > 0 ? "var(--ok)" : "var(--fg-2)" }}>
            {hostsCovered > 0 ? "TELEMETRY AVAILABLE" : "NO HOSTS MAPPED"}
          </div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
            {hostsCovered > 0
              ? `${hostsCovered} affected host${hostsCovered !== 1 ? "s" : ""} resolved to cargonet lab nodes`
              : "Upstream correlate_assets produced no host mappings"}
          </div>
          {labRef && <div className="mono" style={{ fontSize: 11, color: "var(--fg-3)" }}>lab: {labRef}</div>}
        </div>
      </div>

      {/* Host → lab/node mapping */}
      {hosts.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between",
          }}>
            <span>host → cargonet lab node</span>
            <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-2)" }}>{hosts.length} host{hosts.length !== 1 ? "s" : ""}</span>
          </div>
          <div style={{ padding: 4 }}>
            {hosts.map((h, i) => {
              const m = corrMap[h] || {};
              const hasMapping = !!(m.lab_id || m.node_id);
              return (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "1fr auto auto auto", gap: 12, alignItems: "center",
                  padding: "6px 10px", fontSize: 12,
                  borderBottom: i < hosts.length - 1 ? "1px solid var(--line-1)" : "none",
                }}>
                  <span className="mono" style={{ color: hasMapping ? "var(--fg-0)" : "var(--fg-3)", fontWeight: 500 }}>{h}</span>
                  {m.lab_id ? <span className="mono muted" style={{ fontSize: 11 }}>lab: {m.lab_id}</span> : <span style={{ width: 1 }} />}
                  {m.node_id ? <span className="mono muted" style={{ fontSize: 11 }}>node: {m.node_id}</span> : <span style={{ width: 1 }} />}
                  {hasMapping
                    ? <span style={{ color: "var(--ok)", fontSize: 13 }}>✓</span>
                    : <span style={{ color: "var(--fg-3)", fontSize: 13 }}>—</span>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Provenance — broker envelope */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <div style={{
          padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
          letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
        }}>broker envelope · retrieval markers</div>
        <div style={{ padding: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
          {retrievals.length === 0 ? (
            <span className="muted" style={{ fontSize: 12, fontStyle: "italic" }}>no retrievals recorded</span>
          ) : retrievals.map((r, i) => (
            <span key={i} className="mono" style={{
              fontSize: 11, padding: "2px 8px", borderRadius: 3, fontWeight: 600,
              background: r === "cargonet_lab_telemetry" ? "var(--accent-dim)" : "rgba(255,255,255,.06)",
              color: r === "cargonet_lab_telemetry" ? "var(--accent)" : "var(--fg-2)",
            }}>{r}</span>
          ))}
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Stand-in: marker-only retrieval. Production swap pulls live lab telemetry (NetFlow, syslog, snmp) from each affected host via cargonet broker.
      </div>
    </div>
  );
}

// ─── PlannerPanel ──────────────────────────────────────────────────────

function PlannerPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="llm" lifecycle={lifecycle} panelId="planner" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const planHash = allState.plan_hash || runState.plan_hash || "";
  const codeRuntime = allState.code_runtime || runState.code_runtime || "";
  const sandboxRuntime = allState.sandbox_runtime || runState.sandbox_runtime || "";
  const rationale = allState.plan_rationale || runState.plan_rationale || "";
  const latency = allState.planner_latency_ms || runState.planner_latency_ms;
  const planSpec = allState.plan_spec || runState.plan_spec || {};
  const deficits = allState.plan_spec_deficits || runState.plan_spec_deficits || [];
  const agentTrace = allState.plan_agent_trace || allState.agent_trace || runState.plan_agent_trace || [];
  const lmError = allState.last_planner_lm_error || runState.last_planner_lm_error || "";
  const templateHit = allState.template_lookup_hit || runState.template_lookup_hit;
  const priorCount = allState.prior_retro_count || runState.prior_retro_count || 0;
  const priorOutcomes = allState.prior_retro_outcomes || runState.prior_retro_outcomes || {};
  const schemaRetries = allState.planner_schema_retries || runState.planner_schema_retries || 0;
  const ragSources = allState.rag_sources || runState.rag_sources || [];

  // Plan spec slot completeness
  const slots = ["apply", "verify", "rollback"];
  const slotStatus = slots.map(s => {
    const o = planSpec[s] || {};
    const filled = !!(o.intent || o.primitive || o.target || o.target_version);
    return { name: s, filled, obj: o };
  });
  const slotsFilled = slotStatus.filter(s => s.filled).length;
  const hasPlanSpec = slotsFilled > 0;

  const generationPath = hasPlanSpec
    ? { label: "STRUCTURED PLAN", color: "var(--ok)", desc: "Plan spec emitted — deterministic bundle synthesis" }
    : rationale
    ? { label: "LM RATIONALE ONLY", color: "var(--warn)", desc: "No structured plan spec — code_writer will use LM generator" }
    : { label: "EMPTY PLAN", color: "var(--err)", desc: "Neither plan_spec nor rationale produced" };

  return (
    <div data-panel-id="planner" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      {lmError && <ErrorBanner field="planner LM" value={lmError} />}

      <div className="mono muted" style={{ fontSize: 11.5 }}>
        LM planner · cve_rem.planner
        {latency != null && <> · {(latency / 1000).toFixed(2)}s</>}
        {schemaRetries > 0 && <> · {schemaRetries} schema retries</>}
      </div>

      {/* Verdict */}
      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: generationPath.color === "var(--ok)" ? "var(--ok-dim)" : generationPath.color === "var(--warn)" ? "rgba(255,193,7,.10)" : "rgba(239,106,106,.10)",
        border: "1px solid " + generationPath.color + "55",
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: generationPath.color }}>{generationPath.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{generationPath.desc}</div>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {codeRuntime && <span style={{ background: "var(--accent-dim)", color: "var(--accent)", fontSize: 11, padding: "3px 10px", borderRadius: 3, fontWeight: 600 }}>code: {codeRuntime}</span>}
          {sandboxRuntime && <span style={{ background: "rgba(122,182,255,.12)", color: "var(--info)", fontSize: 11, padding: "3px 10px", borderRadius: 3, fontWeight: 600 }}>sandbox: {sandboxRuntime}</span>}
        </div>
      </div>

      {/* Plan hash + template hit + prior retros row */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", fontSize: 11.5 }}>
        {planHash && (
          <span style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", padding: "4px 10px", borderRadius: 4 }}>
            <span style={{ color: "var(--fg-3)" }}>plan_hash </span>
            <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{planHash}</span>
          </span>
        )}
        {templateHit && (
          <span style={{ background: "var(--ok-dim)", color: "var(--ok)", padding: "4px 10px", borderRadius: 4, fontWeight: 600 }}>
            ✓ template cache hit
          </span>
        )}
        {priorCount > 0 && (
          <span style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", padding: "4px 10px", borderRadius: 4 }}>
            <span style={{ color: "var(--fg-3)" }}>prior retros </span>
            <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{priorCount}</span>
            {Object.keys(priorOutcomes).length > 0 && (
              <span style={{ color: "var(--fg-3)" }}> · {Object.entries(priorOutcomes).map(([k, v]) => `${k}:${v}`).join(" / ")}</span>
            )}
          </span>
        )}
        {agentTrace.length > 0 && (
          <span style={{ background: "rgba(122,182,255,.10)", color: "var(--info)", padding: "4px 10px", borderRadius: 4, fontWeight: 600 }}>
            multi-turn agent · {agentTrace.length} turn{agentTrace.length !== 1 ? "s" : ""}
          </span>
        )}
        {ragSources.length > 0 && (
          <span style={{ background: "rgba(122,182,255,.10)", color: "var(--info)", padding: "4px 10px", borderRadius: 4, fontWeight: 600 }}>
            RAG · {ragSources.length} source{ragSources.length !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Plan spec slots */}
      {hasPlanSpec && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between",
          }}>
            <span>plan spec · 3-slot</span>
            <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-2)" }}>{slotsFilled}/3 filled</span>
          </div>
          <div style={{ padding: 4 }}>
            {slotStatus.map((s, i) => (
              <div key={s.name} style={{
                padding: "8px 12px", borderBottom: i < slotStatus.length - 1 ? "1px solid var(--line-1)" : "none",
                display: "grid", gridTemplateColumns: "70px auto 1fr auto", gap: 12, alignItems: "center", fontSize: 12,
              }}>
                <span style={{
                  fontSize: 10, fontWeight: 700, padding: "2px 8px", borderRadius: 3, textAlign: "center", letterSpacing: ".04em",
                  background: s.filled ? "var(--ok-dim)" : "rgba(255,255,255,.06)",
                  color: s.filled ? "var(--ok)" : "var(--fg-3)",
                }}>{s.name.toUpperCase()}</span>
                {s.filled ? (
                  <>
                    <span className="mono" style={{ color: "var(--fg-3)", fontSize: 11 }}>{s.obj.primitive || "—"}</span>
                    <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 500 }}>
                      {s.obj.target || ""}
                      {s.obj.target_version && <span style={{ color: "var(--fg-2)" }}>@{s.obj.target_version}</span>}
                    </span>
                    {s.obj.cite_url && (
                      <a href={s.obj.cite_url} target="_blank" rel="noopener" className="mono" style={{ fontSize: 10.5, color: "var(--info)", textDecoration: "none" }}>cite ↗</a>
                    )}
                  </>
                ) : (
                  <span className="muted" style={{ gridColumn: "2 / -1", fontStyle: "italic" }}>empty slot</span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Deficits */}
      {deficits.length > 0 && (
        <div style={{ background: "rgba(255,193,7,.06)", border: "1px solid rgba(255,193,7,.3)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--warn)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 700, borderBottom: "1px solid rgba(255,193,7,.2)",
          }}>plan deficits · {deficits.length}</div>
          <div style={{ padding: 8 }}>
            {deficits.map((d, i) => (
              <div key={i} style={{ fontSize: 11.5, padding: "4px 8px", color: "var(--fg-1)" }}>
                <span className="mono" style={{ color: "var(--warn)", fontWeight: 600 }}>{d.kind || "deficit"}</span>
                {d.slot && <span style={{ color: "var(--fg-3)" }}> · slot={d.slot}</span>}
                {d.detail && <span style={{ color: "var(--fg-2)" }}> — {d.detail}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* LM rationale */}
      {rationale && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between",
          }}>
            <span>LM rationale</span>
            <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-2)" }}>{rationale.length} chars</span>
          </div>
          <div style={{ padding: 14, fontSize: 12.5, color: "var(--fg-1)", lineHeight: 1.55, whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>
            {rationale}
          </div>
        </div>
      )}

      {/* RAG sources */}
      {ragSources.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          }}>RAG sources fetched</div>
          <div style={{ padding: 8 }}>
            {ragSources.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, fontSize: 11.5, padding: "4px 6px", alignItems: "center" }}>
                {r.url ? <a href={r.url} target="_blank" rel="noopener" className="mono" style={{ color: "var(--info)", textDecoration: "none", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.url} ↗</a> : <span className="mono muted" style={{ flex: 1 }}>{r.id || `source ${i + 1}`}</span>}
                {r.bytes != null && <span className="mono muted" style={{ fontSize: 10.5 }}>{r.bytes}b</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── CodeWriterPanel ───────────────────────────────────────────────────

function CodeWriterPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="llm" lifecycle={lifecycle} panelId="code_writer" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const bundle = allState.bundle || runState.bundle || {};
  const runtime = bundle.runtime || allState.code_runtime || "";
  const applyRef = bundle.apply_bundle_ref || "";
  const rollbackRef = bundle.rollback_bundle_ref || "";
  const verifyRef = bundle.verify_probe_ref || "";
  const meta = bundle.metadata || {};
  const generatedBy = meta.generated_by || "";
  const elapsedMs = timing?.elapsed_ms;

  const GEN_INFO = {
    plan_spec_deterministic: { label: "DETERMINISTIC", sublabel: "plan_spec", color: "var(--ok)",   bg: "var(--ok-dim)",         icon: "⚙", desc: "Built from structured plan_spec — no LM in bundle path", safety: "high" },
    probe_primitives:        { label: "PRIMITIVES",    sublabel: "no-LM",     color: "var(--ok)",   bg: "var(--ok-dim)",         icon: "⚒", desc: "Built from isolate/disable/quarantine primitives — no LM fabrication", safety: "high" },
    lm_code_writer:          { label: "LM-GENERATED",  sublabel: "synthesized", color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "✦", desc: "Ansible YAML synthesized by LM — must pass safe_load + critic", safety: "needs-critic" },
  };
  const gi = GEN_INFO[generatedBy] || { label: (generatedBy || "UNKNOWN").toUpperCase(), sublabel: "", color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "?", desc: "Unknown synthesis path", safety: "unknown" };

  const RUNTIME_INFO = {
    ansible:               { icon: "🅰", desc: "Ansible playbook on remote hosts (apt/yum/pip/systemctl)" },
    container_image_bump:  { icon: "📦", desc: "Container image tag bump + rolling restart" },
    kubernetes_helm:       { icon: "⎈", desc: "Helm values patch + helm upgrade" },
    static_detection:      { icon: "👁", desc: "Static probe — read-only; no apply" },
    cargonet_lab:          { icon: "🧪", desc: "CargoNet sandboxed lab apply (revertible)" },
    docker_compose:        { icon: "🐳", desc: "docker-compose up with new image" },
  };
  const ri = RUNTIME_INFO[runtime] || { icon: "·", desc: "Runtime-specific apply harness" };

  const refs = [
    { slot: "APPLY",    ref: applyRef,    desc: "Playbook executed on hosts", color: "var(--info)" },
    { slot: "ROLLBACK", ref: rollbackRef, desc: "Reverses the apply on failure", color: "var(--warn)" },
    { slot: "VERIFY",   ref: verifyRef,   desc: "Confirms apply succeeded",      color: "var(--ok)"   },
  ];
  const refsFilled = refs.filter(r => r.ref).length;

  const shortRef = (r) => {
    if (!r) return "";
    const m = r.match(/\/([a-f0-9]{16})[a-f0-9]*_/);
    return m ? m[1] : r.split("/").pop();
  };

  return (
    <div data-panel-id="code_writer" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Remediation bundle synthesizer · cve_rem.code_writer
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* Hero */}
      <div style={{ padding: "18px 22px", borderRadius: 10, background: gi.bg, border: "1px solid " + gi.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 48, height: 48, borderRadius: 24, background: gi.color + "22", border: "1px solid " + gi.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, color: gi.color, fontWeight: 700 }}>{gi.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
            <span style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: gi.color }}>{gi.label}</span>
            {gi.sublabel && <span className="mono" style={{ fontSize: 10.5, color: gi.color, opacity: 0.7 }}>· {gi.sublabel}</span>}
          </div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{gi.desc}</div>
        </div>
        <span style={{ background: refsFilled === 3 ? "var(--ok)22" : "var(--err)22", color: refsFilled === 3 ? "var(--ok)" : "var(--err)", fontSize: 11, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>{refsFilled}/3 SLOTS</span>
      </div>

      {/* Runtime selection */}
      {runtime && (
        <div style={{ padding: "13px 16px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, display: "flex", alignItems: "center", gap: 14 }}>
          <div style={{ fontSize: 24, lineHeight: 1 }}>{ri.icon}</div>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>runtime</span>
              <span className="mono" style={{ fontSize: 13, color: "var(--accent)", fontWeight: 600 }}>{runtime}</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--fg-2)", marginTop: 2 }}>{ri.desc}</div>
          </div>
        </div>
      )}

      {/* 3-slot bundle cards (side-by-side) */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        {refs.map((r) => (
          <div key={r.slot} style={{
            padding: "12px 13px",
            background: r.ref ? "var(--bg-3)" : "rgba(239,106,106,.06)",
            border: "1px solid " + (r.ref ? r.color + "44" : "var(--err)33"),
            borderRadius: 8,
            display: "flex", flexDirection: "column", gap: 6, minHeight: 100,
          }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <span style={{ fontSize: 10.5, color: r.ref ? r.color : "var(--err)", fontWeight: 700, letterSpacing: ".08em" }}>{r.slot}</span>
              <span style={{ fontSize: 14, color: r.ref ? r.color : "var(--err)" }}>{r.ref ? "✓" : "✗"}</span>
            </div>
            <div style={{ fontSize: 10.5, color: "var(--fg-2)", lineHeight: 1.4 }}>{r.desc}</div>
            {r.ref ? (
              <div className="mono" style={{ fontSize: 10.5, color: "var(--fg-0)", wordBreak: "break-all", marginTop: 2, padding: "4px 6px", background: "rgba(0,0,0,.2)", borderRadius: 3 }}>
                {shortRef(r.ref)}
              </div>
            ) : (
              <div style={{ fontSize: 10.5, color: "var(--err)", fontStyle: "italic", marginTop: 4 }}>missing — critic vetos</div>
            )}
          </div>
        ))}
      </div>

      {/* Full refs collapsible */}
      {(applyRef || rollbackRef || verifyRef) && (
        <details style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <summary style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, cursor: "pointer", listStyle: "none", display: "flex", justifyContent: "space-between" }}>
            <span>full artifact paths</span>
            <span style={{ color: "var(--fg-3)" }}>▸</span>
          </summary>
          <div style={{ padding: "10px 14px", borderTop: "1px solid var(--line-1)", display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 12px", fontSize: 10.5 }}>
            {applyRef && (<><span style={{ color: "var(--info)", fontWeight: 600 }}>apply</span><span className="mono" style={{ color: "var(--fg-0)", wordBreak: "break-all" }}>{applyRef}</span></>)}
            {rollbackRef && (<><span style={{ color: "var(--warn)", fontWeight: 600 }}>rollback</span><span className="mono" style={{ color: "var(--fg-0)", wordBreak: "break-all" }}>{rollbackRef}</span></>)}
            {verifyRef && (<><span style={{ color: "var(--ok)", fontWeight: 600 }}>verify</span><span className="mono" style={{ color: "var(--fg-0)", wordBreak: "break-all" }}>{verifyRef}</span></>)}
          </div>
        </details>
      )}

      {/* Metadata */}
      {Object.keys(meta).length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8 }}>
          <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>bundle metadata</div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 14px", fontSize: 11.5 }}>
            {Object.entries(meta).map(([k, v]) => (
              <React.Fragment key={k}>
                <span style={{ color: "var(--fg-3)" }}>{k}</span>
                <span className="mono" style={{ color: k === "generated_by" ? gi.color : "var(--fg-0)", wordBreak: "break-all" }}>{typeof v === "string" ? v : JSON.stringify(v)}</span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic", padding: "10px 13px", background: "rgba(122,162,247,.05)", border: "1px solid rgba(122,162,247,.18)", borderRadius: 6 }}>
        Bundle feeds <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "emit_remediation_bundle" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer", fontStyle: "normal" }}
        >emit_remediation_bundle ↗</span> (content-addressed write) then <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "validate_dispatch" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer", fontStyle: "normal" }}
        >validate_dispatch ↗</span> (safety/lint/critic) before any sandbox or production apply.
      </div>
    </div>
  );
}

// ─── EmitRemediationBundlePanel ────────────────────────────────────────

function EmitRemediationBundlePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="artifact" lifecycle={lifecycle} panelId="emit_remediation_bundle" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const ref = allState.remediation_bundle_artifact_ref || runState.remediation_bundle_artifact_ref || "";
  const bundle = allState.bundle || runState.bundle || {};
  const elapsedMs = timing?.elapsed_ms;

  // Extract content-addressed digest from file:// path
  const digestMatch = ref.match(/([a-f0-9]{64})\.json$/);
  const digest = digestMatch ? digestMatch[1] : "";
  const digestShort = digest ? digest.slice(0, 16) : "";

  return (
    <div data-panel-id="emit_remediation_bundle" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Content-addressed write · cve_rem.emit_remediation_bundle
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* Verdict */}
      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: ref ? "var(--ok-dim)" : "rgba(239,106,106,.12)",
        border: "1px solid " + (ref ? "var(--ok)" : "var(--err)") + "55",
        display: "flex", alignItems: "center", gap: 16,
      }}>
        <div style={{ fontSize: 26, color: ref ? "var(--ok)" : "var(--err)", fontWeight: 700, lineHeight: 1 }}>
          {ref ? "✓" : "✕"}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: ref ? "var(--ok)" : "var(--err)" }}>
            {ref ? "PERSISTED" : "NO ARTIFACT REF"}
          </div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
            {ref ? "Bundle written to artifacts root, content-addressed by blake3 of canonical JSON" : "emit_remediation_bundle did not produce a ref"}
          </div>
        </div>
        {digestShort && (
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 12, color: "var(--fg-0)", fontWeight: 700 }}>{digestShort}…</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>blake3</div>
          </div>
        )}
      </div>

      {/* Artifact ref */}
      {ref && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          }}>artifact ref</div>
          <div style={{ padding: 12, fontSize: 11.5, color: "var(--fg-0)", wordBreak: "break-all" }} className="mono">{ref}</div>
        </div>
      )}

      {/* Bundle contents being persisted */}
      {Object.keys(bundle).length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          }}>bundle contents (persisted as canonical JSON)</div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 11.5 }}>
            {[
              ["runtime", bundle.runtime],
              ["apply", bundle.apply_bundle_ref],
              ["rollback", bundle.rollback_bundle_ref],
              ["verify", bundle.verify_probe_ref],
              ["generated_by", (bundle.metadata || {}).generated_by],
            ].filter(([, v]) => v).map(([k, v]) => (
              <React.Fragment key={k}>
                <span style={{ color: "var(--fg-3)" }}>{k}</span>
                <span className="mono" style={{ color: "var(--fg-1)", wordBreak: "break-all" }}>{String(v)}</span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Content-addressed: identical bundles dedupe to the same digest path. Tamper-evident — any post-write mutation breaks the hash.
      </div>
    </div>
  );
}

// ─── ValidateDispatchPanel — parallel fan-out helper ───────────────────

function ValidateDispatchPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId="validate_dispatch" />;

  const elapsedMs = timing?.elapsed_ms;
  const targets = [
    { id: "judge_safety", label: "Code-safety judge", desc: "Fathom-style critic + watermark recheck" },
    { id: "judge_lint",   label: "Runtime lint judge", desc: "Validates bundle ref shape (apply/rollback/verify)" },
  ];

  return (
    <div data-panel-id="validate_dispatch" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        PassthroughStub · parallel fan-out · cve_rem.validate_dispatch
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: "rgba(122,182,255,.10)", border: "1px solid rgba(122,182,255,.4)",
        display: "flex", alignItems: "center", gap: 16,
      }}>
        <div style={{ fontSize: 26, color: "var(--info)", lineHeight: 1 }}>⇉</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: "var(--info)" }}>PARALLEL DISPATCH</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>Fans out to 2 judges concurrently; joins at validate_plan_join</div>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {targets.map((t, i) => (
          <div
            key={i}
            onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: t.id } }))}
            style={{
              cursor: "pointer", padding: "10px 14px", borderRadius: 6,
              background: "var(--bg-3)", border: "1px solid var(--line-1)",
              display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 12, alignItems: "center",
            }}
          >
            <span className="mono" style={{ background: "rgba(122,182,255,.12)", color: "var(--info)", fontSize: 11, padding: "3px 10px", borderRadius: 3, fontWeight: 700 }}>→ {t.id}</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              <span style={{ fontSize: 12.5, color: "var(--fg-0)", fontWeight: 500 }}>{t.label}</span>
              <span style={{ fontSize: 11, color: "var(--fg-3)" }}>{t.desc}</span>
            </div>
            <span style={{ color: "var(--info)", fontSize: 14 }}>↗</span>
          </div>
        ))}
      </div>

      <div
        onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "validate_plan_join" } }))}
        style={{ background: "var(--bg-3)", border: "1px dashed var(--line-2)", borderRadius: 6, padding: "10px 14px", cursor: "pointer", fontSize: 12, color: "var(--fg-2)" }}
      >
        <span style={{ color: "var(--fg-3)" }}>Join target: </span>
        <span className="mono" style={{ color: "var(--info)", fontWeight: 600 }}>validate_plan_join ↗</span>
        <span style={{ color: "var(--fg-3)" }}> — converges judges into validation_passed (AND)</span>
      </div>
    </div>
  );
}

// ─── JudgeSafetyPanel ──────────────────────────────────────────────────

function JudgeSafetyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="llm" lifecycle={lifecycle} panelId="judge_safety" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const verdict = allState.judge_safety_verdict || runState.judge_safety_verdict || "";
  const criticVerdict = allState.critic_verdict || runState.critic_verdict || "";
  const influenced = !!(allState.untrusted_text_influenced || runState.untrusted_text_influenced);
  const hitlGates = allState.hitl_gates || runState.hitl_gates || {};
  const ingestDecision = (hitlGates.ingest || {}).decision || "";
  const planDecision = (hitlGates.plan || {}).decision || "";
  const elapsedMs = timing?.elapsed_ms;

  const passed = verdict === "pass";
  const vi = passed
    ? { color: "var(--ok)",  bg: "var(--ok-dim)",          icon: "✓", label: "PASS" }
    : { color: "var(--err)", bg: "rgba(239,106,106,.12)",  icon: "✕", label: "FAIL" };

  // Compute which sub-checks passed
  const criticOk = criticVerdict === "approved" || (criticVerdict === "feedback" && planDecision === "approve");
  const influencedOk = !influenced || ingestDecision === "approve";

  return (
    <div data-panel-id="judge_safety" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Code-safety judge · Fathom + watermark · cve_rem.judge_safety
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: vi.bg, border: "1px solid " + vi.color + "55",
        display: "flex", alignItems: "center", gap: 16,
      }}>
        <div style={{ fontSize: 28, color: vi.color, fontWeight: 700, lineHeight: 1 }}>{vi.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: vi.color }}>{vi.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
            {passed ? "Bundle cleared safety + watermark checks" : "Bundle blocked — see sub-checks below"}
          </div>
        </div>
      </div>

      {/* Sub-checks */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <div style={{
          padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
          letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
        }}>verdict reasoning</div>
        <div style={{ padding: 4 }}>
          {/* Critic check */}
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--line-1)", fontSize: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
              <span style={{ color: criticOk ? "var(--ok)" : "var(--err)", fontSize: 14 }}>{criticOk ? "✓" : "✕"}</span>
              <span style={{ color: "var(--fg-0)", fontWeight: 600 }}>Critic verdict</span>
              <span
                onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "critic" } }))}
                className="mono"
                style={{ marginLeft: "auto", color: "var(--info)", cursor: "pointer", fontSize: 11 }}
              >critic ↗</span>
            </div>
            <div style={{ paddingLeft: 24, color: "var(--fg-3)", fontSize: 11.5 }}>
              {criticVerdict ? (
                <>verdict: <span className="mono" style={{ color: criticOk ? "var(--ok)" : "var(--warn)" }}>{criticVerdict}</span>
                {criticVerdict === "feedback" && planDecision === "approve" && <span style={{ color: "var(--fg-2)" }}> · overridden by HITL plan approval</span>}</>
              ) : "no verdict recorded"}
            </div>
          </div>

          {/* Influenced check */}
          <div style={{ padding: "8px 12px", fontSize: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
              <span style={{ color: influencedOk ? "var(--ok)" : "var(--err)", fontSize: 14 }}>{influencedOk ? "✓" : "✕"}</span>
              <span style={{ color: "var(--fg-0)", fontWeight: 600 }}>Untrusted-text taint</span>
              <span
                onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "source_trust_audit" } }))}
                className="mono"
                style={{ marginLeft: "auto", color: "var(--info)", cursor: "pointer", fontSize: 11 }}
              >source_trust_audit ↗</span>
            </div>
            <div style={{ paddingLeft: 24, color: "var(--fg-3)", fontSize: 11.5 }}>
              {influenced ? (
                <>untrusted text reached extract: <span className="mono" style={{ color: "var(--warn)" }}>true</span>
                {ingestDecision === "approve" && <span style={{ color: "var(--fg-2)" }}> · cleared by HITL ingest approval</span>}</>
              ) : <>no taint: <span className="mono" style={{ color: "var(--ok)" }}>clean</span></>}
            </div>
          </div>
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Heuristic stand-in for Fathom code-safety. Production hits Fathom rules engine + krakntrust watermark API.
      </div>
    </div>
  );
}

// ─── JudgeLintPanel ────────────────────────────────────────────────────

function JudgeLintPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="llm" lifecycle={lifecycle} panelId="judge_lint" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const verdict = allState.judge_lint_verdict || runState.judge_lint_verdict || "";
  const bundle = allState.bundle || runState.bundle || {};
  const runtime = bundle.runtime || allState.code_runtime || "";
  const applyRef = bundle.apply_bundle_ref || "";
  const rollbackRef = bundle.rollback_bundle_ref || "";
  const verifyRef = bundle.verify_probe_ref || "";
  const elapsedMs = timing?.elapsed_ms;

  const applyOk = applyRef.startsWith("bundle://") || applyRef.startsWith("file://");
  const rollbackOk = rollbackRef.startsWith("bundle://") || rollbackRef.startsWith("file://");
  const verifyOk = verifyRef.startsWith("probe://") || verifyRef.startsWith("file://");

  const passed = verdict === "pass";
  const vi = passed
    ? { color: "var(--ok)",  bg: "var(--ok-dim)",          icon: "✓", label: "PASS" }
    : { color: "var(--err)", bg: "rgba(239,106,106,.12)",  icon: "✕", label: "FAIL" };

  const slots = [
    { name: "apply",    ref: applyRef,    ok: applyOk,    accept: "bundle:// or file://" },
    { name: "rollback", ref: rollbackRef, ok: rollbackOk, accept: "bundle:// or file://" },
    { name: "verify",   ref: verifyRef,   ok: verifyOk,   accept: "probe:// or file://" },
  ];

  return (
    <div data-panel-id="judge_lint" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Runtime lint judge · cve_rem.judge_lint
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: vi.bg, border: "1px solid " + vi.color + "55",
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexWrap: "wrap",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
          <div style={{ fontSize: 28, color: vi.color, fontWeight: 700, lineHeight: 1 }}>{vi.icon}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: vi.color }}>{vi.label}</div>
            <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
              {passed ? "All bundle refs use valid URI schemes" : "One or more refs missing or wrong scheme"}
            </div>
          </div>
        </div>
        {runtime && <span style={{ background: "var(--accent-dim)", color: "var(--accent)", fontSize: 11, padding: "3px 10px", borderRadius: 3, fontWeight: 600 }}>{runtime}</span>}
      </div>

      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <div style={{
          padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
          letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          display: "flex", justifyContent: "space-between",
        }}>
          <span>bundle ref shape · {slots.filter(s => s.ok).length}/3 valid</span>
          <span
            onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "code_writer" } }))}
            className="mono"
            style={{ textTransform: "none", letterSpacing: 0, color: "var(--info)", cursor: "pointer" }}
          >code_writer ↗</span>
        </div>
        <div style={{ padding: 4 }}>
          {slots.map((s, i) => (
            <div key={s.name} style={{
              padding: "8px 12px", borderBottom: i < slots.length - 1 ? "1px solid var(--line-1)" : "none",
              display: "grid", gridTemplateColumns: "70px auto 1fr auto", gap: 12, alignItems: "center", fontSize: 12,
            }}>
              <span style={{
                fontSize: 10, fontWeight: 700, padding: "2px 0", borderRadius: 3, textAlign: "center", letterSpacing: ".04em",
                background: s.ok ? "var(--ok-dim)" : "rgba(239,106,106,.12)",
                color: s.ok ? "var(--ok)" : "var(--err)",
              }}>{s.name.toUpperCase()}</span>
              <span style={{ color: s.ok ? "var(--ok)" : "var(--err)", fontSize: 14 }}>{s.ok ? "✓" : "✕"}</span>
              <span className="mono" style={{ color: s.ref ? "var(--fg-0)" : "var(--fg-3)", fontSize: 11, wordBreak: "break-all" }}>
                {s.ref || <span style={{ fontStyle: "italic" }}>empty</span>}
              </span>
              <span className="mono muted" style={{ fontSize: 10.5 }}>{s.accept}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Heuristic stand-in for ansible-lint / kubeval / tflint / Batfish. Validates URI scheme only — production runs the real linters per runtime.
      </div>
    </div>
  );
}

// ─── SandboxSkipPanel ──────────────────────────────────────────────────

function SandboxSkipPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="sandbox" lifecycle={lifecycle} panelId="sandbox_skip" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const skipSandbox = !!(allState.skip_sandbox || runState.skip_sandbox);
  const sandboxStatus = allState.sandbox_status || runState.sandbox_status || "";
  const sandboxRuntime = allState.sandbox_runtime || runState.sandbox_runtime || "";
  const extract = allState.extract || runState.extract || {};
  const vulnClass = extract.vuln_class || allState.vuln_class || "";
  const forceHitl = !!(allState.force_hitl ?? runState.force_hitl);
  const elapsedMs = timing?.elapsed_ms;

  const wasSkipped = skipSandbox && sandboxStatus === "skipped";
  const guarded = !skipSandbox && sandboxStatus && sandboxStatus !== "skipped";

  const vi = wasSkipped
    ? { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "⊘", label: "SANDBOX BYPASSED",   verdict: "Runtime classified as skip — production verify only" }
    : guarded
    ? { color: "var(--ok)",   bg: "var(--ok-dim)",         icon: "✓", label: "PROBE RESULTS PRESERVED", verdict: "Sandbox already ran; this leaf preserved real probe results" }
    : { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: "NO-OP PASSTHROUGH",       verdict: "Reached on serial wiring; no signal modified" };

  return (
    <div data-panel-id="sandbox_skip" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Sandbox skip leaf · cve_rem.sandbox_skip · serial bypass
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "16px 20px", borderRadius: 10, background: vi.bg, border: "1px solid " + vi.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 44, height: 44, borderRadius: 22, background: vi.color + "22", border: "1px solid " + vi.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, color: vi.color, fontWeight: 700 }}>{vi.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: vi.color }}>{vi.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{vi.verdict}</div>
        </div>
        {sandboxStatus && <span style={{ background: vi.color + "22", color: vi.color, fontSize: 10.5, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>{sandboxStatus.toUpperCase()}</span>}
      </div>

      {/* State flags — visual diff */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        <div style={{ padding: "11px 12px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>sandbox_runtime</div>
          <div className="mono" style={{ fontSize: 12, color: "var(--accent)", fontWeight: 600, marginTop: 3 }}>{sandboxRuntime || "—"}</div>
          {vulnClass && <div style={{ fontSize: 10, color: "var(--fg-3)", marginTop: 2 }}>vuln_class: <span className="mono" style={{ color: "var(--fg-1)" }}>{vulnClass}</span></div>}
        </div>
        <div style={{ padding: "11px 12px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>skip_sandbox</div>
          <div className="mono" style={{ fontSize: 12, color: skipSandbox ? "var(--warn)" : "var(--fg-2)", fontWeight: 600, marginTop: 3 }}>{String(skipSandbox)}</div>
        </div>
        <div style={{ padding: "11px 12px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>force_hitl</div>
          <div className="mono" style={{ fontSize: 12, color: forceHitl ? "var(--warn)" : "var(--fg-2)", fontWeight: 600, marginTop: 3 }}>{String(forceHitl)}</div>
        </div>
      </div>

      {/* Routing arm taken */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>downstream route</div>
        <div style={{ padding: "14px 16px", display: "flex", alignItems: "center", justifyContent: "center", gap: 10, flexWrap: "wrap" }}>
          <span className="mono" style={{ fontSize: 11, color: vi.color, padding: "5px 10px", border: "1px solid " + vi.color + "55", borderRadius: 16, background: vi.color + "11" }}>sandbox_skip</span>
          <span style={{ color: vi.color, fontSize: 14, fontFamily: "var(--mono)" }}>→</span>
          <span className="mono" onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "create_change_request" } }))} style={{ fontSize: 11, color: "var(--info)", padding: "5px 10px", border: "1px solid var(--info)55", borderRadius: 16, background: "var(--info)11", cursor: "pointer" }}>create_change_request ↗</span>
        </div>
      </div>

      {wasSkipped && (
        <div style={{ padding: "12px 14px", background: "rgba(245,181,74,.06)", border: "1px solid rgba(245,181,74,.28)", borderRadius: 6, fontSize: 11.5, color: "var(--fg-1)", lineHeight: 1.5 }}>
          <div style={{ color: "var(--warn)", fontWeight: 600, marginBottom: 6, fontSize: 11, textTransform: "uppercase", letterSpacing: ".06em" }}>downstream consequences</div>
          Skip path sets <span className="mono" style={{ color: "var(--warn)" }}>force_hitl=true</span> — CR will route to <span
            onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "hitl_change_approval" } }))}
            className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
          >hitl_change_approval ↗</span> instead of auto-apply. Compensating controls (NIST + CAPEC) surface in <span
            onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "framework_mapping" } }))}
            className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
          >framework_mapping ↗</span> doctrine refs attached to the CR.
        </div>
      )}
    </div>
  );
}

// ─── SandboxDispatchPanel — runtime router ──────────────────────────────

function SandboxDispatchPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="decision" lifecycle={lifecycle} panelId="sandbox_dispatch" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const runtime = allState.sandbox_runtime || runState.sandbox_runtime || "";
  const extract = allState.extract || runState.extract || {};
  const vulnClass = extract.vuln_class || allState.vuln_class || "";
  const elapsedMs = timing?.elapsed_ms;

  const ROUTES = [
    { runtime: "skip",             target: "sandbox_skip",   icon: "⊘", color: "var(--warn)", desc: "Static / read-only — no apply harness" },
    { runtime: "cargonet_lab",     target: "sandbox_run",    icon: "🧪", color: "var(--info)", desc: "CargoNet lab — fully revertible apply" },
    { runtime: "docker_compose",   target: "sandbox_run",    icon: "🐳", color: "var(--info)", desc: "Compose project — isolated containers" },
    { runtime: "static_detection", target: "sandbox_run",    icon: "👁", color: "var(--info)", desc: "Read-only probe — no state change" },
  ];
  const taken = ROUTES.find(r => r.runtime === runtime);
  const target = taken?.target || "sandbox_skip";
  const targetColor = target === "sandbox_skip" ? "var(--warn)" : "var(--info)";

  return (
    <div data-panel-id="sandbox_dispatch" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Sandbox runtime router · cve_rem.sandbox_dispatch · Fathom rule pack
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "16px 20px", borderRadius: 10, background: targetColor + "11", border: "1px solid " + targetColor + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 44, height: 44, borderRadius: 22, background: targetColor + "22", border: "1px solid " + targetColor + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20 }}>{taken?.icon || "?"}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: targetColor }}>RUNTIME SELECTED · {(runtime || "—").toUpperCase()}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{taken?.desc || "No runtime selected — falling through to skip"}</div>
        </div>
        {vulnClass && <span className="mono" style={{ background: "var(--accent)22", color: "var(--accent)", fontSize: 10.5, padding: "4px 10px", borderRadius: 12, fontWeight: 600, whiteSpace: "nowrap" }}>{vulnClass}</span>}
      </div>

      {/* Decision tree */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>routing table</div>
        <div style={{ padding: 6 }}>
          {ROUTES.map((r, i) => {
            const isActive = r.runtime === runtime;
            return (
              <div key={r.runtime} style={{
                display: "grid", gridTemplateColumns: "28px 130px 1fr 24px 140px", gap: 12,
                padding: "10px 12px", borderBottom: i < ROUTES.length - 1 ? "1px solid var(--line-1)" : "none",
                alignItems: "center",
                background: isActive ? r.color + "11" : "transparent",
                borderLeft: "3px solid " + (isActive ? r.color : "transparent"),
                borderRadius: isActive ? 4 : 0,
              }}>
                <span style={{ fontSize: 16, textAlign: "center", opacity: isActive ? 1 : 0.4 }}>{r.icon}</span>
                <span className="mono" style={{ fontSize: 11.5, color: isActive ? r.color : "var(--fg-2)", fontWeight: isActive ? 700 : 400 }}>{r.runtime}</span>
                <span style={{ fontSize: 10.5, color: isActive ? "var(--fg-1)" : "var(--fg-3)" }}>{r.desc}</span>
                <span style={{ color: isActive ? r.color : "var(--fg-3)", fontFamily: "var(--mono)", fontSize: 13, opacity: isActive ? 1 : 0.3 }}>→</span>
                <span
                  onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: r.target } }))}
                  className="mono"
                  style={{ fontSize: 11, color: isActive ? "var(--info)" : "var(--fg-3)", cursor: "pointer", fontWeight: isActive ? 600 : 400 }}
                >{r.target} {isActive ? "↗" : ""}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic", padding: "10px 13px", background: "rgba(122,162,247,.05)", border: "1px solid rgba(122,162,247,.18)", borderRadius: 6 }}>
        Routing rule: <span className="mono" style={{ color: "var(--accent)" }}>r-sandbox-skip</span> when <span className="mono">sandbox_runtime=skip</span>, else <span className="mono" style={{ color: "var(--accent)" }}>r-sandbox-run</span>. Driven by <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "extract_trusted" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer", fontStyle: "normal" }}
        >vuln_class ↗</span> classification.
      </div>
    </div>
  );
}

// ─── EmitSandboxEvidencePanel ──────────────────────────────────────────

function EmitSandboxEvidencePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="artifact" lifecycle={lifecycle} panelId="emit_sandbox_evidence" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const ref = allState.sandbox_evidence_artifact_ref || runState.sandbox_evidence_artifact_ref || "";
  const sandbox = allState.sandbox || runState.sandbox || {};
  const sandboxStatus = allState.sandbox_status || runState.sandbox_status || "";
  const sandboxRuntime = allState.sandbox_runtime || runState.sandbox_runtime || "";
  const probeLatency = allState.sandbox_probe_latency_ms || runState.sandbox_probe_latency_ms;
  const elapsedMs = timing?.elapsed_ms;

  const digestMatch = ref.match(/([a-f0-9]{64})\.json$/);
  const digest = digestMatch ? digestMatch[1] : "";
  const digestShort = digest ? digest.slice(0, 16) : "";

  return (
    <div data-panel-id="emit_sandbox_evidence" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Content-addressed write · cve_rem.emit_sandbox_evidence
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{
        padding: "14px 18px", borderRadius: 8,
        background: ref ? "var(--ok-dim)" : "rgba(255,255,255,.04)",
        border: "1px solid " + (ref ? "var(--ok)" : "var(--line-2)") + "55",
        display: "flex", alignItems: "center", gap: 16,
      }}>
        <div style={{ fontSize: 26, color: ref ? "var(--ok)" : "var(--fg-3)", fontWeight: 700, lineHeight: 1 }}>
          {ref ? "✓" : "·"}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: ref ? "var(--ok)" : "var(--fg-2)" }}>
            {ref ? "EVIDENCE PERSISTED" : "NO ARTIFACT"}
          </div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
            {ref ? "Sandbox probe trace written, content-addressed by blake3 of canonical JSON" : "Sandbox produced no probe payload"}
          </div>
        </div>
        {digestShort && (
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 12, color: "var(--fg-0)", fontWeight: 700 }}>{digestShort}…</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>blake3</div>
          </div>
        )}
      </div>

      {/* Sandbox summary */}
      {(sandboxStatus || sandboxRuntime || probeLatency != null) && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
            display: "flex", justifyContent: "space-between",
          }}>
            <span>sandbox summary</span>
            <span
              onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "sandbox_run" } }))}
              className="mono"
              style={{ textTransform: "none", letterSpacing: 0, color: "var(--info)", cursor: "pointer" }}
            >sandbox_run ↗</span>
          </div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
            {sandboxRuntime && (<><span style={{ color: "var(--fg-3)" }}>runtime</span><span className="mono" style={{ color: "var(--fg-0)" }}>{sandboxRuntime}</span></>)}
            {sandboxStatus && (<><span style={{ color: "var(--fg-3)" }}>status</span>
              <span className="mono" style={{ color: sandboxStatus === "ok" || sandboxStatus === "clean" ? "var(--ok)" : sandboxStatus === "fail" ? "var(--err)" : "var(--warn)" }}>{sandboxStatus}</span>
            </>)}
            {probeLatency != null && (<><span style={{ color: "var(--fg-3)" }}>probe latency</span><span className="mono" style={{ color: "var(--fg-0)" }}>{probeLatency}ms</span></>)}
          </div>
        </div>
      )}

      {/* Artifact ref */}
      {ref && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          }}>artifact ref</div>
          <div style={{ padding: 12, fontSize: 11.5, color: "var(--fg-0)", wordBreak: "break-all" }} className="mono">{ref}</div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Content-addressed: identical sandbox runs dedupe to same digest. Probe trace becomes durable evidence for the change request + retro audit chain.
      </div>
    </div>
  );
}

// ─── VerifyImmediatePanel ───────────────────────────────────────────────

function VerifyImmediatePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="verify_immediate" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const probeMethod = allState.verify_probe_method || runState.verify_probe_method || "";
  const divergence = !!(allState.sandbox_prod_divergence ?? runState.sandbox_prod_divergence);
  const driftWindow = allState.drift_watch_window_hours ?? runState.drift_watch_window_hours;
  const sandboxStatus = allState.sandbox_status || runState.sandbox_status || "";
  const sandboxRuntime = allState.sandbox_runtime || runState.sandbox_runtime || "";
  const fleetPassed = !!(allState.fleet_passed ?? runState.fleet_passed);
  const mitigationOnly = !!(allState.mitigation_only ?? runState.mitigation_only);
  const tier = allState.ssvc_decision || runState.ssvc_decision || "";
  const elapsedMs = timing?.elapsed_ms;

  const OUTCOME_INFO = {
    patched:             { color: "var(--ok)",   bg: "var(--ok-dim)",         icon: "✓", label: "PATCHED" },
    vulnerable:          { color: "var(--err)",  bg: "rgba(239,106,106,.12)", icon: "✗", label: "STILL VULNERABLE" },
    divergence:          { color: "var(--err)",  bg: "rgba(239,106,106,.12)", icon: "⚠", label: "SANDBOX⇄PROD DIVERGENCE" },
    mitigation_applied:  { color: "var(--info)", bg: "rgba(122,162,247,.12)", icon: "◐", label: "MITIGATION APPLIED" },
    waiting_on_operator: { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "⏸", label: "WAITING ON OPERATOR" },
  };
  const oi = OUTCOME_INFO[verifyOutcome] || { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: (verifyOutcome || "—").toUpperCase() };

  const PROBE_INFO = {
    none:              { icon: "·", desc: "No probe — sandbox apply already verified" },
    cargonet_diff:     { icon: "🧪", desc: "CargoNet sandbox vs prod diff" },
    pip_show:          { icon: "🐍", desc: "Per-host pip show probe — installed version check" },
    apt_show:          { icon: "📦", desc: "Per-host apt show probe — installed package version" },
    helm_get:          { icon: "⎈", desc: "Helm release values diff" },
    batfish:           { icon: "🐡", desc: "Batfish network config diff" },
    vendor_cli_show:   { icon: "💻", desc: "Vendor CLI show command parse" },
    mitigation_audit:  { icon: "📝", desc: "Mitigation evidence audit (CWE controls)" },
  };
  const pi = PROBE_INFO[probeMethod] || { icon: "·", desc: probeMethod || "Probe method unset" };

  // Routing arms
  const ROUTES = [
    { cond: verifyOutcome === "patched" && !divergence, target: "drift_watch_spawn",    label: "patched + no divergence",     color: "var(--ok)"   },
    { cond: divergence,                                 target: "divergence_quarantine", label: "sandbox⇄prod divergence",    color: "var(--err)"  },
    { cond: verifyOutcome === "vulnerable" || verifyOutcome === "mitigation_applied" || verifyOutcome === "waiting_on_operator", target: "write_retrospective", label: "non-patched terminal", color: "var(--warn)" },
  ];
  const takenRoute = ROUTES.find(r => r.cond);

  return (
    <div data-panel-id="verify_immediate" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Verify probe · cve_rem.verify_immediate · runtime-appropriate
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "16px 20px", borderRadius: 10, background: oi.bg, border: "1px solid " + oi.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 44, height: 44, borderRadius: 22, background: oi.color + "22", border: "1px solid " + oi.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, color: oi.color, fontWeight: 700 }}>{oi.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: oi.color }}>VERIFY OUTCOME · {oi.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
            {divergence
              ? "Sandbox said OK but prod probe failed — plan_hash will be quarantined"
              : verifyOutcome === "patched"
              ? "Production probe confirms apply succeeded; drift watcher will guard"
              : "Verify did not confirm patched state"}
          </div>
        </div>
        {fleetPassed && <span style={{ background: "var(--ok)22", color: "var(--ok)", fontSize: 10.5, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>FLEET PASS</span>}
      </div>

      {/* Probe + signals */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div style={{ padding: "12px 14px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
            <span style={{ fontSize: 18 }}>{pi.icon}</span>
            <div>
              <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>probe method</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--accent)", fontWeight: 600 }}>{probeMethod || "—"}</div>
            </div>
          </div>
          <div style={{ fontSize: 11, color: "var(--fg-2)" }}>{pi.desc}</div>
        </div>
        <div style={{ padding: "12px 14px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>signals</div>
          <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "3px 10px", fontSize: 11 }}>
            <span style={{ color: "var(--fg-3)" }}>fleet_passed</span><span className="mono" style={{ color: fleetPassed ? "var(--ok)" : "var(--fg-2)" }}>{String(fleetPassed)}</span>
            <span style={{ color: "var(--fg-3)" }}>divergence</span><span className="mono" style={{ color: divergence ? "var(--err)" : "var(--ok)" }}>{String(divergence)}</span>
            <span style={{ color: "var(--fg-3)" }}>mitigation_only</span><span className="mono" style={{ color: mitigationOnly ? "var(--info)" : "var(--fg-2)" }}>{String(mitigationOnly)}</span>
            {sandboxStatus && (<><span style={{ color: "var(--fg-3)" }}>sandbox</span><span className="mono" style={{ color: "var(--fg-1)" }}>{sandboxStatus} · {sandboxRuntime}</span></>)}
          </div>
        </div>
      </div>

      {/* Drift window */}
      {driftWindow != null && (
        <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>drift_watch_window</div>
            <div style={{ fontSize: 11, color: "var(--fg-2)", marginTop: 2 }}>tier <span className="mono" style={{ color: "var(--fg-1)" }}>{tier || "—"}</span> gets {driftWindow}h post-apply observation</div>
          </div>
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 22, color: driftWindow > 0 ? "var(--accent)" : "var(--fg-3)", fontWeight: 700, lineHeight: 1 }}>{driftWindow}h</div>
          </div>
        </div>
      )}

      {/* Routing arms */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>downstream arms</div>
        <div style={{ padding: 6 }}>
          {ROUTES.map((r, i) => {
            const isActive = r === takenRoute;
            return (
              <div key={r.target} style={{
                display: "grid", gridTemplateColumns: "1fr 24px 160px", gap: 10, padding: "9px 12px",
                borderBottom: i < ROUTES.length - 1 ? "1px solid var(--line-1)" : "none",
                background: isActive ? r.color + "11" : "transparent",
                borderLeft: "3px solid " + (isActive ? r.color : "transparent"),
                borderRadius: isActive ? 4 : 0, alignItems: "center",
              }}>
                <span style={{ fontSize: 11, color: isActive ? r.color : "var(--fg-3)", fontWeight: isActive ? 600 : 400 }}>{r.label}</span>
                <span style={{ color: isActive ? r.color : "var(--fg-3)", fontFamily: "var(--mono)", fontSize: 13, opacity: isActive ? 1 : 0.3 }}>→</span>
                <span
                  onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: r.target } }))}
                  className="mono"
                  style={{ fontSize: 11, color: isActive ? "var(--info)" : "var(--fg-3)", cursor: "pointer", fontWeight: isActive ? 600 : 400 }}
                >{r.target} {isActive ? "↗" : ""}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ─── ProgressiveExecutePanel ────────────────────────────────────────────

function ProgressiveExecutePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="progressive_execute" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const elapsedMs = timing?.elapsed_ms;

  const canary = !!(allState.canary_passed ?? runState.canary_passed);
  const stage = !!(allState.stage_passed ?? runState.stage_passed);
  const fleet = !!(allState.fleet_passed ?? runState.fleet_passed);
  const rollback = !!(allState.rollback_triggered ?? runState.rollback_triggered);
  const ledger = allState.execution_ledger || runState.execution_ledger || [];
  const apply = allState.per_host_apply_results || runState.per_host_apply_results || [];
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const probeMethod = allState.verify_probe_method || runState.verify_probe_method || "";
  const haltReason = allState.halt_reason || runState.halt_reason || "";
  const crStatus = allState.cr_status || runState.cr_status || "";
  const lifecycleStates = allState.cr_lifecycle_states || runState.cr_lifecycle_states || [];
  const mitigationOnly = !!(allState.mitigation_only ?? runState.mitigation_only);
  const mitigationProbePassed = !!(allState.mitigation_probe_passed ?? runState.mitigation_probe_passed);
  const oncallPaged = !!(allState.oncall_paged ?? runState.oncall_paged);
  const cveId = allState.cve_id || runState.cve_id || "";
  const bundleRef = (allState.bundle && allState.bundle.apply_bundle_ref) || (runState.bundle && runState.bundle.apply_bundle_ref) || "";
  const bundleDigest = (bundleRef.match(/([a-f0-9]{64})/) || [])[1] || "";
  const crSysId = (allState.servicenow_response || runState.servicenow_response || {})?.result?.sys_id || "";

  // Reachable + verify totals
  const reachable = apply.filter(r => !r.unreachable);
  const unreachable = apply.filter(r => r.unreachable);
  const successHosts = reachable.filter(r => r.ok).length;
  const failedHosts = reachable.filter(r => !r.ok).length;
  const totalTasksRun = apply.reduce((s, r) => s + (r.tasks_run || 0), 0);
  const totalTasksSkipped = apply.reduce((s, r) => s + (r.tasks_skipped || 0), 0);
  const totalVerifyRun = apply.reduce((s, r) => s + (r.verify_tasks_run || 0), 0);
  const totalVerifyPassed = apply.reduce((s, r) => s + (r.verify_tasks_passed || 0), 0);

  // Verdict
  const VERDICT = (() => {
    if (oncallPaged) return { color: "var(--err)", bg: "rgba(239,106,106,.12)", icon: "⚠", label: "HALT — ON-CALL PAGED", desc: haltReason || "Quarantine — rollout did not run" };
    if (mitigationOnly) return { color: mitigationProbePassed ? "var(--info)" : "var(--warn)", bg: mitigationProbePassed ? "rgba(122,162,247,.10)" : "rgba(245,181,74,.12)", icon: "⛨", label: "MITIGATION-ONLY", desc: "No upstream patch; mitigation guidance recorded" };
    if (rollback) return { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "↶", label: "ROLLBACK TRIGGERED", desc: haltReason || "Apply failed mid-flight; preserved ledger" };
    if (fleet) return { color: "var(--ok)", bg: "var(--ok-dim)", icon: "✓", label: "FLEET PATCHED", desc: `${successHosts}/${reachable.length} reachable host(s) passed verify` };
    if (haltReason) return { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "⊘", label: "HALTED", desc: haltReason };
    return { color: "var(--info)", bg: "rgba(122,162,247,.10)", icon: "⫶", label: "IN PROGRESS", desc: "Rollout pending" };
  })();

  // Phase stepper
  const phases = [
    { key: "canary", label: "Canary", icon: "🐤", passed: canary,
      desc: "Single high-confidence host. Bundle ran end-to-end with verify-tagged tasks succeeding."},
    { key: "stage", label: "Stage", icon: "🧪", passed: stage,
      desc: "Pre-prod fleet. Same bundle, broader blast radius, all verify tasks green."},
    { key: "fleet", label: "Fleet", icon: "🌐", passed: fleet,
      desc: "Full prod rollout. All reachable hosts patched; unreachable hosts excluded."},
  ];

  // Ledger chips: canary:bundle / stage:bundle / fleet:bundle / halted:X / suppressed:X
  const ledgerChips = ledger.filter(e => typeof e === "string");

  // CR lifecycle stepper
  const CR_STAGES = ["draft", "approved", "implement", "verified", "closed"];
  const reachedIdx = (() => {
    let i = -1;
    for (let j = 0; j < CR_STAGES.length; j++) {
      if (lifecycleStates.includes(CR_STAGES[j])) i = j;
    }
    return i;
  })();
  const crTerminal = crStatus === "implemented" ? { color: "var(--ok)", label: "implemented" }
                   : crStatus === "rejected"    ? { color: "var(--err)", label: "rejected" }
                   : crStatus === "cancelled"   ? { color: "var(--fg-3)", label: "cancelled" }
                   : crStatus === "awaiting_hitl" ? { color: "var(--warn)", label: "awaiting HITL" }
                   : { color: "var(--fg-3)", label: crStatus || "draft" };

  // Task drilldown
  const [openHost, setOpenHost] = React.useState(null);

  return (
    <div data-panel-id="progressive_execute" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        cve_rem.progressive_execute · canary → stage → fleet rollout · bundle-driven apply via cargonet_exec
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* Hero verdict */}
      <div style={{ padding: "16px 20px", borderRadius: 10, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 48, height: 48, borderRadius: 24, background: VERDICT.color + "22", border: "1px solid " + VERDICT.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{VERDICT.desc}</div>
        </div>
        {verifyOutcome && (
          <span style={{ background: (verifyOutcome === "patched" ? "var(--ok)" : verifyOutcome === "mitigation_applied" ? "var(--info)" : "var(--warn)") + "22", color: verifyOutcome === "patched" ? "var(--ok)" : verifyOutcome === "mitigation_applied" ? "var(--info)" : "var(--warn)", fontSize: 10.5, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>
            verify: {verifyOutcome}
          </span>
        )}
      </div>

      {/* Phase stepper */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>rollout phases</div>
        <div style={{ padding: "16px", display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, position: "relative" }}>
          {phases.map((p, idx) => {
            const isLast = idx === phases.length - 1;
            const nextPassed = !isLast && phases[idx + 1].passed;
            const color = rollback && !p.passed ? "var(--warn)" : p.passed ? "var(--ok)" : "var(--fg-3)";
            return (
              <React.Fragment key={p.key}>
                <div style={{
                  padding: "12px 14px", borderRadius: 8,
                  background: p.passed ? "var(--ok-dim)" : rollback ? "rgba(245,181,74,.08)" : "rgba(122,162,247,.04)",
                  border: "1px solid " + color + "44",
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 5, position: "relative",
                }}>
                  {!isLast && (
                    <div style={{ position: "absolute", top: "50%", right: -10, width: 10, height: 1, background: nextPassed ? "var(--ok)" : "var(--fg-3)" }} />
                  )}
                  <div style={{ fontSize: 22 }}>{p.icon}</div>
                  <div style={{ fontSize: 11.5, color, fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" }}>
                    {p.passed ? "✓" : rollback ? "✗" : "○"} {p.label}
                  </div>
                  <div style={{ fontSize: 10, color: "var(--fg-2)", textAlign: "center", lineHeight: 1.4 }}>{p.desc}</div>
                </div>
              </React.Fragment>
            );
          })}
        </div>
      </div>

      {/* Counts strip */}
      {apply.length > 0 && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr 1fr", gap: 10 }}>
          <div style={{ padding: "10px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
            <div className="mono" style={{ fontSize: 22, color: "var(--fg-0)", fontWeight: 700, lineHeight: 1 }}>{successHosts}<span style={{ fontSize: 13, color: "var(--fg-3)", fontWeight: 400 }}>/{reachable.length}</span></div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 4 }}>reachable hosts ok</div>
          </div>
          <div style={{ padding: "10px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
            <div className="mono" style={{ fontSize: 22, color: totalVerifyRun > 0 && totalVerifyPassed === totalVerifyRun ? "var(--ok)" : "var(--warn)", fontWeight: 700, lineHeight: 1 }}>{totalVerifyPassed}<span style={{ fontSize: 13, color: "var(--fg-3)", fontWeight: 400 }}>/{totalVerifyRun}</span></div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 4 }}>verify tasks passed</div>
          </div>
          <div style={{ padding: "10px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
            <div className="mono" style={{ fontSize: 22, color: "var(--fg-0)", fontWeight: 700, lineHeight: 1 }}>{totalTasksRun}</div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 4 }}>ansible tasks executed</div>
          </div>
          <div style={{ padding: "10px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
            <div className="mono" style={{ fontSize: 22, color: unreachable.length > 0 ? "var(--warn)" : "var(--fg-3)", fontWeight: 700, lineHeight: 1 }}>{unreachable.length}</div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 4 }}>unreachable (excluded)</div>
          </div>
        </div>
      )}

      {/* Per-host apply table */}
      {apply.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
          <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>per-host apply ({apply.length})</span>
            <span className="mono" style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "none", letterSpacing: 0 }}>click row to drill into tasks</span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(140px, 1.6fr) 80px 80px 80px 80px 50px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", padding: "7px 14px", borderBottom: "1px solid var(--line-1)", background: "rgba(255,255,255,.02)" }}>
            <span>host</span>
            <span style={{ textAlign: "right" }}>tasks</span>
            <span style={{ textAlign: "right" }}>verify</span>
            <span style={{ textAlign: "right" }}>latency</span>
            <span style={{ textAlign: "center" }}>status</span>
            <span></span>
          </div>
          {apply.map((r, i) => {
            const isOpen = openHost === i;
            const hostColor = r.unreachable ? "var(--fg-3)" : r.ok ? "var(--ok)" : "var(--err)";
            const statusLabel = r.unreachable ? "unreachable" : r.ok ? "✓ ok" : "✗ fail";
            return (
              <React.Fragment key={i}>
                <div
                  onClick={() => setOpenHost(isOpen ? null : i)}
                  style={{
                    display: "grid", gridTemplateColumns: "minmax(140px, 1.6fr) 80px 80px 80px 80px 50px",
                    padding: "9px 14px", borderBottom: isOpen ? "1px solid var(--line-1)" : i < apply.length - 1 ? "1px solid var(--line-1)" : "none",
                    fontSize: 11.5, alignItems: "center", cursor: "pointer",
                    background: isOpen ? "rgba(122,162,247,.06)" : "transparent",
                  }}
                >
                  <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{r.host || "—"}</span>
                  <span className="mono" style={{ color: "var(--fg-1)", textAlign: "right" }}>{r.tasks_run || 0}<span style={{ color: "var(--fg-3)" }}>/{(r.tasks_run || 0) + (r.tasks_skipped || 0)}</span></span>
                  <span className="mono" style={{ color: (r.verify_tasks_run > 0 && r.verify_tasks_passed === r.verify_tasks_run) ? "var(--ok)" : r.verify_tasks_run > 0 ? "var(--warn)" : "var(--fg-3)", textAlign: "right" }}>{r.verify_tasks_passed || 0}<span style={{ color: "var(--fg-3)" }}>/{r.verify_tasks_run || 0}</span></span>
                  <span className="mono" style={{ color: "var(--fg-2)", textAlign: "right", fontSize: 10.5 }}>{r.latency_ms != null ? r.latency_ms + "ms" : "—"}</span>
                  <span style={{ textAlign: "center" }}>
                    <span style={{ color: hostColor, fontSize: 10.5, padding: "2px 7px", borderRadius: 10, background: hostColor + "18", fontWeight: 700, letterSpacing: ".04em" }}>{statusLabel}</span>
                  </span>
                  <span style={{ textAlign: "center", color: "var(--fg-3)", fontSize: 12 }}>{isOpen ? "▾" : "▸"}</span>
                </div>
                {isOpen && (
                  <div style={{ padding: "10px 14px 14px 14px", borderBottom: i < apply.length - 1 ? "1px solid var(--line-1)" : "none", background: "rgba(0,0,0,.10)" }}>
                    {r.evidence && <div style={{ fontSize: 10.5, color: "var(--fg-3)", fontStyle: "italic", marginBottom: 8 }}>{r.evidence}</div>}
                    {r.error && <div style={{ fontSize: 10.5, color: "var(--err)", marginBottom: 8 }}>error: {r.error}</div>}
                    {Array.isArray(r.task_results) && r.task_results.length > 0 ? (
                      <div style={{ background: "rgba(0,0,0,.20)", border: "1px solid var(--line-1)", borderRadius: 4, overflow: "hidden" }}>
                        {r.task_results.slice(0, 12).map((t, ti) => {
                          const tcolor = t.skipped ? "var(--fg-3)" : t.ok ? "var(--ok)" : "var(--err)";
                          const ticon = t.skipped ? "○" : t.ok ? "✓" : "✗";
                          return (
                            <div key={ti} style={{ padding: "6px 10px", borderBottom: ti < Math.min(r.task_results.length, 12) - 1 ? "1px solid var(--line-1)" : "none", fontSize: 10.5, display: "grid", gridTemplateColumns: "16px 1fr auto", gap: 8, alignItems: "center" }}>
                              <span style={{ color: tcolor, fontWeight: 700, textAlign: "center" }}>{ticon}</span>
                              <span style={{ color: "var(--fg-1)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {t.is_verify && <span style={{ color: "var(--info)", fontWeight: 600, marginRight: 5, fontSize: 9, padding: "1px 5px", borderRadius: 7, background: "var(--info)22" }}>VERIFY</span>}
                                <span className="mono">{t.name || "—"}</span>
                              </span>
                              <span className="mono" style={{ color: "var(--fg-3)", fontSize: 10 }}>
                                {t.skipped ? "skipped" : t.exit_code != null ? "rc=" + t.exit_code : t.error ? "err" : ""}
                              </span>
                            </div>
                          );
                        })}
                        {r.task_results.length > 12 && (
                          <div style={{ padding: "6px 10px", fontSize: 10, color: "var(--fg-3)", borderTop: "1px solid var(--line-1)" }}>+{r.task_results.length - 12} more tasks</div>
                        )}
                      </div>
                    ) : (
                      <div style={{ fontSize: 10.5, color: "var(--fg-3)" }}>no task-level results</div>
                    )}
                  </div>
                )}
              </React.Fragment>
            );
          })}
        </div>
      )}

      {/* Execution ledger chips */}
      {ledgerChips.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, padding: "10px 14px" }}>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 6 }}>execution_ledger</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {ledgerChips.map((e, i) => {
              const isHalt = e.startsWith("halted:") || e.startsWith("suppressed:");
              const color = isHalt ? "var(--warn)" : "var(--ok)";
              return (
                <span key={i} className="mono" style={{ fontSize: 10.5, color, padding: "3px 9px", borderRadius: 10, background: color + "18", border: "1px solid " + color + "33" }}>{e}</span>
              );
            })}
          </div>
        </div>
      )}

      {/* CR lifecycle stepper + bundle ref */}
      <div style={{ display: "grid", gridTemplateColumns: bundleRef ? "1fr 280px" : "1fr", gap: 10 }}>
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, padding: "11px 14px" }}>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 8, display: "flex", justifyContent: "space-between" }}>
            <span>CR lifecycle</span>
            <span style={{ color: crTerminal.color, fontSize: 10, padding: "2px 8px", borderRadius: 10, background: crTerminal.color + "18", letterSpacing: ".04em", textTransform: "none" }}>{crTerminal.label}</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
            {CR_STAGES.map((s, i) => {
              const reached = i <= reachedIdx;
              const color = reached ? "var(--ok)" : "var(--fg-3)";
              return (
                <React.Fragment key={s}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, flex: 1 }}>
                    <div style={{ width: 18, height: 18, borderRadius: 9, background: color + "22", border: "1px solid " + color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, color, fontWeight: 700 }}>{reached ? "✓" : i + 1}</div>
                    <span className="mono" style={{ fontSize: 9.5, color, letterSpacing: ".04em" }}>{s}</span>
                  </div>
                  {i < CR_STAGES.length - 1 && <div style={{ height: 1, flex: 1, background: i < reachedIdx ? "var(--ok)" : "var(--fg-3)44", maxWidth: 30 }} />}
                </React.Fragment>
              );
            })}
          </div>
          {crSysId && <div style={{ fontSize: 10, color: "var(--fg-3)", marginTop: 8 }}>sys_id <span className="mono" style={{ color: "var(--fg-1)" }}>{crSysId}</span></div>}
        </div>
        {bundleRef && (
          <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, padding: "11px 14px" }}>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 6, display: "flex", justifyContent: "space-between" }}>
              <span>apply bundle</span>
              <a href={window.apiUrl("/watch/api/artifact?ref=" + encodeURIComponent(bundleRef))} download={`bundle_${cveId || "cve"}.yaml`} style={{ color: "var(--info)", fontSize: 10, textDecoration: "none", textTransform: "none", letterSpacing: 0 }}>download ↓</a>
            </div>
            <div className="mono" style={{ fontSize: 11, color: "var(--fg-0)", fontWeight: 600 }}>{bundleDigest ? bundleDigest.slice(0, 12) + "…" + bundleDigest.slice(-6) : "—"}</div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", marginTop: 3 }}>probe_method: <span className="mono" style={{ color: "var(--fg-1)" }}>{probeMethod || "—"}</span></div>
          </div>
        )}
      </div>

      {/* Halt reason callout if non-trivial */}
      {haltReason && !rollback && !fleet && (
        <div style={{ padding: "10px 13px", background: oncallPaged ? "rgba(239,106,106,.08)" : "rgba(245,181,74,.08)", border: "1px solid " + (oncallPaged ? "var(--err)" : "var(--warn)") + "44", borderRadius: 6, fontSize: 11.5 }}>
          <div style={{ color: oncallPaged ? "var(--err)" : "var(--warn)", fontWeight: 600, marginBottom: 3, fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".08em" }}>halt_reason</div>
          <div className="mono" style={{ color: "var(--fg-1)", fontSize: 11 }}>{haltReason}</div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic", padding: "10px 13px", background: "rgba(122,162,247,.05)", border: "1px solid rgba(122,162,247,.18)", borderRadius: 6 }}>
        Bundle-driven: the LM-emitted Ansible playbook from <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "emit_remediation_bundle" } }))}
          className="mono" style={{ color: "var(--accent)", cursor: "pointer", fontStyle: "normal" }}
        >emit_remediation_bundle ↗</span> ran on each host via <span className="mono" style={{ color: "var(--accent)" }}>cargonet_exec</span>. Host <span className="mono">ok</span> gates on <span className="mono">verify-tagged</span> tasks succeeding, not every task — apply tasks may legitimately fail on substrates lacking the vendor service. Then <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: rollback ? "partial_apply_rollback" : "verify_immediate" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer", fontStyle: "normal" }}
        >{rollback ? "partial_apply_rollback" : "verify_immediate"} ↗</span> runs next.
      </div>
    </div>
  );
}

// ─── PartialApplyRollbackPanel ──────────────────────────────────────────

function PartialApplyRollbackPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="partial_apply_rollback" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const triggered = !!(allState.rollback_triggered ?? runState.rollback_triggered);
  const ledger = allState.execution_ledger || runState.execution_ledger || [];
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const haltReason = allState.halt_reason || runState.halt_reason || "";
  const rollbackEntry = ledger.find(e => typeof e === "string" && e.startsWith("rollback@"));
  const rollbackTs = rollbackEntry ? rollbackEntry.replace("rollback@", "") : "";
  const fired = triggered || !!rollbackEntry;

  const VERDICT = fired
    ? { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "↶", label: "ROLLBACK EXECUTED", desc: "Apply phase failed mid-flight; recorded rollback in ledger and halted." }
    : { color: "var(--ok)",   bg: "var(--ok-dim)",       icon: "✓", label: "NO ROLLBACK NEEDED", desc: "Progressive execute succeeded; node ran as no-op guard." };

  const elapsedMs = timing?.elapsed_ms;
  return (
    <div data-panel-id="partial_apply_rollback" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        PartialApplyRollback · fail-branch from progressive_execute
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: VERDICT.color, fontWeight: 700, lineHeight: 1 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{VERDICT.desc}</div>
        </div>
      </div>

      {ledger.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
            <span>execution ledger</span>
            <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-2)" }}>{ledger.length} entries</span>
          </div>
          <div style={{ padding: 4 }}>
            {ledger.map((entry, i) => {
              const isRollback = typeof entry === "string" && entry.startsWith("rollback@");
              return (
                <div key={i} style={{ display: "grid", gridTemplateColumns: "20px 1fr", gap: 8, padding: "6px 10px", borderBottom: i < ledger.length - 1 ? "1px solid var(--line-1)" : "none", fontSize: 12 }}>
                  <span className="mono" style={{ color: isRollback ? "var(--warn)" : "var(--ok)" }}>{isRollback ? "↶" : "✓"}</span>
                  <span className="mono" style={{ color: isRollback ? "var(--warn)" : "var(--fg-0)", wordBreak: "break-all" }}>{String(entry)}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
        <span style={{ color: "var(--fg-3)" }}>verify_outcome</span><span className="mono" style={{ color: verifyOutcome === "patched" ? "var(--ok)" : verifyOutcome === "vulnerable" ? "var(--warn)" : "var(--fg-2)" }}>{verifyOutcome || "—"}</span>
        {rollbackTs && (<><span style={{ color: "var(--fg-3)" }}>rollback at</span><span className="mono" style={{ color: "var(--warn)" }}>{rollbackTs}</span></>)}
        {haltReason && (<><span style={{ color: "var(--fg-3)" }}>halt reason</span><span style={{ color: "var(--fg-1)" }}>{haltReason}</span></>)}
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Guard: only emits ledger entry when <span className="mono">rollback_triggered=true</span>. Otherwise runs as a no-op so successful applies aren't tagged as rollbacks.
      </div>
    </div>
  );
}

// ─── DivergenceQuarantinePanel ──────────────────────────────────────────

function DivergenceQuarantinePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="gate" lifecycle={lifecycle} panelId="divergence_quarantine" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const divergence = !!(allState.sandbox_prod_divergence ?? runState.sandbox_prod_divergence);
  const driftEvents = allState.drift_events || runState.drift_events || [];
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const planHash = allState.plan_hash || runState.plan_hash || "";
  const divergenceEntry = driftEvents.find(e => typeof e === "string" && e.startsWith("divergence@"));
  const digestShort = divergenceEntry ? divergenceEntry.replace("divergence@", "") : "";

  const VERDICT = divergence
    ? { color: "var(--err)",  bg: "rgba(239,106,106,.12)", icon: "⚠", label: "DIVERGENCE QUARANTINED", desc: "Sandbox passed but production verify failed. Plan hash flagged (F5)." }
    : { color: "var(--ok)",   bg: "var(--ok-dim)",         icon: "✓", label: "NO DIVERGENCE",            desc: "Sandbox and prod verify agree; quarantine guard ran as no-op." };

  const elapsedMs = timing?.elapsed_ms;
  return (
    <div data-panel-id="divergence_quarantine" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        DivergenceQuarantine · sandbox⇄prod gate · halt-new (F5)
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: VERDICT.color, fontWeight: 700, lineHeight: 1 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{VERDICT.desc}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
        <span style={{ color: "var(--fg-3)" }}>sandbox_prod_divergence</span><span className="mono" style={{ color: divergence ? "var(--err)" : "var(--ok)" }}>{String(divergence)}</span>
        <span style={{ color: "var(--fg-3)" }}>verify_outcome</span><span className="mono" style={{ color: verifyOutcome === "divergence" ? "var(--err)" : verifyOutcome === "patched" ? "var(--ok)" : "var(--fg-2)" }}>{verifyOutcome || "—"}</span>
        {planHash && (<><span style={{ color: "var(--fg-3)" }}>plan_hash</span><span className="mono" style={{ color: divergence ? "var(--err)" : "var(--fg-1)" }}>{planHash.slice(0, 32)}{planHash.length > 32 ? "…" : ""}</span></>)}
      </div>

      {driftEvents.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>drift events</div>
          <div style={{ padding: 4 }}>
            {driftEvents.map((e, i) => (
              <div key={i} className="mono" style={{ padding: "6px 10px", fontSize: 11.5, color: "var(--fg-0)", borderBottom: i < driftEvents.length - 1 ? "1px solid var(--line-1)" : "none", wordBreak: "break-all" }}>
                {String(e)}
              </div>
            ))}
          </div>
        </div>
      )}

      {digestShort && (
        <div style={{ fontSize: 11, color: "var(--fg-3)" }}>
          Quarantine artifact <span className="mono" style={{ color: "var(--err)" }}>{digestShort}</span> written to <span className="mono">.harbor/artifacts/divergence/</span>; downstream planners read this hash to skip vetoed plans.
        </div>
      )}
    </div>
  );
}

// ─── KgRunWritebackPanel ────────────────────────────────────────────────

function KgRunWritebackPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="kg" lifecycle={lifecycle} panelId="kg_run_writeback" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const written = !!(allState.kg_run_written ?? runState.kg_run_written);
  const nodesWritten = allState.kg_run_nodes_written ?? runState.kg_run_nodes_written ?? 0;
  const edgesWritten = allState.kg_run_edges_written ?? runState.kg_run_edges_written ?? 0;
  const lastErr = allState.last_kg_run_error || runState.last_kg_run_error || "";

  let VERDICT;
  if (written) VERDICT = { color: "var(--ok)", bg: "var(--ok-dim)", icon: "✓", label: "RUN UPSERTED", desc: "Run + CVE + Action + CI nodes/edges merged into runtime KG (Neo4j)." };
  else if (lastErr) VERDICT = { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "⊘", label: "HONEST SKIP", desc: lastErr };
  else VERDICT = { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: "NOT WRITTEN", desc: "No CVE id or write was a no-op." };

  const elapsedMs = timing?.elapsed_ms;
  return (
    <div data-panel-id="kg_run_writeback" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        RyuGraph · neo4j · cve_rem.kg_run_writeback (MERGE idempotent)
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: VERDICT.color, fontWeight: 700, lineHeight: 1 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{VERDICT.desc}</div>
        </div>
        {written && (
          <div style={{ display: "flex", gap: 12, textAlign: "right" }}>
            <div>
              <div className="mono" style={{ fontSize: 20, color: "var(--fg-0)", fontWeight: 700 }}>{nodesWritten}</div>
              <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>nodes</div>
            </div>
            <div>
              <div className="mono" style={{ fontSize: 20, color: "var(--fg-0)", fontWeight: 700 }}>{edgesWritten}</div>
              <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>edges</div>
            </div>
          </div>
        )}
      </div>

      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>schema</div>
        <div style={{ padding: 12, fontSize: 11.5, fontFamily: "var(--mono)", color: "var(--fg-1)", lineHeight: 1.6 }}>
          <div>(:CVE id cwe cvss_bp kev)</div>
          <div>(:Product name) · (:CWE id) · (:CI sys_id hostname)</div>
          <div>(:Action kind target_version advisory_ref)</div>
          <div>(:Run id plan_hash terminal_outcome sandbox_status verify_outcome)</div>
          <div style={{ marginTop: 8, color: "var(--fg-3)" }}>edges:</div>
          <div>(CVE)-[:HAS_CWE]→(CWE) · (CVE)-[:HAS_PRODUCT]→(Product) · (CVE)-[:AFFECTS]→(CI)</div>
          <div>(Run)-[:RESOLVED]→(CVE) · (Run)-[:USED]→(Action) · (Action)-[:APPLIED_ON]→(CI)</div>
        </div>
      </div>
    </div>
  );
}

// ─── EmitRetroPayloadPanel ──────────────────────────────────────────────

function EmitRetroPayloadPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="artifact" lifecycle={lifecycle} panelId="emit_retro_payload" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const ref = allState.retro_payload_artifact_ref || runState.retro_payload_artifact_ref || "";
  const outcome = allState.retro_outcome || runState.retro_outcome || "";
  const planHash = allState.plan_hash || runState.plan_hash || "";
  const digestMatch = ref.match(/([a-f0-9]{64})(?:\.json)?$/);
  const digest = digestMatch ? digestMatch[1] : "";
  const digestShort = digest.slice(0, 16);

  const elapsedMs = timing?.elapsed_ms;
  return (
    <div data-panel-id="emit_retro_payload" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Content-addressed write · cve_rem.emit_retro_payload
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: ref ? "var(--ok-dim)" : "rgba(255,255,255,.04)", border: "1px solid " + (ref ? "var(--ok)" : "var(--line-2)") + "55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: ref ? "var(--ok)" : "var(--fg-3)", fontWeight: 700, lineHeight: 1 }}>{ref ? "✓" : "·"}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: ref ? "var(--ok)" : "var(--fg-2)" }}>{ref ? "RETRO PERSISTED" : "NO ARTIFACT"}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{ref ? "Retro payload written, content-addressed by blake3" : "Retro produced no payload"}</div>
        </div>
        {digestShort && (
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 12, color: "var(--fg-0)", fontWeight: 700 }}>{digestShort}…</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>blake3</div>
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
        {outcome && (<><span style={{ color: "var(--fg-3)" }}>retro_outcome</span><span className="mono" style={{ color: outcome === "patched" ? "var(--ok)" : "var(--warn)" }}>{outcome}</span></>)}
        {planHash && (<><span style={{ color: "var(--fg-3)" }}>plan_hash</span><span className="mono" style={{ color: "var(--fg-1)" }}>{planHash.slice(0, 32)}{planHash.length > 32 ? "…" : ""}</span></>)}
      </div>

      {ref && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>artifact ref</div>
          <div style={{ padding: 12, fontSize: 11.5, color: "var(--fg-0)", wordBreak: "break-all" }} className="mono">{ref}</div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Feeds <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "cargonet_writeback" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
        >cargonet_writeback ↗</span> + <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "plan_kg_writeback" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
        >plan_kg_writeback ↗</span> + <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "render_docx" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
        >render_docx ↗</span>.
      </div>
    </div>
  );
}

// ─── RenderDocxPanel ────────────────────────────────────────────────────

function RenderDocxPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="render_docx" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const env = allState.broker_request_envelope || runState.broker_request_envelope || {};
  const envelope = env.data?.docx_render || env.data?.render_docx || env.data || {};
  const cveId = allState.cve_id || runState.cve_id || "";
  const elapsedMs = timing?.elapsed_ms;

  return (
    <div data-panel-id="render_docx" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        RenderDocx · python-docx + Jinja2 narrative render
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: "var(--ok-dim)", border: "1px solid var(--ok)55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: "var(--ok)", fontWeight: 700, lineHeight: 1 }}>📄</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: "var(--ok)" }}>DOCX RENDERED</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>Narrative artifact assembled from retro payload. Hand-off to emit_docx_archive for content-addressed write.</div>
        </div>
      </div>

      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>render inputs</div>
        <div style={{ padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 12 }}>
          <span style={{ color: "var(--fg-3)" }}>cve</span><span className="mono" style={{ color: "var(--fg-0)" }}>{cveId || "—"}</span>
          <span style={{ color: "var(--fg-3)" }}>template</span><span className="mono" style={{ color: "var(--fg-1)" }}>cve_remediation/templates/retro.docx.j2</span>
          <span style={{ color: "var(--fg-3)" }}>renderer</span><span className="mono" style={{ color: "var(--fg-1)" }}>python-docx + jinja2</span>
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        DOCX is the human-facing retrospective. Downstream: <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "emit_docx_archive" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
        >emit_docx_archive ↗</span> writes it content-addressed, <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "publish_docplus" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
        >publish_docplus ↗</span> attaches to ServiceNow Doc+.
      </div>
    </div>
  );
}

// ─── EmitDocxArchivePanel ───────────────────────────────────────────────

function EmitDocxArchivePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="artifact" lifecycle={lifecycle} panelId="emit_docx_archive" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const ref = allState.docx_artifact_ref || runState.docx_artifact_ref || "";
  const stagingRef = allState.docplus_staging_ref || runState.docplus_staging_ref || "";
  const digestMatch = ref.match(/([a-f0-9]{64})/);
  const digest = digestMatch ? digestMatch[1] : "";
  const digestShort = digest.slice(0, 16);

  const elapsedMs = timing?.elapsed_ms;
  return (
    <div data-panel-id="emit_docx_archive" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Content-addressed DOCX write · cve_rem.emit_docx_archive
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: ref ? "var(--ok-dim)" : "rgba(255,255,255,.04)", border: "1px solid " + (ref ? "var(--ok)" : "var(--line-2)") + "55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: ref ? "var(--ok)" : "var(--fg-3)", fontWeight: 700, lineHeight: 1 }}>{ref ? "✓" : "·"}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: ref ? "var(--ok)" : "var(--fg-2)" }}>{ref ? "DOCX ARCHIVED" : "NO DOCX"}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{ref ? "DOCX persisted by blake3 digest; also acts as Doc+ staging ref" : "Render produced no DOCX"}</div>
        </div>
        {digestShort && (
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 12, color: "var(--fg-0)", fontWeight: 700 }}>{digestShort}…</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>blake3</div>
          </div>
        )}
      </div>

      {ref && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>refs</div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "6px 12px", fontSize: 11.5, fontFamily: "var(--mono)" }}>
            <span style={{ color: "var(--fg-3)" }}>docx</span><span style={{ color: "var(--fg-0)", wordBreak: "break-all" }}>{ref}</span>
            {stagingRef && stagingRef !== ref && (<><span style={{ color: "var(--fg-3)" }}>docplus stage</span><span style={{ color: "var(--fg-1)", wordBreak: "break-all" }}>{stagingRef}</span></>)}
          </div>
        </div>
      )}

      {ref && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
            <span>preview</span>
            <a href={window.apiUrl("/watch/api/artifact?ref=" + encodeURIComponent(ref))} download style={{ color: "var(--info)", textDecoration: "none", fontSize: 10.5 }}>download .docx ↓</a>
          </div>
          <div className="docx-body" style={{ padding: "18px 22px", maxHeight: 520, overflow: "auto", background: "#fafbfc", color: "#222" }}>
            <DocxPreview artifactRef={ref} />
          </div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Same digest used for both <span className="mono">docx_artifact_ref</span> and <span className="mono">docplus_staging_ref</span> — Doc+ publish uploads this exact file.
      </div>
    </div>
  );
}

// ─── ProofReportFetch — load markdown artifact, render with MarkdownView

function ProofReportFetch({ artifactRef }) {
  const [state, setState] = React.useState({ status: "idle", text: "", err: "" });
  React.useEffect(() => {
    if (!artifactRef) { setState({ status: "empty", text: "", err: "" }); return; }
    let cancelled = false;
    (async () => {
      setState({ status: "loading", text: "", err: "" });
      try {
        const res = await fetch(window.apiUrl("/watch/api/artifact?ref=" + encodeURIComponent(artifactRef)));
        if (!res.ok) throw new Error("fetch " + res.status);
        const text = await res.text();
        if (!cancelled) setState({ status: "ok", text, err: "" });
      } catch (e) {
        if (!cancelled) setState({ status: "err", text: "", err: String(e?.message || e) });
      }
    })();
    return () => { cancelled = true; };
  }, [artifactRef]);

  if (state.status === "empty")   return <div className="muted" style={{ padding: "12px 14px" }}>no proof report artifact</div>;
  if (state.status === "loading") return <div className="muted" style={{ padding: "12px 14px" }}>fetching .md…</div>;
  if (state.status === "err")     return <div style={{ color: "var(--err)", padding: "12px 14px", fontSize: 12 }}>fetch failed: {state.err}</div>;
  return <MarkdownView source={state.text} />;
}

// ─── EmitProofReportPanel ──────────────────────────────────────────────

function EmitProofReportPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="artifact" lifecycle={lifecycle} panelId="emit_proof_report" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const ref = allState.proof_report_artifact_ref || runState.proof_report_artifact_ref || "";
  const attachSysId = allState.proof_report_attachment_sys_id || runState.proof_report_attachment_sys_id || "";
  const err = allState.last_proof_report_error || runState.last_proof_report_error || "";
  const cveId = allState.cve_id || runState.cve_id || "";
  const planHash = allState.plan_hash || runState.plan_hash || "";
  const elapsedMs = timing?.elapsed_ms;

  const digestMatch = ref.match(/([a-f0-9]{64})/);
  const digest = digestMatch ? digestMatch[1] : "";
  const digestShort = digest ? digest.slice(0, 12) + "…" + digest.slice(-6) : "";

  const VERDICT = ref
    ? { color: "var(--ok)", bg: "var(--ok-dim)", icon: "📋", label: "PROOF REPORT WRITTEN" }
    : err
    ? { color: "var(--err)", bg: "rgba(239,106,106,.12)", icon: "✗", label: "REPORT FAILED" }
    : { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: "NO REPORT" };

  return (
    <div data-panel-id="emit_proof_report" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Full-run proof report · cve_rem.emit_proof_report · markdown
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "16px 20px", borderRadius: 10, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 44, height: 44, borderRadius: 22, background: VERDICT.color + "22", border: "1px solid " + VERDICT.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
            {ref ? "8-section narrative: CVE, hosts, doctrine, plan, sandbox, verify, retro, audit ids" : err || "Report not generated"}
          </div>
        </div>
        {digestShort && (
          <div style={{ textAlign: "right" }}>
            <div className="mono" style={{ fontSize: 11.5, color: "var(--fg-0)", fontWeight: 600 }}>{digestShort}</div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 2 }}>blake3</div>
          </div>
        )}
      </div>

      {/* Refs grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>artifact ref</div>
          <div className="mono" style={{ fontSize: 10.5, color: "var(--fg-0)", wordBreak: "break-all" }}>{ref || "—"}</div>
        </div>
        <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>SN attachment</div>
          <div className="mono" style={{ fontSize: 11.5, color: attachSysId ? "var(--fg-0)" : "var(--fg-3)", fontWeight: attachSysId ? 600 : 400, wordBreak: "break-all" }}>{attachSysId || "not attached"}</div>
        </div>
      </div>

      {/* Section legend */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, padding: "12px 14px" }}>
        <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 9 }}>report sections</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "5px 14px", fontSize: 11 }}>
          {[
            "CVE summary (id, CWE, CVSS, KEV)",
            "Affected hosts (CMDB + CargoNet)",
            "Doctrine mapping (Control→CWE→CVE)",
            "Plan (hash, runtime, rationale)",
            "Sandbox evidence (4-phase probe)",
            "Verification outcome",
            "Retrospective writebacks",
            "Audit ids (CR + attachments)",
          ].map((s, i) => (
            <div key={i} style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
              <span className="mono" style={{ fontSize: 10, color: "var(--accent)", fontWeight: 600 }}>{(i + 1) + "."}</span>
              <span style={{ color: "var(--fg-1)" }}>{s}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Rendered markdown */}
      {ref && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
          <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>rendered report</span>
            <a href={window.apiUrl("/watch/api/artifact?ref=" + encodeURIComponent(ref))} download={`proof_report_${cveId || "cve"}.md`} style={{ color: "var(--info)", textDecoration: "none", fontSize: 10.5 }}>download .md ↓</a>
          </div>
          <div style={{ padding: "16px 20px", maxHeight: 680, overflowY: "auto" }}>
            <ProofReportFetch artifactRef={ref} />
          </div>
        </div>
      )}

      {planHash && (
        <div style={{ fontSize: 11, color: "var(--fg-3)" }}>
          plan_hash: <span className="mono" style={{ color: "var(--fg-1)" }}>{planHash.slice(0, 24)}{planHash.length > 24 ? "…" : ""}</span>
        </div>
      )}

      {err && (
        <div style={{ padding: 10, background: "rgba(239,106,106,.12)", border: "1px solid var(--err)55", borderRadius: 6, fontSize: 12 }}>
          <span style={{ color: "var(--err)", fontWeight: 600 }}>proof report error</span>: <span className="mono" style={{ color: "var(--fg-0)" }}>{err}</span>
        </div>
      )}
    </div>
  );
}

// ─── RetroDispatchPanel — parallel fan-out ──────────────────────────────

function RetroDispatchPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="branch" lifecycle={lifecycle} panelId="retro_dispatch" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const elapsedMs = timing?.elapsed_ms;

  // Retro identity + outcome
  const retroId = allState.retro_id || runState.retro_id || "";
  const retroOutcome = allState.retro_outcome || runState.retro_outcome || "";
  const retroPayloadRef = allState.retro_payload_artifact_ref || runState.retro_payload_artifact_ref || "";
  const retroPayloadDigest = (retroPayloadRef.match(/([a-f0-9]{64})/) || [])[1] || "";
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const cveId = allState.cve_id || runState.cve_id || "";

  // Upstream retro writes (write_retrospective)
  const retroPgWritten = !!(allState.retro_pg_written ?? runState.retro_pg_written);
  const retroPgvWritten = !!(allState.retro_pgvector_written ?? runState.retro_pgvector_written);
  const retroRedisWritten = !!(allState.retro_redis_written ?? runState.retro_redis_written);
  const suggestionCount = allState.retro_suggestion_count ?? runState.retro_suggestion_count ?? 0;
  const preventions = allState.retro_prevention_suggestions || runState.retro_prevention_suggestions || [];
  const failureSignals = allState.retro_failure_signals || runState.retro_failure_signals || [];

  // Prior retro context
  const priorCount = allState.prior_retro_count ?? runState.prior_retro_count ?? 0;
  const priorOutcomes = allState.prior_retro_outcomes || runState.prior_retro_outcomes || {};
  const priorMode = allState.prior_retro_retrieval_mode || runState.prior_retro_retrieval_mode || "";
  const priorStatus = allState.prior_retro_retrieval_status || runState.prior_retro_retrieval_status || "";
  const priorSuggestions = allState.prior_retro_suggestions || runState.prior_retro_suggestions || [];
  const priorPgCount = allState.prior_retros_pg_count ?? runState.prior_retros_pg_count ?? 0;
  const holdoutCount = allState.holdout_retro_count ?? runState.holdout_retro_count ?? 0;

  // Downstream fan-out sinks
  const docplusPublished = !!(allState.docplus_published ?? runState.docplus_published);
  const cargonetWritten = !!(allState.cargonet_writeback_done ?? runState.cargonet_writeback_done);
  const planKgWritten = !!(allState.plan_kg_writeback_done ?? runState.plan_kg_writeback_done);
  const allDone = docplusPublished && cargonetWritten && planKgWritten;

  const ARMS = [
    { id: "publish_docplus",     label: "ServiceNow Doc+",  icon: "📄", desc: "Attach DOCX retro to ServiceNow", done: docplusPublished, err: allState.last_docplus_table_error || "" },
    { id: "cargonet_writeback",  label: "CargoNet",         icon: "🛰", desc: "Visibility-only run-trace append",  done: cargonetWritten,  err: allState.last_cargonet_error || "" },
    { id: "plan_kg_writeback",   label: "Plan-KG",          icon: "🧠", desc: "Neo4j VERIFIED_ON learning edge",   done: planKgWritten,    err: "" },
  ];
  const completedCount = ARMS.filter(a => a.done).length;

  // Prior outcome ratio
  const priorPatched = priorOutcomes.patched || 0;
  const priorRollback = priorOutcomes.rollback || 0;
  const priorOther = priorCount - priorPatched - priorRollback;
  const patchedPct = priorCount > 0 ? Math.round((priorPatched / priorCount) * 100) : 0;

  const isPatched = retroOutcome === "patched";

  return (
    <div data-panel-id="retro_dispatch" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Parallel retro fan-out · cve_rem.retro_dispatch · 3 sinks join at retro_join
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* Hero: retro outcome + arms count */}
      <div style={{ padding: "16px 20px", borderRadius: 10, background: isPatched ? "var(--ok-dim)" : "rgba(245,181,74,.12)", border: "1px solid " + (isPatched ? "var(--ok)" : "var(--warn)") + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 48, height: 48, borderRadius: 24, background: (isPatched ? "var(--ok)" : "var(--warn)") + "22", border: "1px solid " + (isPatched ? "var(--ok)" : "var(--warn)") + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22 }}>{isPatched ? "✓" : "↶"}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: isPatched ? "var(--ok)" : "var(--warn)" }}>RETRO · {(retroOutcome || "—").toUpperCase()}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>
            {retroId && <>retro_id <span className="mono" style={{ color: "var(--fg-0)" }}>{retroId}</span> · </>}
            {suggestionCount > 0 && <>{suggestionCount} prevention suggestion{suggestionCount !== 1 ? "s" : ""} · </>}
            fanning out to 3 sinks
          </div>
        </div>
        <span style={{ background: (allDone ? "var(--ok)" : "var(--info)") + "22", color: allDone ? "var(--ok)" : "var(--info)", fontSize: 10.5, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>{completedCount}/3 SINKS</span>
      </div>

      {/* Prior retro context — learning loop signal */}
      {priorCount > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
          <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
            <span>prior retro retrieval</span>
            <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: priorStatus === "ok" ? "var(--ok)" : "var(--warn)" }}>{priorMode} · {priorStatus}</span>
          </div>
          <div style={{ padding: "12px 14px", display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, alignItems: "center" }}>
            {/* Prior count + ratio bar */}
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
                <span className="mono" style={{ fontSize: 24, color: "var(--fg-0)", fontWeight: 700, lineHeight: 1 }}>{priorCount}</span>
                <span style={{ fontSize: 11, color: "var(--fg-3)" }}>prior retros</span>
              </div>
              <div style={{ display: "flex", height: 8, marginTop: 6, borderRadius: 4, overflow: "hidden", background: "rgba(255,255,255,.04)" }}>
                {priorPatched > 0 && <div style={{ width: `${(priorPatched / priorCount) * 100}%`, background: "var(--ok)" }} title={`${priorPatched} patched`} />}
                {priorRollback > 0 && <div style={{ width: `${(priorRollback / priorCount) * 100}%`, background: "var(--warn)" }} title={`${priorRollback} rollback`} />}
                {priorOther > 0 && <div style={{ width: `${(priorOther / priorCount) * 100}%`, background: "var(--fg-3)" }} title={`${priorOther} other`} />}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 4, fontSize: 10, color: "var(--fg-3)" }}>
                <span><span style={{ color: "var(--ok)" }}>●</span> {priorPatched} patched</span>
                <span><span style={{ color: "var(--warn)" }}>●</span> {priorRollback} rollback</span>
                {priorOther > 0 && <span><span style={{ color: "var(--fg-3)" }}>●</span> {priorOther}</span>}
              </div>
            </div>
            {/* Success ratio */}
            <div style={{ textAlign: "center", borderLeft: "1px solid var(--line-1)", borderRight: "1px solid var(--line-1)", padding: "0 12px" }}>
              <div className="mono" style={{ fontSize: 24, color: patchedPct >= 80 ? "var(--ok)" : patchedPct >= 50 ? "var(--warn)" : "var(--err)", fontWeight: 700, lineHeight: 1 }}>{patchedPct}%</div>
              <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 5 }}>patched ratio</div>
            </div>
            {/* PG / holdout */}
            <div style={{ fontSize: 11, color: "var(--fg-2)", display: "flex", flexDirection: "column", gap: 4 }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--fg-3)" }}>postgres</span>
                <span className="mono" style={{ color: "var(--fg-1)" }}>{priorPgCount}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--fg-3)" }}>holdout</span>
                <span className="mono" style={{ color: "var(--fg-1)" }}>{holdoutCount}</span>
              </div>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "var(--fg-3)" }}>this run</span>
                <span className="mono" style={{ color: isPatched ? "var(--ok)" : "var(--warn)", fontWeight: 600 }}>+1 {retroOutcome || ""}</span>
              </div>
            </div>
          </div>

          {/* Top prior suggestion */}
          {priorSuggestions.length > 0 && priorSuggestions[0].suggestion_text && (
            <div style={{ padding: "10px 14px", borderTop: "1px solid var(--line-1)", fontSize: 11, color: "var(--fg-2)" }}>
              <span style={{ color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", fontSize: 10 }}>top prior suggestion · </span>
              <span style={{ color: "var(--fg-1)", fontStyle: "italic" }}>"{priorSuggestions[0].suggestion_text.length > 140 ? priorSuggestions[0].suggestion_text.slice(0, 140) + "…" : priorSuggestions[0].suggestion_text}"</span>
            </div>
          )}
        </div>
      )}

      {/* Retro payload artifact */}
      {retroPayloadRef && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
          <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
              <span style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>retro payload</span>
              <a href={window.apiUrl("/watch/api/artifact?ref=" + encodeURIComponent(retroPayloadRef))} download={`retro_${cveId || "cve"}.json`} style={{ color: "var(--info)", fontSize: 10, textDecoration: "none" }}>download ↓</a>
            </div>
            <div className="mono" style={{ fontSize: 11.5, color: "var(--fg-0)", fontWeight: 600 }}>{retroPayloadDigest ? retroPayloadDigest.slice(0, 12) + "…" + retroPayloadDigest.slice(-6) : "—"}</div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", marginTop: 3 }}>same JSON written to all 3 sinks</div>
          </div>
          <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 6 }}>upstream writes (write_retrospective)</div>
            <div style={{ display: "flex", gap: 14, fontSize: 11 }}>
              <span style={{ color: retroPgWritten ? "var(--ok)" : "var(--fg-3)" }}>{retroPgWritten ? "✓" : "○"} pg</span>
              <span style={{ color: retroPgvWritten ? "var(--ok)" : "var(--fg-3)" }}>{retroPgvWritten ? "✓" : "○"} pgvector</span>
              <span style={{ color: retroRedisWritten ? "var(--ok)" : "var(--fg-3)" }}>{retroRedisWritten ? "✓" : "○"} redis</span>
            </div>
          </div>
        </div>
      )}

      {/* Visual fan-out */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>parallel sinks · strategy: all</div>
        <div style={{ padding: "18px 16px", position: "relative" }}>
          {/* Source */}
          <div style={{ display: "flex", justifyContent: "center", marginBottom: 22 }}>
            <span className="mono" style={{ fontSize: 11, color: "var(--accent)", padding: "6px 14px", border: "1px solid var(--accent)55", borderRadius: 16, background: "var(--accent)11" }}>retro_dispatch</span>
          </div>
          {/* Arms */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            {ARMS.map((a) => (
              <div key={a.id} style={{
                padding: "12px 11px", borderRadius: 8,
                background: a.done ? "var(--ok-dim)" : a.err ? "rgba(239,106,106,.08)" : "rgba(245,181,74,.06)",
                border: "1px solid " + (a.done ? "var(--ok)" : a.err ? "var(--err)" : "var(--warn)") + "44",
                display: "flex", flexDirection: "column", alignItems: "center", gap: 5, position: "relative",
              }}>
                <div style={{ position: "absolute", top: -14, left: "50%", transform: "translateX(-50%)", width: 1, height: 14, background: a.done ? "var(--ok)" : a.err ? "var(--err)" : "var(--warn)" }} />
                <div style={{ fontSize: 18 }}>{a.icon}</div>
                <span
                  onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: a.id } }))}
                  className="mono" style={{ fontSize: 10.5, color: a.done ? "var(--ok)" : a.err ? "var(--err)" : "var(--warn)", fontWeight: 600, cursor: "pointer", textAlign: "center" }}
                >{a.id} ↗</span>
                <span style={{ fontSize: 10, color: "var(--fg-2)", textAlign: "center", lineHeight: 1.4 }}>{a.desc}</span>
                <span style={{ fontSize: 9.5, color: a.done ? "var(--ok)" : a.err ? "var(--err)" : "var(--warn)", fontWeight: 700, letterSpacing: ".06em", marginTop: 1 }}>{a.done ? "✓ DONE" : a.err ? "✗ FAILED" : "⊘ PENDING"}</span>
              </div>
            ))}
          </div>
          {/* Join */}
          <div style={{ display: "flex", justifyContent: "center", marginTop: 18, position: "relative" }}>
            <div style={{ position: "absolute", top: -14, left: "50%", transform: "translateX(-50%)", width: 1, height: 14, background: allDone ? "var(--ok)" : "var(--fg-3)" }} />
            <span
              onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "retro_join" } }))}
              className="mono" style={{ fontSize: 11, color: allDone ? "var(--ok)" : "var(--fg-3)", padding: "6px 14px", border: "1px solid " + (allDone ? "var(--ok)" : "var(--fg-3)") + "55", borderRadius: 16, background: (allDone ? "var(--ok)" : "var(--fg-3)") + "11", cursor: "pointer" }}
            >retro_join ↗</span>
          </div>
        </div>
      </div>

      {/* Prevention suggestions (this run) */}
      {preventions.length > 0 && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
          <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>this run's prevention suggestions ({preventions.length})</div>
          <div>
            {preventions.slice(0, 4).map((p, i) => (
              <div key={i} style={{ padding: "10px 14px", borderBottom: i < Math.min(preventions.length, 4) - 1 ? "1px solid var(--line-1)" : "none", display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 10, alignItems: "start", fontSize: 11.5 }}>
                <span style={{ color: "var(--accent)", fontWeight: 600, fontSize: 10, textTransform: "uppercase", letterSpacing: ".06em", whiteSpace: "nowrap", paddingTop: 1 }}>{p.category || "—"}</span>
                <span style={{ color: "var(--fg-1)", lineHeight: 1.4 }}>{p.suggestion || ""}</span>
                {p.confidence_bp != null && <span className="mono" style={{ color: p.confidence_bp >= 7500 ? "var(--ok)" : "var(--warn)", fontSize: 10.5, whiteSpace: "nowrap" }}>{(p.confidence_bp / 100).toFixed(0)}%</span>}
              </div>
            ))}
            {preventions.length > 4 && <div style={{ padding: "8px 14px", fontSize: 10.5, color: "var(--fg-3)", borderTop: "1px solid var(--line-1)" }}>+{preventions.length - 4} more</div>}
          </div>
        </div>
      )}

      {/* Failure signals if any */}
      {failureSignals.length > 0 && (
        <div style={{ padding: "10px 13px", background: "rgba(239,106,106,.08)", border: "1px solid rgba(239,106,106,.28)", borderRadius: 6, fontSize: 11.5 }}>
          <div style={{ color: "var(--err)", fontWeight: 600, marginBottom: 4, fontSize: 10.5, textTransform: "uppercase", letterSpacing: ".08em" }}>failure signals ({failureSignals.length})</div>
          {failureSignals.slice(0, 3).map((s, i) => (
            <div key={i} style={{ color: "var(--fg-1)", fontSize: 11 }}>
              <span className="mono" style={{ color: "var(--err)" }}>{s.kind || "?"}</span>: {s.detail || ""}
            </div>
          ))}
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic", padding: "10px 13px", background: "rgba(122,162,247,.05)", border: "1px solid rgba(122,162,247,.18)", borderRadius: 6 }}>
        Three sinks write the same retro payload independently. Join uses <span className="mono" style={{ color: "var(--accent)" }}>strategy: all</span> — any sink failure halts the join. Then <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "krakntrust_attest" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer", fontStyle: "normal" }}
        >krakntrust_attest ↗</span> signs the full run trace including <span className="mono">retro_id</span>.
      </div>
    </div>
  );
}

// ─── PublishDocPlusPanel ────────────────────────────────────────────────

function PublishDocPlusPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="publish_docplus" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const published = !!(allState.docplus_published ?? runState.docplus_published);
  const ids = {
    collection: allState.docplus_collection_sys_id || runState.docplus_collection_sys_id || "",
    doc:        allState.docplus_doc_sys_id || runState.docplus_doc_sys_id || "",
    version:    allState.docplus_version_sys_id || runState.docplus_version_sys_id || "",
    m2m:        allState.docplus_m2m_sys_id || runState.docplus_m2m_sys_id || "",
    attachment: allState.docplus_attachment_sys_id || runState.docplus_attachment_sys_id || "",
    docAttachment: allState.docplus_doc_attachment_sys_id || runState.docplus_doc_attachment_sys_id || "",
    versionAttachment: allState.docplus_version_attachment_sys_id || runState.docplus_version_attachment_sys_id || "",
  };

  const VERDICT = published
    ? { color: "var(--ok)",   bg: "var(--ok-dim)",          icon: "✓", label: "PUBLISHED",  desc: "Doc+ collection + doc + m2m link + attachments created in ServiceNow." }
    : { color: "var(--warn)", bg: "rgba(245,181,74,.12)",  icon: "⊘", label: "NOT PUBLISHED", desc: "ServiceNow creds unset or publish skipped." };

  const elapsedMs = timing?.elapsed_ms;
  return (
    <div data-panel-id="publish_docplus" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        ServiceNow Doc+ · cve_rem.publish_docplus (Nautilus broker)
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: VERDICT.color, fontWeight: 700, lineHeight: 1 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{VERDICT.desc}</div>
        </div>
      </div>

      {published && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>ServiceNow sys_ids</div>
          <div style={{ padding: 12, display: "grid", gridTemplateColumns: "auto 1fr", gap: "5px 14px", fontSize: 11.5 }}>
            {Object.entries(ids).filter(([, v]) => v).map(([k, v]) => (
              <React.Fragment key={k}>
                <span style={{ color: "var(--fg-3)" }}>{k}</span>
                <span className="mono" style={{ color: "var(--fg-0)" }}>{v}</span>
              </React.Fragment>
            ))}
          </div>
        </div>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Creates the trio: Doc+ <span className="mono">collection</span> (groups per-CVE docs) → <span className="mono">doc</span> (this CVE's record) → <span className="mono">m2m</span> link (collection↔doc). Then attaches the rendered DOCX as a doc + version artifact.
      </div>
    </div>
  );
}

// ─── CargoNetWritebackPanel ─────────────────────────────────────────────

function CargoNetWritebackPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="cargonet_writeback" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const done = !!(allState.cargonet_writeback_done ?? runState.cargonet_writeback_done);
  const elapsedMs = timing?.elapsed_ms;

  const VERDICT = done
    ? { color: "var(--ok)", bg: "var(--ok-dim)", icon: "✓", label: "WRITEBACK COMPLETE", desc: "Run trace appended to CargoNet for future probe-history lookup." }
    : { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "⊘", label: "NOT WRITTEN", desc: "CargoNet endpoint unavailable or visibility-only mode." };

  return (
    <div data-panel-id="cargonet_writeback" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        CargoNet · cve_rem.cargonet_writeback · visibility-only (Nautilus broker)
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "14px 18px", borderRadius: 8, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 16 }}>
        <div style={{ fontSize: 26, color: VERDICT.color, fontWeight: 700, lineHeight: 1 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".06em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{VERDICT.desc}</div>
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic" }}>
        Future runs query <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "cargonet_lab_telemetry" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer" }}
        >cargonet_lab_telemetry ↗</span> for prior-run signal; this writeback is what populates that history. Visibility-only — no policy effect.
      </div>
    </div>
  );
}

// ─── PlanKgWritebackPanel ───────────────────────────────────────────────

function PlanKgWritebackPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="kg" lifecycle={lifecycle} panelId="plan_kg_writeback" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const done = !!(allState.plan_kg_writeback_done ?? runState.plan_kg_writeback_done);
  const planHash = allState.plan_hash || runState.plan_hash || "";
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const cveId = allState.cve_id || runState.cve_id || "";
  const productName = allState.matched_candidate_product || runState.matched_candidate_product || allState.cve_product || runState.cve_product || "";
  const fixedVersion = allState.fixed_version || runState.fixed_version || "";
  const outcomeLabel = verifyOutcome === "patched" ? "patched"
    : (verifyOutcome === "vulnerable" || verifyOutcome === "divergence") ? "rollback"
    : verifyOutcome || "—";
  const isPatched = outcomeLabel === "patched";
  const elapsedMs = timing?.elapsed_ms;

  const VERDICT = done
    ? isPatched
      ? { color: "var(--ok)", bg: "var(--ok-dim)", icon: "✓", label: "PATCHED OUTCOME RECORDED" }
      : { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "↶", label: "ROLLBACK OUTCOME RECORDED" }
    : { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: "NOT WRITTEN" };

  const sub = done
    ? isPatched
      ? "This plan template now has +1 patched record against this CVE in the runtime KG."
      : "This plan template now has +1 rollback record against this CVE — future planners will down-rank it."
    : "Plan-KG endpoint unavailable; outcome not persisted.";

  return (
    <div data-panel-id="plan_kg_writeback" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Plan-KG learning loop · neo4j · :PlanTemplate-[:VERIFIED_ON]-&gt;:CVE
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "16px 20px", borderRadius: 10, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 44, height: 44, borderRadius: 22, background: VERDICT.color + "22", border: "1px solid " + VERDICT.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, color: VERDICT.color, fontWeight: 700 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)", lineHeight: 1.45 }}>{sub}</div>
        </div>
        <span style={{ background: VERDICT.color + "22", color: VERDICT.color, fontSize: 10.5, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>{outcomeLabel.toUpperCase()}</span>
      </div>

      {/* Cypher edge visualization */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>cypher write</span>
          <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-3)" }}>MERGE idempotent</span>
        </div>
        <div style={{ padding: "18px 16px", display: "flex", alignItems: "center", justifyContent: "center", gap: 8, flexWrap: "wrap" }}>
          {/* Source node */}
          <div style={{ padding: "8px 14px", border: "1px solid var(--info)66", borderRadius: 24, background: "var(--info)11", display: "flex", flexDirection: "column", alignItems: "center", gap: 1 }}>
            <span className="mono" style={{ fontSize: 10, color: "var(--info)", letterSpacing: ".06em" }}>:PlanTemplate</span>
            <span className="mono" style={{ fontSize: 11, color: "var(--fg-0)" }}>{planHash ? planHash.slice(0, 12) + "…" : "—"}</span>
          </div>
          {/* Edge */}
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2, padding: "0 4px" }}>
            <span style={{ fontSize: 13, color: VERDICT.color, fontFamily: "var(--mono)", fontWeight: 700, lineHeight: 1 }}>—[:VERIFIED_ON]→</span>
            <span className="mono" style={{ fontSize: 10.5, color: VERDICT.color, background: VERDICT.color + "22", padding: "2px 8px", borderRadius: 3, fontWeight: 600 }}>{`{outcome: "${outcomeLabel}"}`}</span>
          </div>
          {/* Target node */}
          <div style={{ padding: "8px 14px", border: "1px solid var(--accent)66", borderRadius: 24, background: "var(--accent)11", display: "flex", flexDirection: "column", alignItems: "center", gap: 1 }}>
            <span className="mono" style={{ fontSize: 10, color: "var(--accent)", letterSpacing: ".06em" }}>:CVE</span>
            <span className="mono" style={{ fontSize: 11, color: "var(--fg-0)" }}>{cveId || "—"}</span>
          </div>
        </div>
        {(productName || fixedVersion) && (
          <div style={{ padding: "8px 14px", borderTop: "1px solid var(--line-1)", display: "flex", gap: 16, fontSize: 11, color: "var(--fg-3)", flexWrap: "wrap" }}>
            {productName && <span>product: <span className="mono" style={{ color: "var(--fg-1)" }}>{productName}</span></span>}
            {fixedVersion && <span>fixed: <span className="mono" style={{ color: "var(--fg-1)" }}>{fixedVersion}</span></span>}
          </div>
        )}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>plan_hash</div>
          <div className="mono" style={{ fontSize: 11, color: "var(--fg-0)", wordBreak: "break-all" }}>{planHash || "—"}</div>
        </div>
        <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>verify_outcome</div>
          <div className="mono" style={{ fontSize: 12, color: isPatched ? "var(--ok)" : "var(--warn)", fontWeight: 600 }}>{verifyOutcome || "—"}</div>
        </div>
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic", padding: "10px 13px", background: "rgba(122,162,247,.05)", border: "1px solid rgba(122,162,247,.18)", borderRadius: 6 }}>
        Future planners hit <span
          onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: "plan_template_lookup" } }))}
          className="mono" style={{ color: "var(--info)", cursor: "pointer", fontStyle: "normal" }}
        >plan_template_lookup ↗</span> against this plan_hash and rank by <span className="mono" style={{ color: "var(--ok)" }}>patched</span> ÷ (<span className="mono" style={{ color: "var(--ok)" }}>patched</span> + <span className="mono" style={{ color: "var(--warn)" }}>rollback</span>) ratio across CVEs.
      </div>
    </div>
  );
}

// ─── RunOutcomePersistPanel ─────────────────────────────────────────────

function RunOutcomePersistPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="run_outcome_persist" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const written = !!(allState.run_outcome_written ?? runState.run_outcome_written);
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const sandboxStatus = allState.sandbox_status || runState.sandbox_status || "";
  const sandboxRuntime = allState.sandbox_runtime || runState.sandbox_runtime || "";
  const cveId = allState.cve_id || runState.cve_id || "";
  const planHash = allState.plan_hash || runState.plan_hash || "";
  const cweId = allState.cwe_id || runState.cwe_id || (allState.extract && allState.extract.cwe_id) || "";
  const tier = allState.ssvc_decision || runState.ssvc_decision || "";
  const runId = allState.run_id || runState.run_id || (runState.runId || "");
  const elapsedMs = timing?.elapsed_ms;

  const isPatched = verifyOutcome === "patched";
  const VERDICT = written
    ? isPatched
      ? { color: "var(--ok)", bg: "var(--ok-dim)", icon: "✓", label: "OUTCOME PERSISTED · PATCHED" }
      : { color: "var(--warn)", bg: "rgba(245,181,74,.12)", icon: "⚠", label: "OUTCOME PERSISTED · NOT PATCHED" }
    : { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: "NOT PERSISTED" };

  const sub = written
    ? `1 row inserted into fleet_outcomes — feeds F12 dashboard + GEPA training pack`
    : "Postgres unavailable; row not written";

  const rowFields = [
    { key: "run_id",          val: runId,         mono: true,  truncate: true },
    { key: "cve_id",          val: cveId,         mono: true,  emphasis: true },
    { key: "cwe_id",          val: cweId,         mono: true },
    { key: "ssvc_decision",   val: tier,          mono: true },
    { key: "verify_outcome",  val: verifyOutcome, mono: true,  color: isPatched ? "var(--ok)" : "var(--warn)" },
    { key: "sandbox_runtime", val: sandboxRuntime, mono: true },
    { key: "sandbox_status",  val: sandboxStatus, mono: true },
    { key: "plan_hash",       val: planHash,      mono: true,  truncate: true },
  ];

  return (
    <div data-panel-id="run_outcome_persist" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Postgres · fleet_outcomes · F12 fleet learning dashboard
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "16px 20px", borderRadius: 10, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 44, height: 44, borderRadius: 22, background: VERDICT.color + "22", border: "1px solid " + VERDICT.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, color: VERDICT.color, fontWeight: 700 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{sub}</div>
        </div>
        <span style={{ background: VERDICT.color + "22", color: VERDICT.color, fontSize: 10.5, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>+1 ROW</span>
      </div>

      {/* SQL-style row card */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between" }}>
          <span>fleet_outcomes row</span>
          <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-3)" }}>INSERT</span>
        </div>
        <div>
          {rowFields.map((f, i) => (
            <div key={f.key} style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: 12, padding: "9px 14px", borderBottom: i < rowFields.length - 1 ? "1px solid var(--line-1)" : "none", alignItems: "baseline" }}>
              <span className="mono" style={{ fontSize: 11, color: "var(--fg-3)" }}>{f.key}</span>
              <span className={f.mono ? "mono" : ""} style={{ fontSize: 12, color: f.color || (f.emphasis ? "var(--fg-0)" : "var(--fg-1)"), fontWeight: f.emphasis ? 600 : 400, wordBreak: "break-all" }}>
                {f.val ? (f.truncate && String(f.val).length > 48 ? String(f.val).slice(0, 24) + "…" + String(f.val).slice(-12) : String(f.val)) : <span style={{ color: "var(--fg-3)", fontStyle: "italic" }}>NULL</span>}
              </span>
            </div>
          ))}
        </div>
      </div>

      {/* Aggregation context */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        {[
          { label: "rollup", val: "by CWE", desc: "success rate per CWE class" },
          { label: "rollup", val: "by runtime", desc: "patched ratio per sandbox runtime" },
          { label: "rollup", val: "by plan_template", desc: "feeds GEPA training set" },
        ].map((r, i) => (
          <div key={i} style={{ padding: "10px 12px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>{r.label}</div>
            <div className="mono" style={{ fontSize: 12, color: "var(--accent)", fontWeight: 600, marginTop: 2 }}>{r.val}</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)", marginTop: 3 }}>{r.desc}</div>
          </div>
        ))}
      </div>

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic", padding: "10px 13px", background: "rgba(122,162,247,.05)", border: "1px solid rgba(122,162,247,.18)", borderRadius: 6 }}>
        Terminal-stage write — every completed CVE remediation adds exactly one row. The F12 dashboard reads <span className="mono" style={{ color: "var(--info)" }}>fleet_outcomes</span> and computes patch-success funnel + per-cluster regression detection.
      </div>
    </div>
  );
}

// ─── Router / Skipped infra ─────────────────────────────────────────────

/** ROUTER_ROUTES — declarative router definitions extracted from harbor.yaml.
 *  Generic RouterPanel renders each entry with the taken route highlighted.
 *  match: scalar value to compare against state[field]; or a predicate fn.
 */
const ROUTER_ROUTES = {
  halt_new_gate: {
    field: "halt_new_active", label: "New-CVE halt switch",
    routes: [
      { match: true,  target: "halt_action_done", label: "HALTED",   desc: "Operator activated halt — run terminates immediately" },
      { match: false, target: "intake_fetch",     label: "Proceed",  desc: "Halt switch off — continue normal intake" },
    ],
  },
  source_trust_gate: {
    field: "source_trust", label: "Source trust classification",
    routes: [
      { match: "trusted",   target: "canonicalize_trusted",   label: "Trusted",   desc: "NVD/OSV/GHSA — skip injection pipeline" },
      { match: "semi",      target: "canonicalize_untrusted", label: "Semi",      desc: "Vendor advisory — quarantine + scan" },
      { match: "untrusted", target: "canonicalize_untrusted", label: "Untrusted", desc: "Blog/gist/unknown — full injection pipeline" },
    ],
  },
  source_trust_audit: {
    field: "untrusted_text_influenced", label: "Untrusted-text taint",
    routes: [
      { match: false, target: "correlate_assets",   label: "Clean",      desc: "No untrusted text reached extract — proceed" },
      { match: true,  target: "hitl_ingest_review", label: "Influenced", desc: "Untrusted text contaminated extract — HITL review" },
    ],
  },
  plan_template_lookup: {
    field: "template_lookup_hit", label: "Plan template cache",
    routes: [
      { match: true,  target: "validate_plan_join",     label: "Hit",  desc: "Cached template matched — skip planner LM" },
      { match: false, target: "mcp_retrieval_dispatch", label: "Miss", desc: "No cached template — run planner" },
    ],
  },
  critic: {
    fieldFn: (s) => `${s.critic_verdict || ""}@${s.critic_attempt || 0}`,
    label: "Bundle critic verdict",
    routes: [
      { matchFn: (v) => v.startsWith("approved"), target: "validate_dispatch", label: "Approved", desc: "Bundle complete — proceed to validation" },
      { matchFn: (v) => v.startsWith("feedback") && /@(1|2)$/.test(v), target: "planner", label: "Feedback (retry)", desc: "Issues found, attempts 1-2 — re-plan" },
      { matchFn: (v) => v.startsWith("veto") && /@(1|2)$/.test(v), target: "hitl_plan_review", label: "Veto (HITL)", desc: "Critic vetoed bundle — surface to operator" },
      { matchFn: (v) => /@3$/.test(v), target: "hitl_plan_review", label: "Exhausted (HITL)", desc: "3 attempts exhausted — HITL last-resort" },
    ],
  },
  validate_plan_join: {
    fieldFn: (s) => `${s.validation_passed === true ? "pass" : "fail"}|${s.template_lookup_hit ? "tmpl" : "lm"}`,
    label: "Plan validation",
    routes: [
      { matchFn: (v) => v.startsWith("pass"), target: "plan_quarantine_gate", label: "Passed", desc: "Plan valid — proceed to quarantine gate" },
      { matchFn: (v) => v === "fail|tmpl", target: "mcp_retrieval_dispatch", label: "Failed (template)", desc: "Template plan invalid — fall back to LM planner" },
      { matchFn: (v) => v === "fail|lm", target: "hitl_plan_review", label: "Failed (LM)", desc: "LM plan invalid — surface to HITL" },
    ],
  },
  plan_quarantine_gate: {
    field: "plan_quarantined", label: "Plan quarantine",
    routes: [
      { match: false, target: "sandbox_dispatch",  label: "Clear",        desc: "Plan safe — proceed to sandbox" },
      { match: true,  target: "hitl_plan_review",  label: "Quarantined",  desc: "Plan flagged — HITL review required" },
    ],
  },
  sandbox_dispatch: {
    field: "sandbox_runtime", label: "Sandbox runtime dispatch",
    routes: [
      { match: "skip",            target: "sandbox_skip", label: "Skip",            desc: "Runtime unsupported (firmware/embedded) — skip sandbox" },
      { match: "cargonet_lab",    target: "sandbox_run",  label: "CargoNet lab",    desc: "Network device — exercise in lab" },
      { match: "docker_compose",  target: "sandbox_run",  label: "Docker compose",  desc: "Container service — exercise in compose" },
      { match: "static_detection", target: "sandbox_run", label: "Static detection", desc: "Detection-only check — no live exercise" },
    ],
  },
  progressive_execute: {
    fieldFn: (s) => s.rollback_triggered ? "rollback" : s.fleet_passed ? "fleet_ok" : "in_progress",
    label: "Progressive rollout outcome",
    routes: [
      { match: "fleet_ok",    target: "verify_immediate",       label: "Fleet passed",       desc: "All hosts succeeded — verify state" },
      { match: "rollback",    target: "partial_apply_rollback", label: "Rollback triggered", desc: "Failures detected — roll back" },
    ],
  },
  verify_immediate: {
    fieldFn: (s) => s.sandbox_prod_divergence ? "diverged" : s.verify_outcome === "patched" ? "patched" : "unverified",
    label: "Post-apply verification",
    routes: [
      { match: "patched",  target: "create_change_request",  label: "Patched",  desc: "Verify clean — proceed to CR" },
      { match: "diverged", target: "divergence_quarantine",  label: "Diverged", desc: "Sandbox vs prod diverged — quarantine" },
    ],
  },
  attach_all_artifacts: {
    fieldFn: (s) => {
      if (s.unpatchable_disposition === "isolate_recommended" || s.unpatchable_disposition === "disable_recommended") return "unpatchable";
      if (s.skip_sandbox) return "skip_sandbox";
      if (s.ssvc_tier === "act_auto") return "auto";
      if (s.ssvc_tier === "act_hitl_required" || s.ssvc_tier === "attend") return "hitl";
      return "unknown";
    },
    label: "Post-CR routing",
    routes: [
      { match: "auto",        target: "hitl_change_approval", label: "Auto-apply",   desc: "act_auto tier + sandbox passed — HITL approval gate" },
      { match: "hitl",        target: "hitl_change_approval", label: "HITL required", desc: "act_hitl/attend tier — HITL approval gate" },
      { match: "skip_sandbox", target: "hitl_change_approval", label: "Skip sandbox", desc: "Sandbox skipped — HITL approval gate" },
      { match: "unpatchable",  target: "hitl_change_approval", label: "Unpatchable", desc: "Isolate/disable recommended — HITL acknowledgment" },
    ],
  },
};

/** Resolve current field value from state per router definition. */
function _routerCurrentValue(def, state) {
  if (def.fieldFn) return def.fieldFn(state || {});
  const path = Array.isArray(def.field) ? def.field : [def.field];
  let v = state;
  for (const k of path) {
    if (v == null) return undefined;
    v = v[k];
  }
  return v;
}

/** Pick matching route from definition based on current value. */
function _routerMatchedRoute(def, value) {
  for (const r of def.routes) {
    if (r.matchFn && r.matchFn(value)) return r;
    if (r.match !== undefined && r.match === value) return r;
  }
  return null;
}

/** Reverse index: target node id → upstream router that could have skipped it. */
const SKIP_REASON_BY_TARGET = (() => {
  const idx = {};
  for (const [routerId, def] of Object.entries(ROUTER_ROUTES)) {
    for (const r of def.routes) {
      if (!r.target) continue;
      if (!idx[r.target]) idx[r.target] = [];
      idx[r.target].push({ routerId, route: r, def });
    }
  }
  return idx;
})();

function RouterPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const def = ROUTER_ROUTES[node.id];

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;

  // Pull full state at this checkpoint
  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const mergedState = { ...(runState || {}), ...allState };
  const observed = _routerCurrentValue(def, mergedState);
  const taken = _routerMatchedRoute(def, observed);

  return (
    <div data-panel-id={node.id} style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Router · {node.id}
        {typeof timing?.elapsed_ms === "number" && <> · {(timing.elapsed_ms / 1000).toFixed(2)}s</>}
      </div>

      {/* Observed value banner */}
      <div style={{
        padding: "12px 16px", borderRadius: 8,
        background: taken ? "rgba(122,182,255,.08)" : "rgba(255,255,255,.04)",
        border: "1px solid " + (taken ? "rgba(122,182,255,.3)" : "var(--line-2)"),
        display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10,
      }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontSize: 11, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>{def.label}</span>
          <span className="mono" style={{ fontSize: 13, color: "var(--fg-0)", fontWeight: 600 }}>
            {observed === undefined ? <span style={{ color: "var(--fg-3)" }}>—</span> : String(observed)}
          </span>
        </div>
        {lifecycle === "pending" ? (
          <span style={{ background: "rgba(255,255,255,.04)", color: "var(--fg-3)", fontSize: 11, padding: "3px 10px", borderRadius: 3, fontWeight: 600 }}>PENDING</span>
        ) : taken ? (
          <span style={{ background: "var(--ok-dim)", color: "var(--ok)", fontSize: 11, padding: "3px 10px", borderRadius: 3, fontWeight: 700, letterSpacing: ".05em" }}>✓ ROUTED</span>
        ) : (
          <span style={{ background: "rgba(255,193,7,.12)", color: "var(--warn)", fontSize: 11, padding: "3px 10px", borderRadius: 3, fontWeight: 600 }}>NO MATCH</span>
        )}
      </div>

      {/* Routes list */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {def.routes.map((r, i) => {
          const isTaken = taken && r === taken;
          const ringColor = isTaken ? "var(--accent)" : "var(--line-1)";
          const bg = isTaken ? "rgba(61,220,151,.06)" : "var(--bg-3)";
          return (
            <div
              key={i}
              onClick={() => {
                const ev = new CustomEvent("nv:select-node", { detail: { id: r.target } });
                window.dispatchEvent(ev);
              }}
              style={{
                cursor: "pointer", padding: "10px 14px", borderRadius: 6,
                background: bg, border: "1px solid " + ringColor,
                boxShadow: isTaken ? "0 0 0 2px var(--accent-ring)" : "none",
                display: "grid", gridTemplateColumns: "auto 1fr auto", gap: 12, alignItems: "center",
              }}
            >
              <span style={{
                fontSize: 11, fontWeight: 700, padding: "2px 8px", borderRadius: 3, letterSpacing: ".04em",
                background: isTaken ? "var(--accent-dim)" : "rgba(255,255,255,.06)",
                color: isTaken ? "var(--accent)" : "var(--fg-2)",
              }}>{r.label}</span>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <span className="mono" style={{ fontSize: 12, color: isTaken ? "var(--fg-0)" : "var(--fg-2)", fontWeight: isTaken ? 600 : 400 }}>→ {r.target}</span>
                <span style={{ fontSize: 11, color: "var(--fg-3)" }}>{r.desc}</span>
              </div>
              {isTaken && <span style={{ color: "var(--ok)", fontSize: 14, fontWeight: 700 }}>✓</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SkippedPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  // Find the upstream router decision that skipped this node
  const reasons = SKIP_REASON_BY_TARGET[node.id] || [];
  let cause = null;
  if (reasons.length > 0) {
    // Pull state at the router checkpoint to determine which route was taken
    const cps = runState?.checkpoints || [];
    for (const r of reasons) {
      // Find checkpoint at upstream router
      let routerState = null;
      for (const c of cps) {
        if (c.last_node === r.routerId) { routerState = c.state || {}; break; }
      }
      if (!routerState) routerState = runState || {};
      const observed = _routerCurrentValue(r.def, routerState);
      const taken = _routerMatchedRoute(r.def, observed);
      // If router took a DIFFERENT route than this one, that's why we were skipped
      if (taken && taken.target !== node.id) {
        cause = { router: r.routerId, def: r.def, observed, taken };
        break;
      }
    }
  }

  return (
    <div data-panel-id={node.id} style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>Skipped · {node.id}</div>

      <div style={{
        padding: "20px", borderRadius: 8,
        background: "rgba(255,255,255,.03)", border: "1px dashed var(--line-2)",
        display: "flex", alignItems: "center", gap: 20,
      }}>
        <div style={{ fontSize: 32, color: "var(--fg-3)", lineHeight: 1 }}>⊘</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ fontSize: 14, fontWeight: 700, letterSpacing: ".06em", color: "var(--fg-2)" }}>SKIPPED</div>
          <div style={{ fontSize: 12, color: "var(--fg-3)" }}>
            {profile?.title || node.id} was not executed in this run.
          </div>
        </div>
      </div>

      {cause ? (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{
            padding: "8px 12px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase",
            letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)",
          }}>routed around by</div>
          <div style={{ padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
            <div
              onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: cause.router } }))}
              style={{ cursor: "pointer", fontSize: 12 }}
            >
              <span style={{ color: "var(--fg-3)" }}>router </span>
              <span className="mono" style={{ color: "var(--info)" }}>{cause.router} ↗</span>
            </div>
            <div style={{ fontSize: 12 }}>
              <span style={{ color: "var(--fg-3)" }}>{cause.def.label} resolved to </span>
              <span className="mono" style={{ color: "var(--fg-0)", fontWeight: 600 }}>{String(cause.observed)}</span>
            </div>
            <div style={{ fontSize: 12, paddingTop: 6, borderTop: "1px solid var(--line-1)" }}>
              <span style={{ color: "var(--fg-3)" }}>which routed to </span>
              <span
                onClick={() => window.dispatchEvent(new CustomEvent("nv:select-node", { detail: { id: cause.taken.target } }))}
                className="mono"
                style={{ color: "var(--info)", cursor: "pointer" }}
              >{cause.taken.target} ↗</span>
              <span style={{ color: "var(--fg-3)" }}> instead of </span>
              <span className="mono" style={{ color: "var(--fg-2)" }}>{node.id}</span>
            </div>
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--fg-3)", fontStyle: "italic" }}>
          Branch not taken. No matching upstream router decision recorded.
        </div>
      )}
    </div>
  );
}

// ─── SandboxRunPanel (FR-P3) — ToolView command variant ─────────────────

function SandboxRunPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="sandbox" lifecycle={lifecycle} panelId="sandbox_run" />;

  const probePhases = ["baseline", "apply", "rollback", "reapply"];
  const probeSteps = runState.sandbox_probe_steps || {};
  const retryAttempts = runState.sandbox_retry_attempts || [];
  const staticDetection = runState.static_detection_per_host || [];

  return (
    <div data-panel-id="sandbox_run" className="nv-grid">
      <PanelCard title="sandbox" className="span-1">
        <KV pairs={[
          ["runtime", runState.sandbox_runtime],
          ["status", runState.sandbox_status],
          ["latency", runState.sandbox_probe_latency_ms != null ? runState.sandbox_probe_latency_ms + "ms" : null],
        ]} />
      </PanelCard>

      <PanelCard title="exit" className="span-1">
        <div className="bigstat">
          <div className={"bigstat-v " + (runState.sandbox_status === "clean" ? "ok" : runState.sandbox_status === "error" ? "fail" : "")}>{runState.sandbox_status || "—"}</div>
          <div className="bigstat-k">probe result</div>
        </div>
      </PanelCard>

      <PanelCard title="quarantine" className="span-1">
        {runState.sandbox_quarantined ? (
          <div style={{ color: "var(--warn)" }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>QUARANTINED</div>
            <div className="mono" style={{ fontSize: 12 }}>{runState.sandbox_quarantine_reason || "reason not provided"}</div>
          </div>
        ) : <div className="muted">not quarantined</div>}
      </PanelCard>

      <PanelCard title="probe phases" className="span-3" data-field="probe_steps" right={<span className="muted mono">4-phase</span>}>
        <DataTable headers={["Phase", "Status", "Observed", "Expected", "Latency", "Digest", "OK"]} rows={probePhases.map(phase => {
          const step = probeSteps[phase];
          if (!step) return <tr key={phase} data-phase={phase} data-empty="probe-not-run"><td style={{ padding: "6px 8px" }} className="mono">{phase}</td><td colSpan={6} style={{ padding: "6px 8px" }} className="muted">not yet run</td></tr>;
          return (
            <tr key={phase} data-phase={phase}>
              <td style={{ padding: "6px 8px" }} className="mono">{phase}</td>
              <td style={{ padding: "6px 8px" }}>{step.status && <Pill tone={step.status === "pass" ? "ok" : "warn"}>{step.status}</Pill>}</td>
              <td style={{ padding: "6px 8px" }} className="mono">{step.observed_version || ""}</td>
              <td style={{ padding: "6px 8px" }} className="mono">{step.expected_version || ""}</td>
              <td style={{ padding: "6px 8px" }} className="mono">{step.latency_ms != null ? step.latency_ms + "ms" : ""}</td>
              <td style={{ padding: "6px 8px", fontSize: 11 }} className="mono muted">{step.digest ? step.digest.slice(0, 12) : ""}</td>
              <td style={{ padding: "6px 8px" }}>{step.ok != null ? (step.ok ? <span style={{ color: "var(--ok)" }}>✓</span> : <span style={{ color: "var(--err)" }}>✗</span>) : ""}</td>
            </tr>
          );
        })} />
      </PanelCard>

      {retryAttempts.length > 0 && (
        <PanelCard title="retry attempts" className="span-3" right={<span className="muted mono">{retryAttempts.length}</span>}>
          <DataTable headers={["#", "Status", "Reason"]} rows={retryAttempts.map((a, i) => (
            <tr key={i}><td style={{ padding: "6px 8px" }}>{a.attempt || i + 1}</td><td style={{ padding: "6px 8px" }}>{a.status || a.result || ""}</td><td style={{ padding: "6px 8px" }}>{a.reason || ""}</td></tr>
          ))} />
        </PanelCard>
      )}

      {staticDetection.length > 0 && (
        <PanelCard title="static detection" className="span-3">
          <DataTable headers={["Host", "Status"]} rows={staticDetection.map((row, i) => (
            <tr key={i}><td style={{ padding: "6px 8px" }} className="mono">{row.host || row[0] || ""}</td><td style={{ padding: "6px 8px" }}>{row.status || row[1] || ""}</td></tr>
          ))} />
        </PanelCard>
      )}

      {runState.last_sandbox_error && <ErrorBanner field="sandbox" value={runState.last_sandbox_error} />}
    </div>
  );
}

// ─── CreateChangeRequestPanel (FR-P4) — ToolView style ──────────────────

function CreateChangeRequestPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="create_change_request" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const crId = allState.cr_correlation_id || runState.cr_correlation_id || "";
  const crStatus = allState.cr_status || runState.cr_status || "";
  const serviceLookup = allState.cr_service_lookup_status || runState.cr_service_lookup_status || "";
  const ciLinks = allState.task_ci_link_count ?? runState.task_ci_link_count ?? 0;
  const taskCount = allState.change_task_count ?? runState.change_task_count ?? 0;
  const lifecycleStates = allState.cr_lifecycle_states || runState.cr_lifecycle_states || [];
  const requestBody = allState.cr_request_body || runState.cr_request_body || {};
  const snResponse = allState.servicenow_response || runState.servicenow_response || null;
  const envelope = allState.broker_request_envelope || runState.broker_request_envelope || {};
  const linkErr = allState.last_cr_link_error || runState.last_cr_link_error || "";
  const lcErr = allState.last_cr_lifecycle_error || runState.last_cr_lifecycle_error || "";
  const elapsedMs = timing?.elapsed_ms;

  const STAGES = ["assess", "authorize", "scheduled", "implemented", "review", "closed"];
  const stageIdx = (s) => STAGES.indexOf(s);
  const currentStageIdx = lifecycleStates.reduce((max, s) => Math.max(max, stageIdx(s)), -1);
  const finalStage = crStatus && stageIdx(crStatus) > currentStageIdx ? crStatus : (lifecycleStates[lifecycleStates.length - 1] || "");
  const finalIdx = stageIdx(finalStage);
  const stagesShown = STAGES.slice(0, Math.max(finalIdx + 1, 4));

  const shortDesc = requestBody.short_description || "";
  const ciLinksRef = (() => {
    // Extract parent link from envelope
    const sn = envelope.data?.servicenow;
    if (Array.isArray(sn) && sn[0]?.parent?.value) return sn[0].parent.value;
    return "";
  })();
  const snParentLink = (() => {
    const sn = envelope.data?.servicenow;
    if (Array.isArray(sn) && sn[0]?.parent?.link) return sn[0].parent.link;
    return "";
  })();

  const VERDICT = crId && finalStage === "implemented"
    ? { color: "var(--ok)", bg: "var(--ok-dim)", icon: "✓", label: "CHANGE REQUEST IMPLEMENTED" }
    : crId
    ? { color: "var(--info)", bg: "rgba(122,162,247,.12)", icon: "📋", label: "CHANGE REQUEST CREATED" }
    : linkErr || lcErr
    ? { color: "var(--err)", bg: "rgba(239,106,106,.12)", icon: "✗", label: "CR FAILED" }
    : { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: "NO CR" };

  return (
    <div data-panel-id="create_change_request" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        ServiceNow Change Mgmt · cve_rem.create_change_request (Nautilus broker)
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      {/* Hero */}
      <div style={{ padding: "18px 22px", borderRadius: 10, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 48, height: 48, borderRadius: 24, background: VERDICT.color + "22", border: "1px solid " + VERDICT.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22, color: VERDICT.color, fontWeight: 700 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: VERDICT.color }}>{VERDICT.label}</div>
          {crId && <div className="mono" style={{ fontSize: 14, color: "var(--fg-0)", fontWeight: 600, letterSpacing: ".02em" }}>{crId}</div>}
          {shortDesc && <div style={{ fontSize: 11.5, color: "var(--fg-2)" }}>{shortDesc.length > 90 ? shortDesc.slice(0, 90) + "…" : shortDesc}</div>}
        </div>
        {finalStage && (
          <span style={{ background: VERDICT.color + "22", color: VERDICT.color, fontSize: 11, padding: "4px 12px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap", textTransform: "uppercase" }}>{finalStage}</span>
        )}
      </div>

      {/* Lifecycle stepper */}
      {(lifecycleStates.length > 0 || finalStage) && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, padding: "14px 16px" }}>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, marginBottom: 12 }}>change lifecycle</div>
          <div style={{ display: "flex", alignItems: "center", gap: 0, flexWrap: "wrap" }}>
            {stagesShown.map((stage, i) => {
              const stageIdxThis = stageIdx(stage);
              const completed = stageIdxThis <= finalIdx;
              const isCurrent = stageIdxThis === finalIdx;
              const color = completed ? (isCurrent ? "var(--info)" : "var(--ok)") : "var(--fg-3)";
              return (
                <React.Fragment key={stage}>
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 4, flex: "0 0 auto" }}>
                    <div style={{
                      width: 28, height: 28, borderRadius: 14,
                      background: completed ? color + "22" : "rgba(255,255,255,.03)",
                      border: "2px solid " + (completed ? color : "var(--line-2)"),
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 11, color, fontWeight: 700,
                    }}>
                      {completed ? (isCurrent ? "●" : "✓") : (i + 1)}
                    </div>
                    <span style={{ fontSize: 10.5, color, fontWeight: isCurrent ? 600 : 400, textTransform: "uppercase", letterSpacing: ".04em" }}>{stage}</span>
                  </div>
                  {i < stagesShown.length - 1 && (
                    <div style={{ flex: 1, height: 2, background: stageIdx(stagesShown[i + 1]) <= finalIdx ? "var(--ok)" : "var(--line-2)", margin: "0 6px", marginBottom: 18, minWidth: 12 }} />
                  )}
                </React.Fragment>
              );
            })}
          </div>
        </div>
      )}

      {/* Counts grid */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 10 }}>
        <div style={{ padding: "12px 14px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, textAlign: "center" }}>
          <div className="mono" style={{ fontSize: 22, color: "var(--accent)", fontWeight: 700, lineHeight: 1 }}>{taskCount}</div>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 5 }}>change tasks</div>
        </div>
        <div style={{ padding: "12px 14px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, textAlign: "center" }}>
          <div className="mono" style={{ fontSize: 22, color: "var(--ok)", fontWeight: 700, lineHeight: 1 }}>{ciLinks}</div>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 5 }}>CI links</div>
        </div>
        <div style={{ padding: "12px 14px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6, textAlign: "center" }}>
          <div className="mono" style={{ fontSize: 12, color: serviceLookup === "resolved_live" ? "var(--ok)" : "var(--warn)", fontWeight: 600, lineHeight: 1.2 }}>{serviceLookup || "—"}</div>
          <div style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginTop: 5 }}>service lookup</div>
        </div>
      </div>

      {/* CR description preview */}
      {requestBody.description && (
        <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8 }}>
          <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>cr description</div>
          <pre style={{ padding: 14, fontSize: 11.5, color: "var(--fg-1)", whiteSpace: "pre-wrap", margin: 0, fontFamily: "var(--mono)", maxHeight: 200, overflowY: "auto", lineHeight: 1.6 }}>{requestBody.description.length > 800 ? requestBody.description.slice(0, 800) + "…" : requestBody.description}</pre>
        </div>
      )}

      {/* ServiceNow parent link */}
      {(snParentLink || ciLinksRef) && (
        <div style={{ padding: "11px 13px", background: "rgba(122,162,247,.06)", border: "1px solid rgba(122,162,247,.22)", borderRadius: 6, display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 14 }}>🔗</span>
          <div style={{ display: "flex", flexDirection: "column", gap: 2, flex: 1, minWidth: 0 }}>
            <span style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>ServiceNow parent record</span>
            {snParentLink ? (
              <a href={snParentLink} target="_blank" rel="noopener noreferrer" className="mono" style={{ fontSize: 11, color: "var(--info)", wordBreak: "break-all", textDecoration: "none" }}>{snParentLink}</a>
            ) : (
              <span className="mono" style={{ fontSize: 11, color: "var(--fg-1)", wordBreak: "break-all" }}>{ciLinksRef}</span>
            )}
          </div>
        </div>
      )}

      {/* SN response (collapsible) */}
      {snResponse && (
        <details style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8 }}>
          <summary style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, cursor: "pointer", listStyle: "none", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>servicenow response</span>
            <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--ok)" }}>▸ expand</span>
          </summary>
          <pre className="mono" style={{ padding: 14, fontSize: 10.5, color: "var(--fg-0)", whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0, borderTop: "1px solid var(--line-1)", maxHeight: 240, overflowY: "auto", lineHeight: 1.5 }}>
            {typeof snResponse === "string" ? snResponse : JSON.stringify(snResponse, null, 2)}
          </pre>
        </details>
      )}

      {linkErr && (
        <div style={{ padding: 10, background: "rgba(239,106,106,.12)", border: "1px solid var(--err)55", borderRadius: 6, fontSize: 12 }}>
          <span style={{ color: "var(--err)", fontWeight: 600 }}>CR link error</span>: <span className="mono" style={{ color: "var(--fg-0)" }}>{linkErr}</span>
        </div>
      )}
      {lcErr && (
        <div style={{ padding: 10, background: "rgba(239,106,106,.12)", border: "1px solid var(--err)55", borderRadius: 6, fontSize: 12 }}>
          <span style={{ color: "var(--err)", fontWeight: 600 }}>lifecycle error</span>: <span className="mono" style={{ color: "var(--fg-0)" }}>{lcErr}</span>
        </div>
      )}
    </div>
  );
}

// ─── WriteRetrospectivePanel (FR-P5) — custom grid ──────────────────────

function WriteRetrospectivePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId="write_retrospective" />;

  const failureSignals = runState.retro_failure_signals || [];
  const preventionSuggestions = runState.retro_prevention_suggestions || [];

  return (
    <div data-panel-id="write_retrospective" className="nv-grid">
      <PanelCard title="retrospective" className="span-1">
        <KV pairs={[
          ["retro ID", runState.retro_id, { field: "retro_id" }],
          ["outcome", runState.retro_outcome],
          ["artifact", runState.retro_payload_artifact_ref],
        ]} />
      </PanelCard>

      <PanelCard title="prior context" className="span-2">
        <KV pairs={[
          ["prior count", runState.prior_retro_count],
          ["outcomes", Array.isArray(runState.prior_retro_outcomes) ? runState.prior_retro_outcomes.join(", ") : runState.prior_retro_outcomes],
          ["retrieval", runState.prior_retro_retrieval_status],
          ["mode", runState.prior_retro_retrieval_mode],
        ]} />
      </PanelCard>

      <PanelCard title="failure signals" className="span-3" data-table="failure_signals" right={<span className="muted mono">{failureSignals.length}</span>}>
        {failureSignals.length > 0 ? (
          <DataTable headers={["Kind", "Detail", "Evidence"]} rows={failureSignals.map((s, i) => (
            <tr key={i}>
              <td style={{ padding: "6px 8px" }}>{cellText(s.kind)}</td>
              <td style={{ padding: "6px 8px" }}>{cellText(s.detail)}</td>
              <td style={{ padding: "6px 8px" }} className="mono muted">{cellText(s.evidence)}</td>
            </tr>
          ))} />
        ) : <div className="muted">no failure signals recorded</div>}
      </PanelCard>

      <PanelCard title="prevention suggestions" className="span-3" data-table="prevention_suggestions" right={<span className="muted mono">{preventionSuggestions.length}</span>}>
        {preventionSuggestions.length > 0 ? (
          <DataTable headers={["Category", "Suggestion", "Rationale", "Cited Signals", "Conf"]} rows={preventionSuggestions.map((s, i) => (
            <tr key={i}>
              <td style={{ padding: "6px 8px" }}>{cellText(s.category)}</td>
              <td style={{ padding: "6px 8px" }}>{cellText(s.suggestion)}</td>
              <td style={{ padding: "6px 8px" }} className="muted">{cellText(s.rationale)}</td>
              <td style={{ padding: "6px 8px" }} className="mono">{Array.isArray(s.cited_signals) ? s.cited_signals.map(cellText).join(", ") : cellText(s.cited_signals)}</td>
              <td style={{ padding: "6px 8px" }} className="mono">{s.confidence_bp != null ? (s.confidence_bp / 100).toFixed(0) + "%" : ""}</td>
            </tr>
          ))} />
        ) : <div className="muted">no prevention suggestions</div>}
      </PanelCard>

      {runState.retro_failure_analysis && (
        <PanelCard title="failure analysis" className="span-3">
          <MarkdownView source={runState.retro_failure_analysis} />
        </PanelCard>
      )}

      {runState.last_retro_error && <ErrorBanner field="retro" value={runState.last_retro_error} />}
      {runState.retro_analysis_error && <ErrorBanner field="analysis" value={runState.retro_analysis_error} />}
    </div>
  );
}

// ─── KrakntrustAttestPanel (FR-P6) — ToolView style ────────────────────

function KrakntrustAttestPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="tool" lifecycle={lifecycle} panelId="krakntrust_attest" />;

  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const jws = allState.run_attestation_jws || runState.run_attestation_jws || "";
  const keyId = allState.krakntrust_key_id || runState.krakntrust_key_id || "";
  const bootSession = allState.boot_session_id || runState.boot_session_id || "";
  const artifactRef = allState.run_attestation_artifact_ref || runState.run_attestation_artifact_ref || "";
  const attachSysId = allState.run_attestation_attachment_sys_id || runState.run_attestation_attachment_sys_id || "";
  const doctrineHash = allState.doctrine_manifest_hash || runState.doctrine_manifest_hash || "";
  const promptArt = allState.prompt_artifact_id || runState.prompt_artifact_id || "";
  const cveId = allState.cve_id || runState.cve_id || "";
  const planHash = allState.plan_hash || runState.plan_hash || "";
  const retroId = allState.retro_id || runState.retro_id || "";
  const verifyOutcome = allState.verify_outcome || runState.verify_outcome || "";
  const runId = allState.run_id || runState.run_id || "";
  const crSysId = (() => {
    const sn = allState.servicenow_response || runState.servicenow_response;
    if (sn?.result?.sys_id) return sn.result.sys_id;
    return "";
  })();
  const err = allState.last_attestation_error || runState.last_attestation_error || "";
  const elapsedMs = timing?.elapsed_ms;

  const digestMatch = artifactRef.match(/([a-f0-9]{64})/);
  const digest = digestMatch ? digestMatch[1] : "";
  const digestShort = digest ? digest.slice(0, 12) + "…" + digest.slice(-6) : "";

  // JWS decode: header.payload.signature
  const parts = jws.split(".");
  const sigLen = parts[2] ? parts[2].length : 0;
  let header = null, payload = null;
  try {
    if (parts.length === 3) {
      header = JSON.parse(atob(parts[0].replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - parts[0].length % 4) % 4)));
      payload = JSON.parse(atob(parts[1].replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - parts[1].length % 4) % 4)));
    }
  } catch { /* malformed */ }

  const VERDICT = jws && !err
    ? { color: "var(--ok)", bg: "var(--ok-dim)", icon: "🛡", label: "RUN ATTESTATION SIGNED", chip: "ED25519 · EdDSA" }
    : err
    ? { color: "var(--err)", bg: "rgba(239,106,106,.12)", icon: "✗", label: "ATTESTATION FAILED", chip: "" }
    : { color: "var(--fg-3)", bg: "rgba(255,255,255,.04)", icon: "·", label: "NOT ATTESTED", chip: "" };

  const issuedAt = payload?.iat ? new Date(payload.iat * 1000).toISOString().replace("T", " ").replace(/\..+/, " UTC") : "";

  // The 12 signed claims (from KrakntrustAttestNode impl)
  const CLAIMS = [
    { key: "iss",                    val: payload?.iss || keyId || "",      desc: "Issuer — Krakntrust key id",                color: "var(--accent)" },
    { key: "sub",                    val: payload?.sub || crSysId || "",    desc: "Subject — CR sys_id (or run_id fallback)",  color: "var(--info)"   },
    { key: "kid",                    val: payload?.kid || keyId || "",      desc: "Key id — pubkey resolution hint",            color: "var(--accent)" },
    { key: "iat",                    val: issuedAt || (payload?.iat ? String(payload.iat) : ""), desc: "Issued at (Unix → ISO)",          color: "var(--fg-1)"   },
    { key: "boot_session_id",        val: payload?.boot_session_id || bootSession || "", desc: "Tamper-evident process-start nonce", color: "var(--warn)"   },
    { key: "run_id",                 val: payload?.run_id || runId || "",   desc: "Workflow run id (run-graph:cv-…)",          color: "var(--fg-1)"   },
    { key: "cve_id",                 val: payload?.cve_id || cveId || "",   desc: "Vulnerability under remediation",            color: "var(--err)"    },
    { key: "cr_sys_id",              val: payload?.cr_sys_id || crSysId || "", desc: "ServiceNow Change Request",               color: "var(--info)"   },
    { key: "prompt_artifact_id",     val: payload?.prompt_artifact_id || promptArt || "", desc: "LM system prompt content hash", color: "var(--info)"   },
    { key: "doctrine_manifest_hash", val: payload?.doctrine_manifest_hash || doctrineHash || "", desc: "Rule-pack constitution hash", color: "var(--warn)" },
    { key: "plan_hash",              val: payload?.plan_hash || planHash || "", desc: "Remediation plan content hash",          color: "var(--accent)" },
    { key: "retro_id",               val: payload?.retro_id || retroId || "", desc: "Retrospective record id",                  color: "var(--info)"   },
    { key: "verify_outcome",         val: payload?.verify_outcome || verifyOutcome || "", desc: "Terminal verify state",          color: verifyOutcome === "patched" ? "var(--ok)" : "var(--warn)" },
  ];
  const filledClaims = CLAIMS.filter(c => c.val).length;

  return (
    <div data-panel-id="krakntrust_attest" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 16 }}>
      <div className="mono muted" style={{ fontSize: 11.5 }}>
        Krakntrust attestation · Ed25519 JWS · doctrine + boot-session bound · cve_rem.krakntrust_attest
        {elapsedMs != null && <> · {(elapsedMs / 1000).toFixed(2)}s</>}
      </div>

      <div style={{ padding: "16px 20px", borderRadius: 10, background: VERDICT.bg, border: "1px solid " + VERDICT.color + "55", display: "flex", alignItems: "center", gap: 18 }}>
        <div style={{ width: 48, height: 48, borderRadius: 24, background: VERDICT.color + "22", border: "1px solid " + VERDICT.color + "66", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 22 }}>{VERDICT.icon}</div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 700, letterSpacing: ".07em", color: VERDICT.color }}>{VERDICT.label}</div>
          <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{jws ? `${filledClaims}/${CLAIMS.length} claims signed · ${jws.length}b raw JWS · ${sigLen}b signature` : err || "No JWS produced"}</div>
        </div>
        {VERDICT.chip && <span className="mono" style={{ background: VERDICT.color + "22", color: VERDICT.color, fontSize: 10.5, padding: "4px 11px", borderRadius: 12, fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap" }}>{VERDICT.chip}</span>}
      </div>

      {/* Key ceremony diagram (dev vs prod) */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>signing key ceremony</span>
          <span style={{ background: "var(--warn)22", color: "var(--warn)", fontSize: 9.5, padding: "2px 8px", borderRadius: 10, fontWeight: 700, letterSpacing: ".06em", textTransform: "none" }}>⚠ DEV MODE</span>
        </div>
        <div style={{ padding: "14px 16px", display: "grid", gridTemplateColumns: "1fr 28px 1fr", gap: 12, alignItems: "center" }}>
          {/* Active: single key */}
          <div style={{ padding: "12px 14px", border: "1px solid var(--ok)55", background: "var(--ok)11", borderRadius: 8, display: "flex", flexDirection: "column", gap: 5 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: 14 }}>🔑</span>
              <span style={{ fontSize: 10, color: "var(--ok)", fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" }}>ACTIVE · DEV</span>
            </div>
            <div className="mono" style={{ fontSize: 11, color: "var(--fg-0)", wordBreak: "break-all" }}>{keyId || "—"}</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)" }}>Single Ed25519 keypair on disk. Signs with one private key — auditor must trust the host where the key lives.</div>
          </div>
          <div style={{ textAlign: "center", color: "var(--fg-3)", fontFamily: "var(--mono)", fontSize: 14 }}>vs</div>
          {/* Production: Shamir 2-of-3 */}
          <div style={{ padding: "12px 14px", border: "1px dashed var(--line-3)", background: "rgba(255,255,255,.02)", borderRadius: 8, display: "flex", flexDirection: "column", gap: 5 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ fontSize: 14 }}>🔐</span>
              <span style={{ fontSize: 10, color: "var(--fg-2)", fontWeight: 700, letterSpacing: ".06em", textTransform: "uppercase" }}>PRODUCTION · SHAMIR 2-of-3</span>
            </div>
            <div className="mono" style={{ fontSize: 11, color: "var(--fg-2)" }}>root_key.threshold = 2/3</div>
            <div style={{ fontSize: 10.5, color: "var(--fg-3)" }}>Root key split via Shamir Secret Sharing — any 2 of 3 shareholders required. CRITERIA fancy #8.</div>
          </div>
        </div>
      </div>

      {/* Signed claims table — the meat */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>signed claims · {filledClaims}/{CLAIMS.length}</span>
          <span className="mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--fg-3)" }}>jws.payload</span>
        </div>
        <div>
          {CLAIMS.map((c, i) => {
            const present = !!c.val;
            const display = c.val ? (typeof c.val === "string" && c.val.length > 56 ? c.val.slice(0, 26) + "…" + c.val.slice(-12) : String(c.val)) : "";
            return (
              <div key={c.key} style={{
                display: "grid", gridTemplateColumns: "16px 160px 1fr", gap: 12, padding: "9px 14px",
                borderBottom: i < CLAIMS.length - 1 ? "1px solid var(--line-1)" : "none",
                alignItems: "start",
              }}>
                <span style={{ color: present ? "var(--ok)" : "var(--fg-3)", fontFamily: "var(--mono)", fontSize: 11, marginTop: 1 }}>{present ? "✓" : "·"}</span>
                <div>
                  <div className="mono" style={{ fontSize: 11.5, color: present ? c.color : "var(--fg-3)", fontWeight: 600 }}>{c.key}</div>
                  <div style={{ fontSize: 10, color: "var(--fg-3)", marginTop: 2 }}>{c.desc}</div>
                </div>
                <div className="mono" style={{ fontSize: 11, color: present ? "var(--fg-0)" : "var(--fg-3)", fontStyle: present ? "normal" : "italic", wordBreak: "break-all", lineHeight: 1.4 }}>{present ? display : "<unset>"}</div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Verifier CLI */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8, overflow: "hidden" }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>verifier CLI</div>
        <pre className="mono" style={{ padding: "12px 14px", margin: 0, fontSize: 11, color: "var(--fg-1)", lineHeight: 1.65, whiteSpace: "pre", overflowX: "auto" }}>
{`# walk the trust chain back to the pinned Krakntrust pubkey
$ harbor verify-cr `}<span style={{ color: "var(--info)" }}>{crSysId || "<cr_sys_id>"}</span>{`
  ✓ jws signature      EdDSA · `}<span style={{ color: "var(--accent)" }}>{keyId || "krakntrust-<key>"}</span>{`
  ✓ doctrine hash      `}<span style={{ color: "var(--warn)" }}>{doctrineHash ? doctrineHash.slice(0, 16) + "…" : "<hash>"}</span>{`
  ✓ boot session       `}<span style={{ color: "var(--warn)" }}>{bootSession ? bootSession.slice(0, 16) + "…" : "<session>"}</span>{`
  ✓ run trace          run-graph:cv-…
  ⚠ key mode           single-key (dev) — `}<span style={{ color: "var(--info)" }}>not Shamir 2-of-3</span></pre>
      </div>

      {/* Coverage matrix */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8 }}>
        <div style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, borderBottom: "1px solid var(--line-1)" }}>coverage matrix</div>
        <div style={{ padding: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          {[
            { label: "JWS produced",          ok: !!jws,           dev: false },
            { label: "Artifact persisted",    ok: !!artifactRef,   dev: false },
            { label: "SN attachment uploaded", ok: !!attachSysId,  dev: false },
            { label: "Doctrine hash bound",   ok: !!doctrineHash,  dev: false },
            { label: "Boot session bound",    ok: !!bootSession,   dev: false },
            { label: "Prompt artifact bound", ok: !!promptArt,     dev: false },
            { label: "Shamir 2-of-3 root",    ok: false,           dev: true  },
            { label: "Hardware HSM",          ok: false,           dev: true  },
          ].map((r) => (
            <div key={r.label} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 8px", background: r.ok ? "var(--ok)08" : r.dev ? "rgba(245,181,74,.06)" : "rgba(239,106,106,.06)", borderRadius: 4, fontSize: 11 }}>
              <span style={{ color: r.ok ? "var(--ok)" : r.dev ? "var(--warn)" : "var(--err)", fontWeight: 700, width: 14 }}>{r.ok ? "✓" : r.dev ? "○" : "✗"}</span>
              <span style={{ color: "var(--fg-1)", flex: 1 }}>{r.label}</span>
              {r.dev && <span style={{ fontSize: 9, color: "var(--warn)", textTransform: "uppercase", letterSpacing: ".06em" }}>prod-only</span>}
            </div>
          ))}
        </div>
      </div>

      {/* Artifact + SN */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <span style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em" }}>artifact (blake3)</span>
            {artifactRef && (
              <a href={window.apiUrl("/watch/api/artifact?ref=" + encodeURIComponent(artifactRef))} download={`run_attestation_${cveId || "cve"}.jws`} style={{ color: "var(--info)", fontSize: 10, textDecoration: "none" }}>download ↓</a>
            )}
          </div>
          <div className="mono" style={{ fontSize: 11.5, color: "var(--fg-0)", fontWeight: 600 }}>{digestShort || "—"}</div>
          <div className="mono" style={{ fontSize: 10, color: "var(--fg-3)", marginTop: 3, wordBreak: "break-all" }}>{artifactRef || "—"}</div>
        </div>
        <div style={{ padding: "11px 13px", background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
          <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>ServiceNow attachment</div>
          <div className="mono" style={{ fontSize: 11.5, color: attachSysId ? "var(--fg-0)" : "var(--fg-3)", fontWeight: attachSysId ? 600 : 400, wordBreak: "break-all" }}>{attachSysId || "not uploaded"}</div>
          <div style={{ fontSize: 10, color: "var(--fg-3)", marginTop: 3 }}>{attachSysId ? `attached as run_attestation_${cveId || "cve"}.jws` : "SERVICENOW_BASE_URL unset or CR missing"}</div>
        </div>
      </div>

      {/* Raw JWS collapsible */}
      {jws && (
        <details style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 8 }}>
          <summary style={{ padding: "9px 14px", fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600, cursor: "pointer", listStyle: "none", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <span>raw jws · header.payload.signature</span>
            <button
              onClick={(e) => { e.preventDefault(); try { navigator.clipboard.writeText(jws); } catch {} }}
              style={{ background: "var(--accent)22", color: "var(--accent)", border: "1px solid var(--accent)44", padding: "2px 10px", borderRadius: 3, fontSize: 10.5, fontFamily: "var(--mono)", cursor: "pointer", letterSpacing: ".04em" }}
            >COPY</button>
          </summary>
          <pre className="mono" style={{ padding: 12, fontSize: 10, color: "var(--fg-1)", wordBreak: "break-all", whiteSpace: "pre-wrap", margin: 0, maxHeight: 160, overflowY: "auto", lineHeight: 1.5, borderTop: "1px solid var(--line-1)" }}>{jws}</pre>
        </details>
      )}

      <div style={{ fontSize: 11, color: "var(--fg-3)", fontStyle: "italic", padding: "10px 13px", background: "rgba(122,162,247,.05)", border: "1px solid rgba(122,162,247,.18)", borderRadius: 6 }}>
        Tamper-evident: changing any signed claim invalidates the signature. Verifier resolves <span className="mono" style={{ color: "var(--accent)" }}>kid</span> → pinned Krakntrust pubkey, checks EdDSA signature, then walks <span className="mono" style={{ color: "var(--warn)" }}>doctrine_manifest_hash</span> + <span className="mono" style={{ color: "var(--warn)" }}>boot_session_id</span> back to the source manifests.
      </div>

      {err && (
        <div style={{ padding: 10, background: "rgba(239,106,106,.12)", border: "1px solid var(--err)55", borderRadius: 6, fontSize: 12 }}>
          <span style={{ color: "var(--err)", fontWeight: 600 }}>attestation error</span>: <span className="mono" style={{ color: "var(--fg-0)" }}>{err}</span>
        </div>
      )}
    </div>
  );
}

// ─── DriftWatchSpawnPanel (FR-P7) — ToolView style ──────────────────────

function DriftWatchSpawnPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId="drift_watch_spawn" />;

  const driftEvents = runState.drift_events || [];

  return (
    <div data-panel-id="drift_watch_spawn" className="nv-grid">
      <PanelCard title="child run" className="span-1">
        {runState.drift_child_run_id ? (
          <div>
            <a data-field="drift_child_run_id" href={"/watch/?run=" + runState.drift_child_run_id} target="_blank" rel="noopener noreferrer" className="mono" style={{ color: "var(--accent)", textDecoration: "none" }}>{runState.drift_child_run_id}</a>
          </div>
        ) : <div className="muted" data-empty="drift-not-spawned">not yet spawned</div>}
      </PanelCard>

      <PanelCard title="configuration" className="span-2">
        <KV pairs={[
          ["spawn path", runState.drift_spawn_path],
          ["watch window", runState.drift_watch_window_hours != null ? runState.drift_watch_window_hours + "h" : null],
        ]} />
      </PanelCard>

      {driftEvents.length > 0 && (
        <PanelCard title="drift events" className="span-3" right={<span className="muted mono">{driftEvents.length}</span>} scroll>
          <ul className="retrieved">{driftEvents.map((ev, i) => (
            <li key={i}><span className="retrieved-path">{typeof ev === "string" ? ev : JSON.stringify(ev)}</span></li>
          ))}</ul>
        </PanelCard>
      )}

      {runState.last_drift_spawn_error && <ErrorBanner field="drift spawn" value={runState.last_drift_spawn_error} />}
    </div>
  );
}

// ─── CargonetFamilyPanel (FR-F-CARGONET) — ToolView per-node ────────────

const CARGONET_DIAGNOSTIC_FIELDS_FULL = [
  { key: "cargonet_lab_ref", label: "Lab ref" },
  { key: "cargonet_proxy_ref", label: "Proxy ref" },
  { key: "cargonet_node_count", label: "Node count" },
  { key: "cargonet_correlation_map", label: "Correlation map" },
  { key: "last_cargonet_error", label: "Error", danger: true },
  { key: "cargonet_writeback_done", label: "Writeback done" },
  { key: "verify_probe_method", label: "Probe method" },
];

function CargonetFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : status === "failed" ? "failed" : null;

  switch (node.id) {
    case "cargonet_lab_telemetry": {
      if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId={node.id} />;
      const toolCallEvents = events.filter(e => e.type === "tool_call");
      const deltaFields = delta && delta.fields ? Object.keys(delta.fields).filter(k => delta.fields[k] != null) : [];

      if (deltaFields.length === 0 && toolCallEvents.length === 0 && status === "done") {
        const diagPairs = CARGONET_DIAGNOSTIC_FIELDS_FULL.map(f => [f.label, runState[f.key]]).filter(([,v]) => v != null && v !== "" && v !== false);
        if (diagPairs.length === 0) return <div data-panel-id={node.id}><div className="muted" data-empty="no-telemetry">no lab telemetry returned</div></div>;
        return <div data-panel-id={node.id}><PanelCard title="cargonet diagnostics"><KV pairs={diagPairs} /></PanelCard></div>;
      }

      return (
        <div data-panel-id={node.id} className="nv-grid">
          {deltaFields.length > 0 && (
            <PanelCard title="delta fields" className="span-3" data-field="per_host_verify">
              <DataTable headers={["Field", "Value"]} rows={deltaFields.map(k => (
                <tr key={k}><td style={{ padding: "6px 8px" }} className="mono">{k}</td><td style={{ padding: "6px 8px" }} className="mono">{typeof delta.fields[k] === "object" ? JSON.stringify(delta.fields[k]) : String(delta.fields[k])}</td></tr>
              ))} />
            </PanelCard>
          )}
          {toolCallEvents.length > 0 && (
            <PanelCard title="tool calls" className="span-3" right={<span className="muted mono">{toolCallEvents.length}</span>}>
              <DataTable headers={["Tool", "Status"]} rows={toolCallEvents.map((e, i) => (
                <tr key={i}><td style={{ padding: "6px 8px" }} className="mono">{e.tool || e.name || ""}</td><td style={{ padding: "6px 8px" }}>{e.status || e.result || ""}</td></tr>
              ))} />
            </PanelCard>
          )}
        </div>
      );
    }

    case "emit_sandbox_evidence": {
      const artifactRef = runState.sandbox_evidence_artifact_ref;
      const artifactEvents = events.filter(e => e.type === "artifact_written");
      if (!artifactRef && artifactEvents.length === 0) return <div data-panel-id={node.id}><PendingState family="artifact" lifecycle="done_empty" panelId={node.id} /></div>;

      return (
        <div data-panel-id={node.id} className="nv-grid">
          <PanelCard title="evidence artifact" className="span-3">
            <KV pairs={[["artifact ref", artifactRef, { field: "sandbox_evidence_artifact_ref" }]]} />
          </PanelCard>
          {artifactEvents.length > 0 && (
            <PanelCard title="artifacts written" className="span-3" right={<span className="muted mono">{artifactEvents.length}</span>}>
              <DataTable headers={["Hash", "Size", "MIME", "Provenance"]} rows={artifactEvents.map((e, i) => (
                <tr key={i}><td style={{ padding: "6px 8px", fontSize: 11 }} className="mono">{e.hash || ""}</td><td style={{ padding: "6px 8px" }}>{e.size || ""}</td><td style={{ padding: "6px 8px" }}>{e.mime || e.content_type || ""}</td><td style={{ padding: "6px 8px" }}>{e.provenance || ""}</td></tr>
              ))} />
            </PanelCard>
          )}
        </div>
      );
    }

    case "cargonet_writeback": {
      const done = runState.cargonet_writeback_done;
      const error = runState.last_cargonet_error;
      return (
        <div data-panel-id={node.id}>
          <PanelCard title="writeback" data-field="writeback_status">
            <div className="bigstat">
              <div className={"bigstat-v " + (done === true ? "ok" : "")}>{done === true ? "done" : "pending"}</div>
              <div className="bigstat-k">writeback status</div>
            </div>
            {runState.cargonet_lab_ref && <div style={{ marginTop: 8 }}><span className="muted">target: </span><span className="mono">{runState.cargonet_lab_ref}</span></div>}
          </PanelCard>
          {error && <ErrorBanner value={error} />}
        </div>
      );
    }

    default:
      return <div data-panel-id={node.id}><div className="muted">Unknown cargonet node: {node.id}</div></div>;
  }
}

// ─── GateFamilyPanel (FR-F1) — DecisionView variant ─────────────────────

function GateFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="gate" lifecycle={lifecycle} panelId={node.id} />;

  let passed = false;
  let reason = "";
  if (node.id === "halt_new_gate") { passed = runState.halt_new_active === false; reason = runState.halt_reason || ""; }
  else if (node.id === "plan_quarantine_gate") { passed = runState.plan_quarantined === false; reason = runState.plan_quarantine_reason || ""; }
  else if (node.id === "divergence_quarantine") { passed = !runState.gepa_divergence_record_id; reason = runState.gepa_divergence_record_id ? ("divergence record: " + runState.gepa_divergence_record_id) : ""; }

  return (
    <div data-panel-id={node.id} className="nv-grid">
      <PanelCard title="verdict" className="span-1">
        <div className="bigstat">
          <div className={"bigstat-v " + (passed ? "ok" : "fail")}>{passed ? "passed" : "halted"}</div>
          <div className="bigstat-k">gate</div>
        </div>
      </PanelCard>

      <PanelCard title="detail" className="span-2">
        {reason && <p style={{ margin: "0 0 8px", color: "var(--fg-1)" }}>{reason}</p>}
        <KV pairs={[
          ["halt_new_active", runState.halt_new_active != null ? String(runState.halt_new_active) : null],
          ["plan_quarantined", runState.plan_quarantined != null ? String(runState.plan_quarantined) : null],
          ["divergence_record", runState.gepa_divergence_record_id],
        ]} />
      </PanelCard>

      {status === "failed" && <ErrorBanner value={emptyCopy("gate", "failed")} />}
    </div>
  );
}

// ─── DecisionFamilyPanel (FR-F3) — DecisionView ─────────────────────────

function DecisionFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="decision" lifecycle={lifecycle} panelId={node.id} />;

  const transitionEvent = events.find(e => e.type === "transition");
  const decision = transitionEvent ? (transitionEvent.to_node || "transition recorded") : null;

  const DECISION_INPUTS = {
    source_trust_gate: [["source trust", runState.source_trust], ["source class", runState.source_class]],
    ssvc_evaluate: [["SSVC tier", runState.ssvc_tier]],
    sandbox_dispatch: [["sandbox runtime", runState.sandbox_runtime]],
    suppress_not_applicable: [["disposition", runState.disposition]],
  };

  const inputs = DECISION_INPUTS[node.id] || [];

  return (
    <div data-panel-id={node.id} className="nv-grid">
      <PanelCard title="evaluated to" className="span-1">
        <div className="bigstat">
          <div className="bigstat-v ok">{decision || "—"}</div>
          <div className="bigstat-k">decision</div>
        </div>
      </PanelCard>

      <PanelCard title="inputs" className="span-2">
        <KV pairs={inputs.filter(([,v]) => v != null)} />
      </PanelCard>
    </div>
  );
}

// ─── TransformFamilyPanel (FR-F4) — delta display ───────────────────────

function TransformFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId={node.id} />;

  const deltaFields = delta && delta.fields ? Object.keys(delta.fields).filter(k => delta.fields[k] != null) : [];

  return (
    <div data-panel-id={node.id} className="nv-grid">
      {deltaFields.length > 0 && (
        <PanelCard title="changed fields" className="span-3">
          <DataTable headers={["Field", "Value"]} rows={deltaFields.map(k => {
            const val = delta.fields[k];
            const strVal = typeof val === "string" ? val : JSON.stringify(val);
            return <tr key={k}><td style={{ padding: "6px 8px" }} className="mono">{k}</td><td style={{ padding: "6px 8px" }} className="mono">{strVal.length > 120 ? strVal.slice(0, 120) + "…" : strVal}</td></tr>;
          })} />
        </PanelCard>
      )}

      {node.id.startsWith("canonicalize_") && runState.canonical_body != null && (
        <PanelCard title="canonical output" className="span-3">
          <KV pairs={[
            ["body length", typeof runState.canonical_body === "string" ? runState.canonical_body.length : JSON.stringify(runState.canonical_body).length],
            ["injection class", runState.injection_class],
          ]} />
        </PanelCard>
      )}

      {node.id.startsWith("enrich_") && runState.extract != null && (
        <PanelCard title="extract summary" className="span-3" mono>
          <pre className="code" style={{ whiteSpace: "pre-wrap" }}>{typeof runState.extract === "string" ? runState.extract : JSON.stringify(runState.extract, null, 2)}</pre>
        </PanelCard>
      )}
    </div>
  );
}

// ─── LlmFamilyPanel (FR-F5) — Full LLMView ─────────────────────────────

function StreamingTokenBlock({ model, text }) {
  const ref = React.useRef(null);
  React.useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; });
  return (
    <PanelCard title="response" right={<span className="muted mono">{model || "LM"} · {text.length} chars</span>} mono>
      <pre ref={ref} className="code" style={{ whiteSpace: "pre-wrap", maxHeight: 400, overflow: "auto" }}>{text}<span className="caret">█</span></pre>
    </PanelCard>
  );
}

function LlmFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="llm" lifecycle={lifecycle} panelId={node.id} />;

  const tokenEvents = events.filter(e => e.type === "token");

  if (tokenEvents.length > 0) {
    const text = tokenEvents.sort((a, b) => a.index - b.index).map(e => e.token).join("");
    const model = tokenEvents[0].model;

    return (
      <div data-panel-id={node.id} className="nv-grid">
        <PanelCard title="model" className="span-1">
          <KV pairs={[
            ["model", model],
            ["tokens", tokenEvents.length],
          ]} />
        </PanelCard>

        <div className="span-2" />

        <div className="span-3">
          <StreamingTokenBlock model={model} text={text} />
        </div>
      </div>
    );
  }

  return (
    <div data-panel-id={node.id} className="nv-grid">
      {node.id === "code_writer" && (
        <>
          {runState.plan_rationale && (
            <PanelCard title="plan rationale" className="span-3">
              <p className="prose-clip" style={{ margin: 0 }}>{runState.plan_rationale}</p>
            </PanelCard>
          )}
          {runState.plan_spec && (
            <PanelCard title="plan spec" className="span-3" mono>
              <pre className="code" style={{ whiteSpace: "pre-wrap" }}>{typeof runState.plan_spec === "string" ? runState.plan_spec : JSON.stringify(runState.plan_spec, null, 2)}</pre>
            </PanelCard>
          )}
          <PanelCard title="output" className="span-3">
            <KV pairs={[
              ["runtime", runState.code_runtime],
              ["apply bundle", runState.apply_bundle_ref],
              ["rollback bundle", runState.rollback_bundle_ref],
              ["verify probe", runState.verify_probe_ref],
            ]} />
          </PanelCard>
        </>
      )}

      {node.id === "critic" && (
        <>
          {runState.critic_verdict && (
            <PanelCard title="verdict" className="span-1">
              <div className="bigstat">
                <div className={"bigstat-v " + (runState.critic_verdict === "approved" ? "ok" : "fail")}>{runState.critic_verdict}</div>
                <div className="bigstat-k">critic</div>
              </div>
            </PanelCard>
          )}
          {runState.critic_history && Array.isArray(runState.critic_history) && runState.critic_history.length > 0 && (
            <PanelCard title="latest critique" className="span-2" mono>
              <pre className="code" style={{ whiteSpace: "pre-wrap" }}>{typeof runState.critic_history[runState.critic_history.length - 1] === "string" ? runState.critic_history[runState.critic_history.length - 1] : JSON.stringify(runState.critic_history[runState.critic_history.length - 1], null, 2)}</pre>
            </PanelCard>
          )}
        </>
      )}

      {node.id === "judge_safety" && runState.judge_safety_verdict && (
        <PanelCard title="safety verdict" className="span-3">
          <div className="bigstat">
            <div className={"bigstat-v " + (runState.judge_safety_verdict === "safe" ? "ok" : "fail")}>{runState.judge_safety_verdict}</div>
            <div className="bigstat-k">safety</div>
          </div>
        </PanelCard>
      )}

      {node.id.startsWith("extract_") && runState.extract != null && (
        <PanelCard title="extract" className="span-3" mono>
          <pre className="code code-json" style={{ whiteSpace: "pre-wrap" }}>{typeof runState.extract === "string" ? runState.extract : JSON.stringify(runState.extract, null, 2)}</pre>
        </PanelCard>
      )}

      {node.id === "injection_classify" && runState.injection_class != null && (
        <PanelCard title="injection classification" className="span-3">
          <div className="bigstat">
            <div className={"bigstat-v " + (runState.injection_class === "clean" ? "ok" : "fail")}>{runState.injection_class}</div>
            <div className="bigstat-k">class</div>
          </div>
        </PanelCard>
      )}

      {status === "done" && !delta && (
        <PanelCard className="span-3"><div className="muted">{emptyCopy("llm", "done_empty")}</div></PanelCard>
      )}
    </div>
  );
}

// ─── AuditFamilyPanel (FR-F6) — ToolView lite ──────────────────────────

function AuditFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId={node.id} />;

  return (
    <div data-panel-id={node.id} className="nv-grid">
      <PanelCard title="audit" className="span-1">
        <div className="bigstat">
          <div className={"bigstat-v " + (runState.source_audit_written === true ? "ok" : "")}>{runState.source_audit_written === true ? "written" : "pending"}</div>
          <div className="bigstat-k">audit row</div>
        </div>
      </PanelCard>

      <PanelCard title="context" className="span-2">
        <KV pairs={[
          ["source trust", runState.source_trust],
          ["source class", runState.source_class],
          ["classifier ran", runState.source_classifier_ran != null ? String(runState.source_classifier_ran) : null],
          ["trust violation", runState.source_trust_violation != null ? String(runState.source_trust_violation) : null],
        ]} />
      </PanelCard>

      {runState.last_source_audit_error && <ErrorBanner field="audit" value={runState.last_source_audit_error} />}
    </div>
  );
}

// ─── ArtifactFamilyPanel (FR-F7) — ToolView lite ────────────────────────

const ARTIFACT_REF_FIELDS = {
  emit_quarantine_artifact: "quarantine_artifact_ref",
  emit_remediation_bundle: "remediation_bundle_artifact_ref",
  emit_evidence_bundle: "evidence_bundle_artifact_ref",
  emit_retro_payload: "retro_payload_artifact_ref",
  emit_docx_archive: "docx_artifact_ref",
  emit_proof_report: "proof_report_artifact_ref",
};

function ArtifactFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="artifact" lifecycle={lifecycle} panelId={node.id} />;

  const refField = ARTIFACT_REF_FIELDS[node.id];
  const refValue = refField ? runState[refField] : null;
  const artifactEvents = events.filter(e => e.type === "artifact_written");

  if (!refValue && artifactEvents.length === 0) return <PendingState family="artifact" lifecycle="done_empty" panelId={node.id} />;

  return (
    <div data-panel-id={node.id} className="nv-grid">
      {refValue && (
        <PanelCard title="artifact" className="span-3">
          <KV pairs={[["ref", refValue]]} />
        </PanelCard>
      )}
      {artifactEvents.length > 0 && (
        <PanelCard title="artifacts written" className="span-3" right={<span className="muted mono">{artifactEvents.length}</span>}>
          <DataTable headers={["Hash", "Size", "MIME", "Provenance"]} rows={artifactEvents.map((e, i) => (
            <tr key={i}><td style={{ padding: "6px 8px", fontSize: 11 }} className="mono">{e.hash || ""}</td><td style={{ padding: "6px 8px" }}>{e.size || ""}</td><td style={{ padding: "6px 8px" }}>{e.mime || e.content_type || ""}</td><td style={{ padding: "6px 8px" }}>{e.provenance || ""}</td></tr>
          ))} />
        </PanelCard>
      )}
    </div>
  );
}

// ─── HitlFamilyPanel (FR-F8) — Waiting room + verdict ──────────────────

function HitlFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const waitingEvent = events.find(e => e.type === "waiting_for_input");
  const prompt = waitingEvent && waitingEvent.payload ? waitingEvent.payload.prompt : null;
  const requestedCapability = waitingEvent ? (waitingEvent.requested_capability || (waitingEvent.payload && waitingEvent.payload.requested_capability)) : null;
  const response = runState.response || {};

  if (status === "pending") return <PendingState family="hitl" lifecycle="pending" panelId={node.id} />;

  if (status === "running" && !response.decision) {
    return (
      <div data-panel-id={node.id}>
        <div className="nv-pending">
          <div className="nv-pending-card">
            <div className="nv-pending-icon" style={{ borderColor: "var(--warn)", color: "var(--warn)" }}>⏳</div>
            <div className="nv-pending-title">waiting for human input</div>
            {prompt && <div className="nv-pending-sub">{prompt}</div>}
            {requestedCapability && <div className="nv-pending-sub">capability: {requestedCapability}</div>}
            {runState.hitl_blocked_at && <div className="nv-pending-sub" style={{ color: "var(--warn)" }}>blocked since {runState.hitl_blocked_at}</div>}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div data-panel-id={node.id} className="nv-grid">
      {response.decision && (
        <PanelCard title="verdict" className="span-1">
          <div className="bigstat">
            <div className={"bigstat-v " + (response.decision === "approve" ? "ok" : "fail")}>{response.decision}</div>
            <div className="bigstat-k">decision</div>
          </div>
          {response.actor && <div style={{ marginTop: 4 }} className="muted mono" style={{ fontSize: 11 }}>by {response.actor}</div>}
          {response.at && <div className="muted mono" style={{ fontSize: 11 }}>at {response.at}</div>}
        </PanelCard>
      )}

      {prompt && (
        <PanelCard title="prompt" className={response.decision ? "span-2" : "span-3"}>
          <p style={{ margin: 0, color: "var(--fg-1)" }}>{prompt}</p>
        </PanelCard>
      )}

      {requestedCapability && (
        <PanelCard title="capability" className="span-3">
          <KV pairs={[["required", requestedCapability]]} />
        </PanelCard>
      )}
    </div>
  );
}

// ─── BranchFamilyPanel (FR-F9) — DecisionView lite ──────────────────────

function BranchFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="branch" lifecycle={lifecycle} panelId={node.id} />;

  const transitions = events.filter(e => e.type === "transition" && e.from_node === node.id);
  const takenTargets = transitions.map(e => e.to_node).filter(Boolean);
  const ruleIds = transitions.map(e => e.rule_id).filter(Boolean);

  const BRANCH_CONTEXT = {
    source_trust_gate: { key: "source_trust", label: "Trust level" },
    branch_resp_ingest: { key: null, label: "HITL decision" },
    ssvc_evaluate: { key: "ssvc_tier", label: "SSVC tier" },
    suppress_not_applicable: { key: "disposition", label: "Disposition" },
    sandbox_dispatch: { key: "sandbox_runtime", label: "Runtime" },
  };

  const ctx = BRANCH_CONTEXT[node.id] || { key: null, label: null };
  const inputValue = ctx.key ? runState[ctx.key] : null;

  if (!takenTargets.length && !delta) return <PendingState family="branch" lifecycle="done_empty" panelId={node.id} />;

  return (
    <div data-panel-id={node.id} className="nv-grid">
      {(inputValue || ctx.label) && (
        <PanelCard title="routing input" className="span-1">
          <div className="bigstat">
            <div className="bigstat-v ok">{inputValue || "—"}</div>
            <div className="bigstat-k">{ctx.label || "decision"}</div>
          </div>
        </PanelCard>
      )}

      <PanelCard title="branches" className={inputValue ? "span-2" : "span-3"}>
        <ul className="branches">
          {takenTargets.map((target, i) => (
            <li key={i} className="branch is-taken">
              <span className="branch-l"><span className="branch-arrow">→</span><span className="branch-label">{target}</span></span>
              {ruleIds[i] && <span className="branch-r mono">{ruleIds[i]}</span>}
            </li>
          ))}
        </ul>
      </PanelCard>

      {delta && delta.fields && Object.keys(delta.fields).length > 0 && (
        <PanelCard title="state changes" className="span-3">
          <KV pairs={Object.entries(delta.fields).filter(([, v]) => v != null).map(([k, v]) => [k, typeof v === "object" ? JSON.stringify(v) : String(v)])} />
        </PanelCard>
      )}
    </div>
  );
}

// ─── AgentFamilyPanel (FR-F10) — AgentLoopView ──────────────────────────

function AgentFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="default" lifecycle={lifecycle} panelId={node.id} />;

  const toolCallEvents = events.filter(e => e.type === "tool_call");
  const toolResultEvents = events.filter(e => e.type === "tool_result");
  const tokenEvents = events.filter(e => e.type === "token");
  const tokenText = tokenEvents.map(e => e.token).join("");

  if (node.id === "planner") {
    const agentTrace = runState.planner_agent_trace || [];
    const verifierFindings = runState.planner_verifier_findings || [];
    const ragSources = runState.planner_rag_sources || [];

    return (
      <div data-panel-id={node.id} className="agent-grid">
        <div className="agent-strip">
          <div className="agent-strip-l">
            <span><span className="muted">latency</span> <b className="mono">{runState.planner_latency_ms != null ? runState.planner_latency_ms + "ms" : "—"}</b></span>
            <span className="sep">·</span>
            <span><span className="muted">retries</span> <b className="mono">{runState.planner_schema_retries || 0}</b></span>
            <span className="sep">·</span>
            <span><span className="muted">quality</span> <b className="mono">{runState.plan_quality_score_bp != null ? (runState.plan_quality_score_bp / 100).toFixed(0) + "%" : "—"}</b></span>
            <span className="sep">·</span>
            <span><span className="muted">tools</span> <b className="mono">{toolCallEvents.length}</b></span>
          </div>
          <div className="agent-strip-r">
            {runState.planner_verifier_passed != null && (
              <Pill tone={runState.planner_verifier_passed ? "ok" : "warn"}>{runState.planner_verifier_passed ? "verified" : "failed"}</Pill>
            )}
          </div>
        </div>

        <PanelCard title="thought stream" className="agent-thoughts" right={<span className="muted mono">{agentTrace.length} steps</span>}>
          <div className="thought-list">
            {agentTrace.map((row, i) => (
              <div key={i} className={"thought thought-" + (row.role === "assistant" ? "plan" : row.role === "user" ? "read" : "write")}>
                <div className="thought-meta">
                  <span className={"thought-tag thought-tag-" + (row.role === "assistant" ? "plan" : "read")}>{row.role || "step"}</span>
                  <span className="thought-time mono">#{i + 1}</span>
                </div>
                <div className="thought-text">{typeof row.content === "string" ? (row.content.length > 300 ? row.content.slice(0, 300) + "…" : row.content) : JSON.stringify(row.content).slice(0, 300)}</div>
              </div>
            ))}
          </div>
        </PanelCard>

        <PanelCard title={tokenText.length > 0 ? "response stream" : "output"} className="agent-code" mono>
          {tokenText.length > 0
            ? <pre className="codepane-text">{tokenText}<span className="caret">█</span></pre>
            : <pre className="codepane-text">{runState.plan_spec ? (typeof runState.plan_spec === "string" ? runState.plan_spec : JSON.stringify(runState.plan_spec, null, 2)) : emptyCopy("default", "done_empty")}</pre>
          }
        </PanelCard>

        <PanelCard title="RAG sources" className="agent-tests" right={<span className="muted mono">{ragSources.length}</span>}>
          <ul className="tests">
            {ragSources.map((s, i) => (
              <li key={i} className="test test-pass">
                <span className="test-status"><span className="test-dot test-dot-pass">✓</span></span>
                <span className="test-name">{typeof s === "string" ? s : JSON.stringify(s)}</span>
              </li>
            ))}
            {verifierFindings.map((f, i) => (
              <li key={"v" + i} className="test test-fail">
                <span className="test-status"><span className="test-dot test-dot-fail">!</span></span>
                <span className="test-name">{f.finding || f.message || JSON.stringify(f)}</span>
              </li>
            ))}
          </ul>
        </PanelCard>

        <PanelCard title="tool log" className="agent-logs" mono>
          <ul className="loglist">
            {toolCallEvents.map((c, i) => {
              const result = toolResultEvents.find(r => r.call_id === c.call_id);
              return <li key={i}>[{c.namespace}.{c.tool_name}] {result ? (result.ok ? "→ ok" : "→ " + (result.error || "err")) : "…"}</li>;
            })}
          </ul>
        </PanelCard>

        {runState.last_planner_error && <ErrorBanner field="planner" value={runState.last_planner_error} />}
      </div>
    );
  }

  if (node.id === "remediation_discovery") {
    const d = delta?.fields || {};
    const actions = d.recommended_actions || runState.recommended_actions || [];
    const provenance = d.recommendation_provenance || runState.recommendation_provenance || {};
    const vstatus = d.vulnerability_status || runState.vulnerability_status || "";
    const fixVer = d.fixed_version || "";
    const mitOnly = d.mitigation_only || runState.mitigation_only || false;
    const unpatch = d.unpatchable_disposition || runState.unpatchable_disposition || "";

    const STATUS = (() => {
      if (unpatch) return { color: "var(--err)", bg: "rgba(239,83,80,.12)", icon: "✖", label: "UNPATCHABLE", desc: unpatch };
      if (mitOnly) return { color: "var(--warn)", bg: "rgba(255,193,7,.12)", icon: "⚠", label: "MITIGATION ONLY", desc: "No upstream fix available — mitigation actions only." };
      if (vstatus === "downgrade_required") return { color: "var(--warn)", bg: "rgba(255,193,7,.12)", icon: "↓", label: "DOWNGRADE REQUIRED", desc: `Downgrade to ${fixVer}` };
      if (vstatus === "upgrade_required") return { color: "var(--ok)", bg: "var(--ok-dim)", icon: "↑", label: "UPGRADE REQUIRED", desc: `Upgrade to ${fixVer}` };
      if (actions.length > 0) return { color: "var(--ok)", bg: "var(--ok-dim)", icon: "✓", label: "FIX DISCOVERED", desc: `${actions.length} action${actions.length > 1 ? "s" : ""} found` };
      return { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)", icon: "—", label: "NO-OP", desc: "Discovery skipped or found nothing." };
    })();

    const KIND_COLORS = { upgrade: "var(--ok)", downgrade: "var(--warn)", mitigation: "var(--info)", workaround: "var(--fg-2)" };
    const srcAttempted = provenance.sources_attempted || [];
    const srcSucceeded = new Set(provenance.sources_succeeded || []);

    return (
      <div className="nv-grid">
        <PanelCard className="span-3">
          <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "28px 24px 20px" }}>
            <div style={{ width: 72, height: 72, borderRadius: "50%", background: STATUS.bg, border: `2px solid ${STATUS.color}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 32, color: STATUS.color, fontWeight: 700 }}>{STATUS.icon}</div>
            <div style={{ marginTop: 14, fontSize: 20, fontWeight: 700, color: STATUS.color, letterSpacing: "0.05em" }}>{STATUS.label}</div>
            <div style={{ marginTop: 6, color: "var(--fg-3)", fontSize: 12 }}>{STATUS.desc}</div>
          </div>
        </PanelCard>

        {actions.map((a, i) => (
          <PanelCard key={i} className="span-3" title={`action ${i + 1}`} right={
            <span style={{ background: (KIND_COLORS[a.kind] || "var(--fg-3)") + "22", color: KIND_COLORS[a.kind] || "var(--fg-3)", fontSize: 11, padding: "2px 8px", borderRadius: 3, fontWeight: 600, textTransform: "uppercase" }}>{a.kind || "unknown"}</span>
          }>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              {a.target && <span className="mono" style={{ fontWeight: 600, color: "var(--fg-0)" }}>{a.target}</span>}
              {a.target_version && <span className="mono" style={{ color: KIND_COLORS[a.kind] || "var(--fg-2)", fontWeight: 600 }}>→ {a.target_version}</span>}
            </div>
            {a.change && <div style={{ color: "var(--fg-1)", fontSize: 12.5, lineHeight: 1.5, marginBottom: 8 }}>{a.change}</div>}
            {a.rationale && <div style={{ color: "var(--fg-3)", fontSize: 12, fontStyle: "italic", marginBottom: 8 }}>{a.rationale}</div>}
            {a.confidence_bp != null && (
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
                <span style={{ color: "var(--fg-3)", fontSize: 11 }}>confidence</span>
                <div style={{ flex: 1, height: 6, background: "var(--line-1)", borderRadius: 3, overflow: "hidden", maxWidth: 200 }}>
                  <div style={{ width: `${a.confidence_bp / 100}%`, height: "100%", background: a.confidence_bp >= 7000 ? "var(--ok)" : a.confidence_bp >= 4000 ? "var(--warn)" : "var(--err)", borderRadius: 3 }} />
                </div>
                <span className="mono" style={{ fontSize: 11, color: "var(--fg-2)" }}>{(a.confidence_bp / 100).toFixed(0)}%</span>
              </div>
            )}
            {a.citation_url && (
              <div style={{ borderTop: "1px dashed var(--line-2)", paddingTop: 6, marginTop: 4 }}>
                <div style={{ fontSize: 11, color: "var(--fg-3)", marginBottom: 2 }}>citation · {a.source || "unknown"}</div>
                <div className="mono" style={{ fontSize: 11, color: "var(--info)", wordBreak: "break-all" }}>{a.citation_url}</div>
                {a.citation_excerpt && <div style={{ fontSize: 11, color: "var(--fg-2)", marginTop: 4, fontStyle: "italic" }}>"{a.citation_excerpt}"</div>}
              </div>
            )}
          </PanelCard>
        ))}

        <PanelCard title="provenance" className="span-3">
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
            {srcAttempted.map((s, i) => (
              <span key={i} style={{ fontSize: 11, padding: "2px 8px", borderRadius: 3, border: `1px solid ${srcSucceeded.has(s) ? "var(--ok)" : "var(--line-2)"}`, color: srcSucceeded.has(s) ? "var(--ok)" : "var(--fg-3)", background: srcSucceeded.has(s) ? "var(--ok-dim)" : "transparent" }}>{s}{srcSucceeded.has(s) ? " ✓" : ""}</span>
            ))}
          </div>
          <KV pairs={[
            ["references fetched", provenance.references_fetched],
            ["search results", provenance.search_results_fetched],
            ["registry check", provenance.registry_check_result || null],
            ["LM actions emitted", provenance.lm_actions_emitted],
            ["LM actions dropped (no citation)", provenance.lm_actions_dropped_no_citation],
          ]} />
        </PanelCard>

        {provenance.last_error && <ErrorBanner field="discovery" value={provenance.last_error} />}
      </div>
    );
  }

  return <PendingState family="default" lifecycle="done_empty" panelId={node.id} />;
}

// ─── KgFamilyPanel (FR-F12) — GraphTraverseView style ───────────────────

function KgMiniGraph({ items, label }) {
  const cx = 200, cy = 90, r = 60;
  const total = items.length;
  if (total === 0) return null;

  return (
    <svg className="mini-graph" viewBox="0 0 400 180" style={{ width: "100%", height: 160 }}>
      <circle cx={cx} cy={cy} r="16" fill="var(--accent-dim)" stroke="var(--accent)" />
      <text x={cx} y={cy + 4} textAnchor="middle" className="mini-label">{label || "query"}</text>
      {items.slice(0, 12).map((item, i) => {
        const angle = (i / Math.min(total, 12)) * Math.PI * 2 - Math.PI / 2;
        const x = cx + Math.cos(angle) * (r + 50);
        const y = cy + Math.sin(angle) * (r + 20);
        return (
          <g key={i}>
            <line x1={cx} y1={cy} x2={x} y2={y} stroke="var(--edge-bright)" strokeWidth="1" />
            <circle cx={x} cy={y} r="5" fill="var(--ok)" />
            <text x={x} y={y - 10} textAnchor="middle" className="mini-label">{typeof item === "string" ? item.split("/").pop().slice(0, 12) : ("item " + (i + 1))}</text>
          </g>
        );
      })}
    </svg>
  );
}

function KgFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending" : (status === "running" && !delta) ? "running_empty" : (status === "done" && !delta) ? "done_empty" : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") return <PendingState family="kg" lifecycle={lifecycle} panelId={node.id} />;

  const statusFields = {
    vec_search_retros: { status: "prior_retro_retrieval_status", mode: "prior_retro_retrieval_mode" },
    graph_prior_remediations: { status: "graph_prior_retrieval_status" },
    framework_mapping: { status: "framework_mapping_status" },
    plan_template_lookup: { hit: "template_lookup_hit", miss: "template_lookup_miss_reason" },
  };
  const sf = statusFields[node.id];

  // Gather items for the mini graph
  let retrievedItems = [];
  if (node.id === "vec_search_retros") retrievedItems = runState.prior_retro_suggestions || [];
  else if (node.id === "graph_prior_remediations") retrievedItems = runState.graph_prior_actions || [];
  else if (node.id === "framework_mapping") retrievedItems = (runState.framework_controls || []).concat(runState.attack_patterns || []);

  return (
    <div data-panel-id={node.id} className="nv-grid">
      <PanelCard title="index" className="span-1">
        <KV pairs={[
          ["node", node.id],
          ["family", "knowledge graph"],
          ["strategy", node.id.includes("vec") ? "embed + ANN" : "cypher"],
        ]} />
        {sf && (
          <div style={{ marginTop: 8 }}>
            {sf.status && runState[sf.status] != null && <Pill tone="info">{runState[sf.status]}</Pill>}
            {sf.mode && runState[sf.mode] != null && <span className="muted mono" style={{ marginLeft: 8, fontSize: 11 }}>mode: {runState[sf.mode]}</span>}
            {sf.hit != null && runState[sf.hit] != null && <Pill tone={runState[sf.hit] ? "ok" : "warn"}>{runState[sf.hit] ? "hit" : "miss"}</Pill>}
            {sf.miss && runState[sf.miss] && <span className="muted mono" style={{ marginLeft: 8, fontSize: 11 }}>{runState[sf.miss]}</span>}
          </div>
        )}
      </PanelCard>

      <PanelCard title="cursor" className="span-2">
        {retrievedItems.length > 0 ? (
          <div className="cursor-card">
            <div className="cursor-label">retrieved</div>
            <div className="cursor-path mono">{retrievedItems.length} item{retrievedItems.length === 1 ? "" : "s"}</div>
          </div>
        ) : <div className="muted">no results</div>}
      </PanelCard>

      {retrievedItems.length > 0 && (
        <PanelCard title="retrieved" className="span-3" right={<span className="muted mono">{retrievedItems.length}</span>} scroll>
          <ul className="retrieved">
            {retrievedItems.map((item, i) => (
              <li key={i}>
                <span className="retrieved-score mono">{i + 1}</span>
                <span className="retrieved-path mono">{typeof item === "string" ? item : (item.id || item.name || JSON.stringify(item).slice(0, 80))}</span>
              </li>
            ))}
          </ul>
        </PanelCard>
      )}

      {retrievedItems.length > 0 && (
        <PanelCard title="graph" className="span-3">
          <KgMiniGraph items={retrievedItems.map(item => typeof item === "string" ? item : (item.id || item.name || "node"))} label={node.id.replace(/_/g, " ").split(" ").pop()} />
        </PanelCard>
      )}

      {node.id === "framework_mapping" && (
        <>
          {runState.framework_controls && runState.framework_controls.length > 0 && (
            <PanelCard title="NIST controls" className="span-3" right={<span className="muted mono">{runState.framework_controls.length}</span>}>
              <DataTable headers={["Control"]} rows={runState.framework_controls.map((c, i) => (
                <tr key={i}><td style={{ padding: "6px 8px" }} className="mono">{typeof c === "string" ? c : (c.id || "") + " " + (c.name || "")}</td></tr>
              ))} />
            </PanelCard>
          )}
          {runState.attack_patterns && runState.attack_patterns.length > 0 && (
            <PanelCard title="CAPEC patterns" className="span-3" right={<span className="muted mono">{runState.attack_patterns.length}</span>}>
              <DataTable headers={["Pattern"]} rows={runState.attack_patterns.map((p, i) => (
                <tr key={i}><td style={{ padding: "6px 8px" }} className="mono">{typeof p === "string" ? p : (p.id || "") + " " + (p.name || "")}</td></tr>
              ))} />
            </PanelCard>
          )}
        </>
      )}

    </div>
  );
}
// ─── ToolFamilyPanel (FR-F13) ────────────────────────────────────────────

// FR-F13.3: per-node state field lookup table (DRY)
const TOOL_NODE_FIELDS = {
  attach_all_artifacts: [
    { key: "attachment_sys_ids", label: "Attachment Sys IDs" },
    { key: "attachment_count", label: "Attachment Count" },
    { key: "attachment_manifest", label: "Attachment Manifest" },
    { key: "last_attachment_error", label: "Error", danger: true },
  ],
  progressive_execute: [
    { key: "canary_passed", label: "Canary Passed" },
    { key: "stage_passed", label: "Stage Passed" },
    { key: "fleet_passed", label: "Fleet Passed" },
    { key: "per_host_apply_results", label: "Per-Host Apply Results" },
    { key: "execution_ledger", label: "Execution Ledger" },
  ],
  verify_immediate: [
    { key: "verify_outcome", label: "Verify Outcome" },
    { key: "per_host_verify_results", label: "Per-Host Verify Results" },
    { key: "verify_probe_method", label: "Probe Method" },
  ],
  partial_apply_rollback: [
    { key: "rollback_triggered", label: "Rollback Triggered" },
  ],
  judge_lint: [
    { key: "judge_lint_verdict", label: "Lint Verdict" },
  ],
  publish_docplus: [
    { key: "docplus_published", label: "DocPlus Published" },
    { key: "doc_sys_id", label: "Doc Sys ID" },
    { key: "attachment_sys_id", label: "Attachment Sys ID" },
    { key: "last_docplus_table_error", label: "Error", danger: true },
  ],
  run_outcome_persist: [
    { key: "run_outcome_written", label: "Outcome Written" },
    { key: "last_run_outcome_error", label: "Error", danger: true },
  ],
  cr_self_validate: [
    { key: "cr_self_validation_passed", label: "Validation Passed" },
    { key: "cr_self_validation_findings", label: "Findings" },
    { key: "observed_field_lengths", label: "Observed Field Lengths" },
    { key: "observed_attachment_count", label: "Observed Attachment Count" },
    { key: "observed_journal_count", label: "Observed Journal Count" },
  ],
  render_docx: [
    { key: "docx_artifact_ref", label: "DOCX Artifact Ref" },
    { key: "last_docx_emit_error", label: "Error", danger: true },
  ],
};

function ToolFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <PendingState family="default" lifecycle={lifecycle} panelId={node.id} />;
  }

  // FR-F13.1: tool-call events
  const toolCallEvents = events.filter(e => e.type === "tool_call");
  // FR-F13.2: tool-result events
  const toolResultEvents = events.filter(e => e.type === "tool_result");

  // FR-F13.3: per-node state fields from lookup table
  const nodeFields = TOOL_NODE_FIELDS[node.id] || [];

  return (
    <div data-panel-id={node.id}>
      <div className="nv-grid">

        {/* FR-F13.1: tool calls */}
        {toolCallEvents.length > 0 && (
          <PanelCard title="tool calls" className="span-3" right={<span className="muted mono">{toolCallEvents.length}</span>}>
            <DataTable
              headers={["Tool", "Namespace", "Args"]}
              rows={toolCallEvents.map((e, i) => (
                <tr key={i}>
                  <td style={{ padding: "6px 8px" }} className="mono">{e.tool || e.name || ""}</td>
                  <td style={{ padding: "6px 8px" }} className="mono">{e.namespace || ""}</td>
                  <td style={{ padding: "6px 8px", fontSize: "11px" }} className="mono muted">{e.args ? (typeof e.args === "string" ? e.args.slice(0, 80) : JSON.stringify(e.args).slice(0, 80)) : ""}</td>
                </tr>
              ))}
            />
          </PanelCard>
        )}

        {/* FR-F13.2: tool results */}
        {toolResultEvents.length > 0 && (
          <PanelCard title="tool results" className="span-3" right={<span className="muted mono">{toolResultEvents.length}</span>}>
            <DataTable
              headers={["Call ID", "OK", "Result"]}
              rows={toolResultEvents.map((e, i) => (
                <tr key={i}>
                  <td style={{ padding: "6px 8px" }} className="mono">{e.call_id || ""}</td>
                  <td style={{ padding: "6px 8px" }}>{e.ok != null ? (e.ok ? <Pill tone="ok">yes</Pill> : <Pill tone="warn">no</Pill>) : ""}</td>
                  <td style={{ padding: "6px 8px", fontSize: "11px" }}>{e.error || (e.result ? (typeof e.result === "string" ? e.result.slice(0, 80) : JSON.stringify(e.result).slice(0, 80)) : "")}</td>
                </tr>
              ))}
            />
          </PanelCard>
        )}

        {/* FR-F13.3: per-node state fields */}
        {nodeFields.length > 0 && nodeFields.some(f => runState[f.key] != null) && (
          <PanelCard title="state" className="span-3">
            <KV pairs={nodeFields.filter(f => runState[f.key] != null).map(f => {
              const val = runState[f.key];
              const strVal = typeof val === "object" ? JSON.stringify(val) : String(val);
              return [f.label, strVal.length > 120 ? strVal.slice(0, 120) + "..." : strVal, { mono: !f.danger }];
            })} />
          </PanelCard>
        )}

      </div>
    </div>
  );
}

// ─── SandboxFamilyPanel (FR-F14) ─────────────────────────────────────────

function SandboxFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <PendingState family="sandbox" lifecycle={lifecycle} panelId={node.id} />;
  }

  const sandbox = runState.sandbox || {};

  return (
    <div data-panel-id={node.id}>
      <PanelCard title="sandbox config">
        <KV pairs={[
          ["skip sandbox", runState.skip_sandbox != null ? String(runState.skip_sandbox) : null],
          ["skip reason", sandbox.skip_reason],
          ["force hitl", sandbox.force_hitl != null ? String(sandbox.force_hitl) : null],
        ]} />
      </PanelCard>
    </div>
  );
}

// ─── JoinFamilyPanel (FR-F15) ────────────────────────────────────────────

function JoinFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <PendingState family="default" lifecycle={lifecycle} panelId={node.id} />;
  }

  if (node.id === "validate_plan_join") {
    return (
      <div data-panel-id={node.id}>
        <PanelCard title="plan validation" right={
          runState.validation_passed != null
            ? <Pill tone={runState.validation_passed ? "ok" : "warn"}>{runState.validation_passed ? "passed" : "failed"}</Pill>
            : null
        }>
          <KV pairs={[
            ["safety verdict", runState.judge_safety_verdict],
            ["lint verdict", runState.judge_lint_verdict],
          ]} />
        </PanelCard>
      </div>
    );
  }

  if (node.id === "retro_join") {
    const transitionEvent = events.find(e => e.type === "transition");
    return (
      <div data-panel-id={node.id}>
        <PanelCard title="retro join">
          {transitionEvent ? (
            <div>
              <span className="muted" style={{ fontSize: "11px" }}>downstream: </span>
              <Pill tone="info">{transitionEvent.to_node || "transition recorded"}</Pill>
            </div>
          ) : (
            <p className="muted" style={{ margin: 0, fontStyle: "italic" }}>awaiting upstream completion</p>
          )}
        </PanelCard>
      </div>
    );
  }

  return <PendingState family="default" lifecycle="done_empty" panelId={node.id} />;
}

// ─── TerminalFamilyPanel (FR-F16) ────────────────────────────────────────

function TerminalFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <PendingState family="terminal" lifecycle={lifecycle} panelId={node.id} />;
  }

  const d = delta?.fields || {};
  const haltReason = d.halt_reason || runState.halt_reason || "";

  // Pull context from full state at this checkpoint
  const allState = runState?.checkpoints
    ? (() => { const cps = runState.checkpoints; for (const c of cps) { if (c.last_node === node.id) return c.state || {}; } return {}; })()
    : {};
  const cveId = allState.cve_id || "";
  const vendor = allState.cve_vendor || "";
  const product = allState.cve_product || "";
  const disposition = allState.disposition || "";
  const ssvcTier = allState.ssvc_tier || "";
  const hostCount = (allState.affected_host_names || []).length;

  const resultEvent = events.find(e => e.type === "result");
  const durationMs = resultEvent ? resultEvent.run_duration_ms : null;

  const TERMINAL_STYLE = {
    suppress_not_applicable: { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)", icon: "⊘", label: "SUPPRESSED" },
    tier_terminal_track:     { color: "var(--info)",  bg: "rgba(122,182,255,.10)", icon: "◎", label: "TRACK" },
    tier_terminal_defer:     { color: "var(--warn)",  bg: "rgba(255,193,7,.12)",   icon: "⏸", label: "DEFERRED" },
    action_done:             { color: "var(--ok)",    bg: "var(--ok-dim)",          icon: "✓", label: "COMPLETE" },
  };
  const v = TERMINAL_STYLE[node.id] || { color: "var(--fg-3)", bg: "rgba(255,255,255,.06)", icon: "■", label: "TERMINAL" };

  return (
    <div className="nv-grid">
      <PanelCard className="span-3">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "32px 24px 20px" }}>
          <div style={{ width: 72, height: 72, borderRadius: "50%", background: v.bg, border: `2px solid ${v.color}`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 32, color: v.color, fontWeight: 700 }}>{v.icon}</div>
          <div style={{ marginTop: 14, fontSize: 20, fontWeight: 700, color: v.color, letterSpacing: "0.05em" }}>{v.label}</div>
          {haltReason && <div style={{ marginTop: 8, color: "var(--fg-2)", fontSize: 12.5, textAlign: "center", maxWidth: 500 }}>{haltReason}</div>}
          {node.id === "action_done" && durationMs != null && <div style={{ marginTop: 6, color: "var(--fg-3)", fontSize: 12 }}>{(durationMs / 1000).toFixed(1)}s total</div>}
        </div>
      </PanelCard>

      {cveId && (
        <PanelCard title="context" className="span-3">
          <KV pairs={[
            ["CVE", cveId],
            ["vendor / product", vendor && product ? `${vendor} / ${product}` : vendor || product || null],
            ["disposition", disposition],
            ["SSVC tier", ssvcTier],
            ["affected hosts", String(hostCount)],
          ]} />
        </PanelCard>
      )}
    </div>
  );
}

// ─── Panel registries (stubs — filled by later tasks) ─────────────────────

const PRIORITY_PANEL = {};
const FAMILY_PANEL = {};

// ─── Phase / copy skeletons ───────────────────────────────────────────────

const PHASE_ORDER = ["intake", "correlate", "plan_sandbox", "cr_execute", "retro"];
const PHASE_LABEL = {
  intake: "intake",
  correlate: "correlate",
  plan_sandbox: "plan+sandbox",
  cr_execute: "cr+execute",
  retro: "retro",
};

// ─── harbor.yaml phase parser ────────────────────────────────────────────

const PHASE_HEADER_RE = /^#\s*-{5,}\s*(?:Phase\s+\d+:\s*)?([^-]+?)\s*-{5,}/;
const ID_LINE_RE = /^\s*-\s+id:\s*([a-z0-9_]+)/;

const PHASE_NAME_TO_KEY = {
  "pre-flight gates": "intake",
  "intake": "intake",
  "correlate + tier": "correlate",
  "plan + sandbox": "plan_sandbox",
  "cr + execute + verify": "cr_execute",
  "retro + learn": "retro",
  "terminal": "retro",
};

const STATIC_PHASE_FOR = {
  halt_new_gate: "intake", intake_fetch: "intake", source_trust_gate: "intake",
  canonicalize_trusted: "intake", extract_trusted: "intake", enrich_cve_trusted: "intake",
  source_trust_audit: "intake", canonicalize_untrusted: "intake", emit_quarantine_artifact: "intake",
  extract_untrusted: "intake", injection_classify: "intake", critique_extracted: "intake",
  enrich_cve_untrusted: "intake", hitl_ingest_review: "intake", branch_resp_ingest: "intake",
  remediation_discovery: "correlate", correlate_assets: "correlate",
  suppress_not_applicable: "correlate", ssvc_evaluate: "correlate",
  tier_terminal_track: "correlate", tier_terminal_defer: "correlate",
  plan_template_lookup: "plan_sandbox", mcp_retrieval_dispatch: "plan_sandbox",
  vec_search_retros: "plan_sandbox", graph_prior_remediations: "plan_sandbox",
  graph_blast_radius: "plan_sandbox", framework_mapping: "plan_sandbox",
  cargonet_lab_telemetry: "plan_sandbox", planner: "plan_sandbox",
  code_writer: "plan_sandbox", emit_remediation_bundle: "plan_sandbox",
  critic: "plan_sandbox", hitl_plan_review: "plan_sandbox", branch_resp_plan: "plan_sandbox",
  validate_dispatch: "plan_sandbox", judge_safety: "plan_sandbox", judge_lint: "plan_sandbox",
  validate_plan_join: "plan_sandbox", plan_quarantine_gate: "plan_sandbox",
  sandbox_dispatch: "plan_sandbox", sandbox_run: "plan_sandbox",
  sandbox_skip: "plan_sandbox", emit_sandbox_evidence: "plan_sandbox",
  create_change_request: "cr_execute", open_change_request: "cr_execute",
  emit_evidence_bundle: "cr_execute",
  attach_all_artifacts: "cr_execute", hitl_change_approval: "cr_execute",
  branch_resp_change: "cr_execute", progressive_execute: "cr_execute",
  verify_execution: "cr_execute", rollback_if_failed: "cr_execute",
  drift_watch_spawn: "retro", retro_analysis: "retro", write_retrospective: "retro",
  kg_writeback: "retro", emit_kg_payload: "retro", render_docx: "retro",
  emit_docx_artifact: "retro", dispatch_docplus: "retro", retro_fanout: "retro",
  retro_join: "retro", krakntrust_attest: "retro", outcome_record: "retro",
  cr_validate: "retro", emit_proof_chain: "retro", hitl_retro_review: "retro",
  branch_resp_retro: "retro",
};

function parseHarborYamlPhases(yamlText) {
  const lines = yamlText.split("\n");
  const phaseFor = new Map();
  let currentPhase = null;
  for (const line of lines) {
    const hm = line.match(PHASE_HEADER_RE);
    if (hm) {
      const raw = hm[1].trim().toLowerCase();
      currentPhase = PHASE_NAME_TO_KEY[raw] || null;
      continue;
    }
    const im = line.match(ID_LINE_RE);
    if (im && currentPhase) {
      phaseFor.set(im[1], currentPhase);
    }
  }
  return { phaseFor };
}

let _phaseMapCache = null;
let _phaseMapPromise = null;

function usePhaseMap() {
  const [map, setMap] = React.useState(_phaseMapCache);
  React.useEffect(() => {
    if (_phaseMapCache) { setMap(_phaseMapCache); return; }
    if (!_phaseMapPromise) {
      _phaseMapPromise = fetch(window.apiUrl("/watch/harbor.yaml"))
        .then(r => { if (!r.ok) throw new Error(r.status); return r.text(); })
        .then(text => {
          const parsed = parseHarborYamlPhases(text);
          if (parsed.phaseFor.size === 0) {
            parsed.phaseFor = new Map(Object.entries(STATIC_PHASE_FOR));
          }
          _phaseMapCache = parsed;
          return _phaseMapCache;
        })
        .catch(err => {
          console.warn("[usePhaseMap] fetch failed, using static fallback:", err);
          _phaseMapCache = { phaseFor: new Map(Object.entries(STATIC_PHASE_FOR)) };
          return _phaseMapCache;
        });
    }
    _phaseMapPromise.then(m => setMap(m));
  }, []);
  return map;
}

// single grep-target for all 4x~10 empty-state cells per D3
const EMPTY_COPY = {
  default: {
    pending: "pending",
    running_empty: "running — no checkpoint yet",
    done_empty: "no state changes",
    failed: "FAILED",
  },
  gate: { done_empty: "gate passed (no halt)" },
  decision: { done_empty: "decision made — no state change" },
  branch: { done_empty: "branch not yet evaluated" },
  artifact: { done_empty: "no artifact emitted" },
  hitl: { running_empty: "waiting for human input" },
  llm: { running_empty: "running — awaiting token stream" },
  kg: { done_empty: "no retrieval results" },
  sandbox: { done_empty: "probe not yet run" },
  terminal: { done_empty: "run complete" },
};

/** Return lifecycle copy for a given family, falling back to default. */
function emptyCopy(family, lifecycle) {
  const override = EMPTY_COPY[family];
  if (override && override[lifecycle] !== undefined) {
    return override[lifecycle];
  }
  return EMPTY_COPY.default[lifecycle];
}

// Diagnostic fields for cargonet panels per D12.
const CARGONET_DIAGNOSTIC_FIELDS = [
  "cargonet_lab_ref",
  "cargonet_proxy_ref",
  "cargonet_node_count",
  "cargonet_correlation_map",
  "last_cargonet_error",
  "cargonet_writeback_done",
];

// ─── Helpers ──────────────────────────────────────────────────────────────

/** Return the data-panel-id attribute value for a given node. */
function panelDataNodeId(node) {
  return node.id;
}

// ─── Dispatcher ───────────────────────────────────────────────────────────

/**
 * panelForNode(node) — 3-tier panel dispatcher per D1.
 *
 * Precedence (D1): priority-id → cargonet-id → family → OutcomePanel fallback.
 *
 *   1. PRIORITY_PANEL[node.id]  — bespoke panels for high-value nodes
 *   2. CARGONET_IDS.has(node.id) → CargonetFamilyPanel (when wired)
 *   3. FAMILY_PANEL[profile.family] — family-shaped fallback
 *   4. window.OutcomePanel — generic last-resort
 *
 * Returns a component reference (function), NOT JSX.
 * The caller renders: <Panel {...props} />.
 */
function panelForNode(node, status) {
  // 0. Skipped overrides all — node was routed around, never executed
  if (status === "skipped") {
    return SkippedPanel;
  }

  // 0.5. Router nodes — generic router panel based on hardcoded route table
  if (ROUTER_ROUTES[node.id]) {
    return RouterPanel;
  }

  // 1. Priority lookup
  if (PRIORITY_IDS.has(node.id) && PRIORITY_PANEL[node.id]) {
    return PRIORITY_PANEL[node.id];
  }

  // Dev guard: warn on unmapped priority ids (D2 assertion)
  if (typeof process !== "undefined" && process?.env?.NODE_ENV !== "production" && PRIORITY_IDS.has(node.id) && !PRIORITY_PANEL[node.id]) {
    console.warn(`[node-panels] priority id "${node.id}" has no mapped panel in PRIORITY_PANEL`);
  }

  // 2. Cargonet lookup
  if (CARGONET_IDS.has(node.id)) {
    return window.CargonetFamilyPanel || UnimplementedPanel;
  }

  // 3. Family lookup via profile
  const profile = window.NODE_PROFILE && window.NODE_PROFILE[node.id];
  if (profile && profile.family && FAMILY_PANEL[profile.family]) {
    return FAMILY_PANEL[profile.family];
  }

  // 4. Fallback
  return window.OutcomePanel || UnimplementedPanel;
}

// ─── Priority panel registration ─────────────────────────────────────────

PRIORITY_PANEL.intake_fetch = IntakeFetchPanel;
PRIORITY_PANEL.canonicalize_trusted = TransformPanel;
PRIORITY_PANEL.canonicalize_untrusted = TransformPanel;
PRIORITY_PANEL.extract_trusted = ExtractPanel;
PRIORITY_PANEL.extract_untrusted = ExtractPanel;
PRIORITY_PANEL.enrich_cve_trusted = TransformPanel;
PRIORITY_PANEL.enrich_cve_untrusted = TransformPanel;
PRIORITY_PANEL.emit_quarantine_artifact = EmitQuarantinePanel;
PRIORITY_PANEL.injection_classify = InjectionClassifyPanel;
PRIORITY_PANEL.critique_extracted = CritiqueExtractedPanel;
PRIORITY_PANEL.hitl_ingest_review = HitlReviewPanel;
PRIORITY_PANEL.hitl_plan_review = HitlReviewPanel;
PRIORITY_PANEL.hitl_change_approval = HitlReviewPanel;
PRIORITY_PANEL.hitl_retrospective_review = HitlReviewPanel;
PRIORITY_PANEL.correlate_assets = CorrelateAssetsPanel;
PRIORITY_PANEL.graph_blast_radius = GraphBlastRadiusPanel;
PRIORITY_PANEL.framework_mapping = FrameworkMappingPanel;
PRIORITY_PANEL.cargonet_lab_telemetry = CargonetLabTelemetryPanel;
PRIORITY_PANEL.planner = PlannerPanel;
PRIORITY_PANEL.code_writer = CodeWriterPanel;
PRIORITY_PANEL.emit_remediation_bundle = EmitRemediationBundlePanel;
PRIORITY_PANEL.validate_dispatch = ValidateDispatchPanel;
PRIORITY_PANEL.judge_safety = JudgeSafetyPanel;
PRIORITY_PANEL.judge_lint = JudgeLintPanel;
PRIORITY_PANEL.sandbox_skip = SandboxSkipPanel;
PRIORITY_PANEL.emit_sandbox_evidence = EmitSandboxEvidencePanel;
PRIORITY_PANEL.ssvc_evaluate = SsvcEvaluatePanel;
PRIORITY_PANEL.vec_search_retros = VecSearchRetrosPanel;
PRIORITY_PANEL.graph_prior_remediations = GraphPriorRemediationsPanel;
PRIORITY_PANEL.sandbox_run = SandboxRunPanel;
PRIORITY_PANEL.create_change_request = CreateChangeRequestPanel;
PRIORITY_PANEL.write_retrospective = WriteRetrospectivePanel;
PRIORITY_PANEL.krakntrust_attest = KrakntrustAttestPanel;
PRIORITY_PANEL.drift_watch_spawn = DriftWatchSpawnPanel;
PRIORITY_PANEL.partial_apply_rollback = PartialApplyRollbackPanel;
PRIORITY_PANEL.divergence_quarantine = DivergenceQuarantinePanel;
PRIORITY_PANEL.kg_run_writeback = KgRunWritebackPanel;
PRIORITY_PANEL.emit_retro_payload = EmitRetroPayloadPanel;
PRIORITY_PANEL.render_docx = RenderDocxPanel;
PRIORITY_PANEL.emit_docx_archive = EmitDocxArchivePanel;
PRIORITY_PANEL.publish_docplus = PublishDocPlusPanel;
PRIORITY_PANEL.cargonet_writeback = CargoNetWritebackPanel;
PRIORITY_PANEL.plan_kg_writeback = PlanKgWritebackPanel;
PRIORITY_PANEL.run_outcome_persist = RunOutcomePersistPanel;
PRIORITY_PANEL.sandbox_dispatch = SandboxDispatchPanel;
PRIORITY_PANEL.verify_immediate = VerifyImmediatePanel;
PRIORITY_PANEL.retro_dispatch = RetroDispatchPanel;
PRIORITY_PANEL.emit_proof_report = EmitProofReportPanel;
PRIORITY_PANEL.progressive_execute = ProgressiveExecutePanel;

// ─── Family panel registration ──────────────────────────────────────────

FAMILY_PANEL.gate = GateFamilyPanel;
FAMILY_PANEL.decision = DecisionFamilyPanel;
FAMILY_PANEL.transform = TransformFamilyPanel;
FAMILY_PANEL.llm = LlmFamilyPanel;
FAMILY_PANEL.audit = AuditFamilyPanel;
FAMILY_PANEL.artifact = ArtifactFamilyPanel;
FAMILY_PANEL.hitl = HitlFamilyPanel;
FAMILY_PANEL.branch = BranchFamilyPanel;
FAMILY_PANEL.agent = AgentFamilyPanel;
FAMILY_PANEL.kg = KgFamilyPanel;
FAMILY_PANEL.tool = ToolFamilyPanel;
FAMILY_PANEL.sandbox = SandboxFamilyPanel;
FAMILY_PANEL.join = JoinFamilyPanel;
FAMILY_PANEL.terminal = TerminalFamilyPanel;

// ─── Coverage assertion ──────────────────────────────────────────────────

function assertNodeCoverage(topo) {
  if (!topo || !topo.order) return;
  for (const entry of topo.order) {
    const id = entry.id || entry;
    const panel = panelForNode({ id });
    if (panel === (window.OutcomePanel || UnimplementedPanel)) {
      console.warn(`[assertNodeCoverage] node "${id}" falls through to OutcomePanel — no bespoke/family panel mapped`);
    }
  }
}

// ─── Event filtering helper ──────────────────────────────────────────────

// reuses existing nodeEvents map dedupe; NFR-10
function eventsForNode(allEvents, id, delta) {
  return allEvents.filter(
    e => e.from_node === id || e.to_node === id || (delta && e.step === delta.step)
  );
}

// ─── Window exports ───────────────────────────────────────────────────────

window.panelForNode = panelForNode;
window.FAMILY_PANEL = FAMILY_PANEL;
window.PRIORITY_PANEL = PRIORITY_PANEL;
window.IntakeFetchPanel = IntakeFetchPanel;
window.ExtractPanel = ExtractPanel;
window.EmitQuarantinePanel = EmitQuarantinePanel;
window.InjectionClassifyPanel = InjectionClassifyPanel;
window.CritiqueExtractedPanel = CritiqueExtractedPanel;
window.HitlReviewPanel = HitlReviewPanel;
window.CorrelateAssetsPanel = CorrelateAssetsPanel;
window.SsvcEvaluatePanel = SsvcEvaluatePanel;
window.VecSearchRetrosPanel = VecSearchRetrosPanel;
window.GraphPriorRemediationsPanel = GraphPriorRemediationsPanel;
window.GraphBlastRadiusPanel = GraphBlastRadiusPanel;
window.FrameworkMappingPanel = FrameworkMappingPanel;
window.CargonetLabTelemetryPanel = CargonetLabTelemetryPanel;
window.PlannerPanel = PlannerPanel;
window.CodeWriterPanel = CodeWriterPanel;
window.EmitRemediationBundlePanel = EmitRemediationBundlePanel;
window.ValidateDispatchPanel = ValidateDispatchPanel;
window.JudgeSafetyPanel = JudgeSafetyPanel;
window.JudgeLintPanel = JudgeLintPanel;
window.SandboxSkipPanel = SandboxSkipPanel;
window.EmitSandboxEvidencePanel = EmitSandboxEvidencePanel;
window.TransformPanel = TransformPanel;
window.RouterPanel = RouterPanel;
window.SkippedPanel = SkippedPanel;
window.ROUTER_ROUTES = ROUTER_ROUTES;
window.SandboxRunPanel = SandboxRunPanel;
window.CreateChangeRequestPanel = CreateChangeRequestPanel;
window.WriteRetrospectivePanel = WriteRetrospectivePanel;
window.KrakntrustAttestPanel = KrakntrustAttestPanel;
window.DriftWatchSpawnPanel = DriftWatchSpawnPanel;
window.PartialApplyRollbackPanel = PartialApplyRollbackPanel;
window.DivergenceQuarantinePanel = DivergenceQuarantinePanel;
window.KgRunWritebackPanel = KgRunWritebackPanel;
window.EmitRetroPayloadPanel = EmitRetroPayloadPanel;
window.RenderDocxPanel = RenderDocxPanel;
window.EmitDocxArchivePanel = EmitDocxArchivePanel;
window.PublishDocPlusPanel = PublishDocPlusPanel;
window.CargoNetWritebackPanel = CargoNetWritebackPanel;
window.PlanKgWritebackPanel = PlanKgWritebackPanel;
window.RunOutcomePersistPanel = RunOutcomePersistPanel;
window.MarkdownView = MarkdownView;
window.DocxPreview = DocxPreview;
window.SandboxDispatchPanel = SandboxDispatchPanel;
window.VerifyImmediatePanel = VerifyImmediatePanel;
window.RetroDispatchPanel = RetroDispatchPanel;
window.EmitProofReportPanel = EmitProofReportPanel;
window.ProofReportFetch = ProofReportFetch;
window.ProgressiveExecutePanel = ProgressiveExecutePanel;
window.CargonetFamilyPanel = CargonetFamilyPanel;
window.CARGONET_DIAGNOSTIC_FIELDS_FULL = CARGONET_DIAGNOSTIC_FIELDS_FULL;
window.GateFamilyPanel = GateFamilyPanel;
window.DecisionFamilyPanel = DecisionFamilyPanel;
window.TransformFamilyPanel = TransformFamilyPanel;
window.LlmFamilyPanel = LlmFamilyPanel;
window.StreamingTokenBlock = StreamingTokenBlock;
window.AuditFamilyPanel = AuditFamilyPanel;
window.ArtifactFamilyPanel = ArtifactFamilyPanel;
window.HitlFamilyPanel = HitlFamilyPanel;
window.BranchFamilyPanel = BranchFamilyPanel;
window.AgentFamilyPanel = AgentFamilyPanel;
window.KgFamilyPanel = KgFamilyPanel;
window.ToolFamilyPanel = ToolFamilyPanel;
window.TOOL_NODE_FIELDS = TOOL_NODE_FIELDS;
window.SandboxFamilyPanel = SandboxFamilyPanel;
window.JoinFamilyPanel = JoinFamilyPanel;
window.TerminalFamilyPanel = TerminalFamilyPanel;
window.ARTIFACT_REF_FIELDS = ARTIFACT_REF_FIELDS;
window.PRIORITY_IDS = PRIORITY_IDS;
window.CARGONET_IDS = CARGONET_IDS;
window.UnimplementedPanel = UnimplementedPanel;
window.PHASE_ORDER = PHASE_ORDER;
window.PHASE_LABEL = PHASE_LABEL;
window.PHASE_HEADER_RE = PHASE_HEADER_RE;
window.PHASE_NAME_TO_KEY = PHASE_NAME_TO_KEY;
window.parseHarborYamlPhases = parseHarborYamlPhases;
window.usePhaseMap = usePhaseMap;
window.EMPTY_COPY = EMPTY_COPY;
window.emptyCopy = emptyCopy;
window.CARGONET_DIAGNOSTIC_FIELDS = CARGONET_DIAGNOSTIC_FIELDS;
window.panelDataNodeId = panelDataNodeId;
window.eventsForNode = eventsForNode;
window.ErrorBanner = ErrorBanner;
window.assertNodeCoverage = assertNodeCoverage;
window.usePanelMountMark = usePanelMountMark;
window.PanelCard = PanelCard;
window.KV = KV;
window.StatCards = StatCards;
window.PendingState = PendingState;
window.Pill = Pill;
window.DataTable = DataTable;
