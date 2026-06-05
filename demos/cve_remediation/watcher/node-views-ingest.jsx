// node-views-ingest.jsx — specialized view components for CVE remediation ingest pipeline

const { useState } = React;

// ─── IntakeFetchView (SOURCE — NVD + EPSS + KEV) ───────────────────────────

function IntakeFetchView({ node, content, progress, isLive }) {
  const { cvss, epss, kevListed, published, feeds, rows, query } = content;
  const [reqOpen, setReqOpen] = useState(false);
  const r = rows[0] || {};

  const sev = cvss >= 9 ? { label: "CRITICAL", bg: "rgba(239,106,106,.15)", border: "rgba(239,106,106,.4)", color: "var(--err)" }
            : cvss >= 7 ? { label: "HIGH",     bg: "rgba(245,181,74,.12)",  border: "rgba(245,181,74,.35)", color: "var(--warn)" }
            : cvss >= 4 ? { label: "MEDIUM",   bg: "rgba(122,182,255,.10)", border: "rgba(122,182,255,.3)", color: "var(--info)" }
            :             { label: "LOW",       bg: "rgba(95,207,144,.10)",  border: "rgba(95,207,144,.3)",  color: "var(--ok)" };

  const feedsDone = feeds.filter(f => !isLive || progress >= f.doneAt);

  return (
    <div style={{ padding: "20px 24px", display: "flex", flexDirection: "column", gap: 20, overflow: "auto", minHeight: 0 }}>

      {/* ── Hero: CVSS badge + CVE identity ── */}
      <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
        <div style={{
          minWidth: 96, textAlign: "center", padding: "16px 14px 12px",
          background: sev.bg, border: "1px solid " + sev.border, borderRadius: 10,
        }}>
          <div className="mono" style={{ fontSize: 36, fontWeight: 700, lineHeight: 1, color: sev.color }}>{cvss}</div>
          <div style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".12em", color: sev.color, marginTop: 6 }}>{sev.label}</div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="mono" style={{ fontSize: 17, fontWeight: 600, color: "var(--fg-0)" }}>{r.id}</div>
          <div style={{ color: "var(--fg-1)", marginTop: 4 }}>{r.subject}</div>
          <div className="mono muted" style={{ fontSize: 11.5, marginTop: 8 }}>
            published {published}
            {r.email && <> · {r.email}</>}
          </div>
        </div>
      </div>

      {/* ── Stat strip: EPSS / KEV / refs ── */}
      <div style={{ display: "flex", gap: 8 }}>
        {[
          { label: "EPSS", value: isLive && progress < 0.6 ? "..." : String(epss), mono: true },
          { label: "KEV",  value: isLive && progress < 0.85 ? "..." : (kevListed ? "YES" : "NO"),
            color: kevListed ? "var(--err)" : "var(--ok)" },
          { label: "refs", value: isLive && progress < 0.3 ? "..." : String(r.attachments), mono: true },
        ].map(s => (
          <div key={s.label} style={{
            flex: 1, background: "var(--bg-3)", border: "1px solid var(--line-1)",
            borderRadius: 6, padding: "10px 12px", textAlign: "center",
          }}>
            <div className={s.mono ? "mono" : ""} style={{ fontSize: 18, fontWeight: 600, color: s.color || "var(--fg-0)", lineHeight: 1.2 }}>{s.value}</div>
            <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", marginTop: 4 }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* ── Feeds checklist ── */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <span style={{ fontSize: 10.5, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".1em", fontWeight: 600 }}>feeds</span>
          <span className="mono muted" style={{ fontSize: 11 }}>{feedsDone.length} / {feeds.length}</span>
        </div>
        {feeds.map(f => {
          const done = !isLive || progress >= f.doneAt;
          return (
            <div key={f.id} style={{
              display: "flex", alignItems: "center", gap: 10, padding: "7px 0",
              borderBottom: "1px solid var(--line-1)",
            }}>
              <span style={{ width: 16, textAlign: "center", fontSize: 12, color: done ? "var(--ok)" : "var(--fg-3)" }}>
                {done ? "✓" : "◌"}
              </span>
              <span style={{ fontWeight: 500, fontSize: 12, color: done ? "var(--fg-0)" : "var(--fg-3)", minWidth: 36 }}>{f.label}</span>
              <span className="mono muted" style={{ fontSize: 11, flex: 1 }}>{f.url}</span>
              {done
                ? <span className="mono" style={{ fontSize: 11, color: "var(--fg-1)" }}>{f.value}</span>
                : <ShimmerLine width={60} />
              }
            </div>
          );
        })}
      </div>

      {/* ── Raw request (collapsed) ── */}
      <div style={{ background: "var(--bg-3)", border: "1px solid var(--line-1)", borderRadius: 6 }}>
        <button
          onClick={() => setReqOpen(!reqOpen)}
          style={{
            display: "flex", width: "100%", justifyContent: "space-between", alignItems: "center",
            padding: "8px 12px", background: "none", border: "none", color: "var(--fg-2)", fontSize: 11.5,
          }}
        >
          <span style={{ textTransform: "uppercase", letterSpacing: ".08em", fontWeight: 500, fontSize: 10.5 }}>request</span>
          <span className="mono" style={{ fontSize: 11 }}>{reqOpen ? "▾ collapse" : "▸ " + query.length + " chars"}</span>
        </button>
        {reqOpen && <pre className="code" style={{ margin: 0, padding: "0 12px 12px", fontSize: 11.5 }}>{query}</pre>}
      </div>
    </div>
  );
}

// ─── ExtractTrustedView (LLM / DSPy extraction) ────────────────────────────

function ExtractTrustedView({ node, content, progress, isLive }) {
  const { model, promptTokens, signature, extracted, reasoning, streamingResponse } = content;
  const [jsonOpen, setJsonOpen] = useState(false);

  const fullResp = streamingResponse || "";
  const reasoningShown = isLive
    ? reasoning.slice(0, Math.ceil(reasoning.length * Math.min(1, progress / 0.7)))
    : reasoning;
  const respStart = 0.7, respSpan = 0.3;
  const responseShown = !isLive ? fullResp
    : progress < respStart ? ""
    : fullResp.slice(0, Math.floor(fullResp.length * Math.min(1, (progress - respStart) / respSpan)));

  const fields = [
    { k: "cwe",      v: extracted?.cwe,          tone: "warn", thresh: 0.75 },
    { k: "vector",   v: extracted?.vector,        tone: "info", thresh: 0.80 },
    { k: "products", v: extracted?.products?.join(", "), tone: null, thresh: 0.85 },
    { k: "range",    v: extracted?.versionRange,  tone: null,   thresh: 0.90 },
  ];

  const outTokens = Math.floor((responseShown.length || 0) / 4);

  return (
    <div className="nv-grid">
      <Panel title="model" className="span-1">
        <dl className="kv">
          <dt>model</dt><dd className="mono">{model}</dd>
          <dt>tokens in</dt><dd className="mono">{promptTokens?.toLocaleString()}</dd>
          <dt>tokens out</dt><dd className="mono">{outTokens}</dd>
          <dt>confidence</dt><dd className="mono">{extracted?.confidence}</dd>
        </dl>
      </Panel>

      <Panel title="DSPy signature" className="span-2">
        <dl className="kv">
          <dt>IN</dt><dd className="mono">{signature?.inputs?.join(", ")}</dd>
          <dt>OUT</dt><dd className="mono">{signature?.outputs?.join(", ")}</dd>
        </dl>
      </Panel>

      <Panel title="extracted fields" className="span-3">
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
          {fields.map(f => {
            const visible = !isLive || progress >= f.thresh;
            return (
              <div key={f.k} style={{ background: "var(--bg-2)", border: "1px solid var(--line-1)", borderRadius: 6, padding: "10px 12px" }}>
                <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 6 }}>{f.k}</div>
                {visible
                  ? f.tone ? <Pill tone={f.tone}>{f.v}</Pill> : <span className="mono" style={{ fontSize: 12 }}>{f.v}</span>
                  : <ShimmerLine />
                }
              </div>
            );
          })}
        </div>
      </Panel>

      <Panel title="reasoning trace" className="span-3" right={
        <span className="muted mono">{reasoningShown.length} / {reasoning.length}</span>
      }>
        <ol className="reason">
          {reasoningShown.map((r, i) => (
            <li key={i}><span className="reason-n">{i + 1}</span>{r}</li>
          ))}
          {isLive && reasoningShown.length < reasoning.length && (
            <li className="reason-typing"><span className="reason-n">{reasoningShown.length + 1}</span><ShimmerLine /></li>
          )}
        </ol>
      </Panel>

      <Panel title="response" className="span-3" mono right={
        <button className="ghost-btn" onClick={() => setJsonOpen(!jsonOpen)}>{jsonOpen ? "collapse" : "expand"}</button>
      }>
        {isLive && progress < 1 ? (
          <pre className="code code-json">{responseShown}<span className="caret">█</span></pre>
        ) : jsonOpen ? (
          <pre className="code code-json">{fullResp}</pre>
        ) : (
          <span className="muted mono" style={{ fontSize: 12, padding: "0 14px" }}>{fullResp.length} chars</span>
        )}
      </Panel>
    </div>
  );
}

// ─── CorrelateAssetsView (Nautilus broker) ───────────────────────────────────

function CorrelateAssetsView({ node, content, progress, isLive }) {
  const { visited, totalHosts, exposedCount, assetClasses, edgesFollowed, nodesExpanded, attestation, root } = content;
  const shown = isLive ? Math.max(1, Math.floor(visited.length * Math.min(1, progress / 0.9))) : visited.length;
  const visibleItems = visited.slice(0, shown);

  const grouped = {};
  visibleItems.forEach(v => { (grouped[v.sourceType] = grouped[v.sourceType] || []).push(v); });

  const typeColor = { nautobot: "var(--ok)", cmdb: "var(--info)", reachability: "var(--warn)" };

  // topology SVG
  const cx = 240, cy = 90;
  const svgItems = visited.slice(0, shown);

  return (
    <div className="nv-grid">
      <Panel title="blast radius" className="span-3">
        <div className="statsbar" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
          <div className="stat"><div className="stat-v mono">{totalHosts}</div><div className="stat-k">total hosts</div></div>
          <div className="stat"><div className="stat-v mono" style={{ color: exposedCount > 0 ? "var(--err)" : "var(--ok)" }}>{exposedCount}</div><div className="stat-k">internet-exposed</div></div>
          <div className="stat"><div className="stat-v mono">{assetClasses.length}</div><div className="stat-k">asset classes</div></div>
        </div>
      </Panel>

      <Panel title="broker" className="span-1">
        <dl className="kv">
          <dt>root</dt><dd className="mono">{root}</dd>
          <dt>attestation</dt><dd className="mono">{attestation}</dd>
          <dt>nodes</dt><dd className="mono">{nodesExpanded}</dd>
          <dt>edges</dt><dd className="mono">{edgesFollowed}</dd>
        </dl>
      </Panel>

      <Panel title="topology" className="span-2">
        <svg className="mini-graph" viewBox="0 0 480 180">
          <circle cx={cx} cy={cy} r="16" fill="var(--accent-dim)" stroke="var(--accent)" />
          <text x={cx} y={cy + 4} textAnchor="middle" className="mini-label">broker</text>
          {svgItems.map((v, i) => {
            const angle = (i / Math.max(visited.length, 1)) * Math.PI * 2 - Math.PI / 2;
            const x = cx + Math.cos(angle) * 120;
            const y = cy + Math.sin(angle) * 65;
            const fill = typeColor[v.sourceType] || "var(--mute-2)";
            const isCurrent = i === shown - 1;
            return (
              <g key={i}>
                <line x1={cx} y1={cy} x2={x} y2={y} stroke={isCurrent ? "var(--accent)" : "var(--edge-bright)"} strokeWidth={isCurrent ? 1.6 : 1} />
                <circle cx={x} cy={y} r={isCurrent ? 6 : 4} fill={fill} />
                <text x={x} y={y - 9} textAnchor="middle" className="mini-label">{v.path.split("/").pop()}</text>
              </g>
            );
          })}
        </svg>
      </Panel>

      <Panel title="assets" className="span-3" scroll right={
        <span className="muted mono">{shown} / {visited.length}</span>
      }>
        <ul className="retrieved">
          {Object.entries(grouped).map(([type, items]) => (
            <React.Fragment key={type}>
              <li style={{ gridTemplateColumns: "1fr", color: "var(--fg-3)", fontSize: 10, textTransform: "uppercase", letterSpacing: ".08em", borderBottom: "1px solid var(--line-1)", paddingBottom: 4 }}>{type}</li>
              {items.map((v, i) => (
                <li key={i} className={v === visited[shown - 1] ? "is-current" : ""} style={v.sourceType === "reachability" ? { borderLeft: "2px solid var(--warn)" } : undefined}>
                  <span className="retrieved-score mono">{v.score.toFixed(2)}</span>
                  <span className="retrieved-path mono">{v.path}</span>
                  <span className="retrieved-hit mono">{v.hit}</span>
                </li>
              ))}
            </React.Fragment>
          ))}
        </ul>
      </Panel>

      <Panel title="stats" className="span-3">
        <div className="statsbar">
          <div className="stat"><div className="stat-v mono">{nodesExpanded}</div><div className="stat-k">nodes expanded</div></div>
          <div className="stat"><div className="stat-v mono">{edgesFollowed}</div><div className="stat-k">edges followed</div></div>
          <div className="stat"><div className="stat-v mono">—</div><div className="stat-k">latency</div></div>
          <div className="stat"><div className="stat-v mono">{visited.length}</div><div className="stat-k">total visited</div></div>
        </div>
      </Panel>
    </div>
  );
}

// ─── SSVCEvaluateView (decision gate) ───────────────────────────────────────

function SSVCEvaluateView({ node, content, progress, isLive }) {
  const { evaluated, dimensions, evalMethod, evalRule, evalLatencyMs, evalConfidence, branches, condition } = content;

  const tierStyle = {
    act_auto:       { bg: "rgba(239,106,106,.15)", color: "var(--err)" },
    act_supervised: { bg: "rgba(245,181,74,.15)",  color: "var(--warn)" },
    track:          { bg: "rgba(122,182,255,.15)",  color: "var(--info)" },
    defer:          { bg: "rgba(255,255,255,.06)",  color: "var(--fg-2)" },
  }[evaluated] || { bg: "rgba(255,255,255,.06)", color: "var(--fg-2)" };

  const tierReady = !isLive || progress >= 0.8;
  const takenBranch = branches.find(b => b.taken);

  const dimThresholds = [0.2, 0.4, 0.6, 0.8];

  return (
    <div className="nv-grid">
      <Panel className="span-3">
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", padding: "16px 0 12px", gap: 6 }}>
          {tierReady ? (
            <>
              <div className="mono" style={{ fontSize: 28, fontWeight: 600, padding: "6px 20px", borderRadius: 6, background: tierStyle.bg, color: tierStyle.color }}>{evaluated}</div>
              {takenBranch && <div className="mono muted" style={{ fontSize: 12 }}>{"→"} {takenBranch.target}</div>}
            </>
          ) : (
            <div className="streaming-bar" style={{ height: 44, width: "60%" }}><span className="streaming-bar-fill" /><span className="streaming-bar-label mono">evaluating SSVC dimensions...</span></div>
          )}
        </div>
      </Panel>

      <Panel title="eval method" className="span-1">
        <dl className="kv">
          <dt>method</dt><dd><Pill tone={evalMethod === "fathom_cache_hit" ? "ok" : "warn"}>{evalMethod.replace(/_/g, " ")}</Pill></dd>
          <dt>rule</dt><dd className="mono">{evalRule}</dd>
          <dt>latency</dt><dd className="mono">{evalLatencyMs}ms</dd>
          <dt>confidence</dt><dd className="mono">{evalConfidence}</dd>
        </dl>
      </Panel>

      <Panel title="decision matrix" className="span-2">
        {dimensions.map((dim, i) => {
          const visible = !isLive || progress >= dimThresholds[i];
          const gaugeColor = dim.level >= 0.8 ? "var(--err)" : dim.level >= 0.5 ? "var(--warn)" : "var(--info)";
          return (
            <div key={dim.name} style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 8 }}>
              <span className="mono" style={{ width: 100, textAlign: "right", fontSize: 11, color: "var(--fg-3)" }}>{dim.name}</span>
              <div style={{ flex: 1, height: 16, background: "rgba(255,255,255,.06)", borderRadius: 3, overflow: "hidden" }}>
                <div style={{ width: visible ? `${dim.level * 100}%` : "0%", height: "100%", background: gaugeColor, borderRadius: 3, transition: "width .3s" }} />
              </div>
              <span className="mono" style={{ width: 120, fontSize: 12, color: "var(--fg-1)" }}>{visible ? dim.value : ""}</span>
            </div>
          );
        })}
      </Panel>

      <Panel title="branches" className="span-3">
        <ul className="branches">
          {branches.map((b, i) => (
            <li key={i} className={"branch " + (b.taken ? "is-taken" : "is-skipped")}>
              <span className="branch-l">
                <span className="branch-arrow">{b.taken ? "→" : "↛"}</span>
                <span className="branch-label">{b.label}</span>
              </span>
              <span className="branch-r mono">{b.target}</span>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

Object.assign(window, { IntakeFetchView, ExtractTrustedView, CorrelateAssetsView, SSVCEvaluateView });
