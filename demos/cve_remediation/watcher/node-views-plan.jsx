// node-views-plan.jsx — PlanTemplateLookupView + RemediationDiscoveryView
// Buildless React components. Globals: React, Panel, Pill, ShimmerLine.

const { useMemo, useEffect, useRef } = React;

// ─── PlanTemplateLookupView ───────────────────────────────────────────────

function PlanTemplateLookupView({ node, content, progress, isLive, clockInNode }) {
  const w = content.winner;
  const isHit = !!w;
  const visibleRows = content.visited.filter(v => clockInNode >= v.t);

  // SVG: inputs on left, template results on right
  const inputs = [content.queryInputs.cwe, content.queryInputs.assetClass, content.queryInputs.runtime];
  const tpls = content.visited;
  const litCount = isLive ? [0.25, 0.5, 0.75].filter(g => progress >= g).length : 3;
  const allLit = !isLive || progress >= 1.0;

  return (
    <div className="nv-grid">
      {/* Row 1: HIT/MISS bigstat + Mini KG SVG */}
      <Panel title="match" className="span-1">
        <div className="bigstat">
          <div className="bigstat-v" style={{ color: isHit ? "var(--ok)" : "var(--warn)" }}>
            {isHit ? "HIT" : "MISS"}
          </div>
          <div className="bigstat-k mono">{isHit ? w.score.toFixed(2) : "—"}</div>
          {isHit && <div className="mono muted" style={{ fontSize: 11, marginTop: 4 }}>{w.path}</div>}
        </div>
      </Panel>

      <Panel title="query graph" className="span-2">
        <svg viewBox="0 0 480 160" style={{ width: "100%", height: 140 }}>
          {/* input nodes */}
          {inputs.map((label, i) => {
            const y = 30 + i * 50;
            const lit = isLive ? i < litCount : true;
            return (
              <g key={"in-" + i} opacity={lit ? 1 : 0.3}>
                <rect x={10} y={y - 14} width={90} height={28} rx={4}
                  fill="var(--bg-2)" stroke="var(--accent)" strokeWidth={1} />
                <text x={55} y={y + 4} textAnchor="middle" className="mini-label">{label}</text>
                <line x1={100} y1={y} x2={150} y2={y} stroke="var(--edge-bright)" strokeWidth={1} />
              </g>
            );
          })}
          {/* merge node */}
          <circle cx={170} cy={80} r={8} fill="var(--bg-2)" stroke="var(--edge-bright)" opacity={litCount >= 2 ? 1 : 0.3} />
          {inputs.map((_, i) => (
            <line key={"merge-" + i} x1={150} y1={30 + i * 50} x2={162} y2={80}
              stroke="var(--edge-bright)" strokeWidth={1} opacity={litCount >= 2 ? 1 : 0.3} />
          ))}
          {/* fan-out to template nodes */}
          {tpls.map((t, i) => {
            const y = 20 + i * 36;
            const isWinner = w && t.path === w.path;
            const visible = allLit || clockInNode >= t.t;
            return (
              <g key={"tpl-" + i} opacity={visible ? 1 : 0.3}>
                <line x1={178} y1={80} x2={300} y2={y}
                  stroke={isWinner ? "var(--ok)" : "var(--edge-bright)"} strokeWidth={1} />
                <rect x={300} y={y - 12} width={170} height={24} rx={4}
                  fill={isWinner ? "var(--ok-dim)" : "var(--bg-2)"}
                  stroke={isWinner ? "var(--ok)" : t.isRetro ? "var(--info)" : "var(--edge-bright)"}
                  strokeWidth={isWinner ? 2 : 1}
                  strokeDasharray={t.isRetro ? "4 3" : "none"} />
                <text x={385} y={y + 3} textAnchor="middle" className="mini-label">
                  {t.path.split("/").pop()}
                </text>
              </g>
            );
          })}
        </svg>
      </Panel>

      {/* Row 2: Template ranking */}
      <Panel title="templates" className="span-3" right={
        <span className="muted mono">{visibleRows.length} / {content.visited.length}</span>
      } scroll>
        <ul className="retrieved">
          {visibleRows.map((v, i) => (
            <li key={i} className={w && v.path === w.path ? "is-current" : ""}
              style={w && v.path === w.path ? { background: "var(--ok-dim)" } : undefined}>
              <span className="retrieved-score mono">{v.score.toFixed(2)}</span>
              <span className="retrieved-path mono">{v.path}</span>
              <span className="retrieved-hit mono">{v.hit}</span>
              {v.isRetro && <Pill tone="info">retro</Pill>}
            </li>
          ))}
          {isLive && visibleRows.length < content.visited.length && <ShimmerLine />}
        </ul>
      </Panel>

      {/* Row 3: Retro overlap callout */}
      {content.retroOverlap && (
        <Panel className="span-3">
          <div style={{ borderLeft: "3px solid var(--info)", padding: "8px 12px" }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>
              {"⟲ "}{content.retroOverlap.cve} retro informs this plan
            </div>
            <div className="mono muted" style={{ fontSize: 12 }}>{content.retroOverlap.insight}</div>
          </div>
        </Panel>
      )}

      {/* Row 4: Statsbar */}
      <Panel title="stats" className="span-3">
        <div className="statsbar">
          <div className="stat"><div className="stat-v mono">{content.edgesFollowed}</div><div className="stat-k">edges</div></div>
          <div className="stat"><div className="stat-v mono">{content.nodesExpanded}</div><div className="stat-k">nodes</div></div>
          <div className="stat"><div className="stat-v mono">{content.visited.length}</div><div className="stat-k">matches</div></div>
          <div className="stat"><div className="stat-v mono">{node.duration ? node.duration + "s" : "—"}</div><div className="stat-k">wall time</div></div>
        </div>
      </Panel>
    </div>
  );
}

// ─── RemediationDiscoveryView ─────────────────────────────────────────────

function RemediationDiscoveryView({ node, content, progress, isLive, clockInNode }) {
  const dur = node.duration || 40;
  const confirmed = content.sources.filter(s => clockInNode >= s.confirmedAt);
  const allConfirmed = confirmed.length === content.sources.length;

  // thoughts stream
  const thoughts = content.thoughts.filter(t => clockInNode >= t.t);
  const thoughtsRef = useRef(null);
  useEffect(() => {
    if (thoughtsRef.current) thoughtsRef.current.scrollTop = thoughtsRef.current.scrollHeight;
  }, [thoughts.length]);

  // code typewriter
  const code = useMemo(() => {
    if (!isLive) return content.code;
    const startReveal = dur * 0.4;
    const span = Math.max(1, dur - startReveal - 2);
    const local = Math.max(0, clockInNode - startReveal);
    const frac = Math.min(1, local / span);
    const cutAt = Math.floor(content.code.length * frac);
    return content.code.slice(0, Math.max(cutAt, 1));
  }, [clockInNode, isLive, content.code, dur]);

  // logs
  const logsShown = Math.max(1, Math.floor(content.logs.length * Math.min(1, clockInNode / Math.max(1, dur - 2))));
  const logs = content.logs.slice(0, logsShown);

  // tests: flip based on source confirmedAt, last test at 85%
  const passThreshold = dur * 0.85;
  const tests = content.tests.map((t, i) => {
    if (t.status !== "running") return t;
    if (i < content.sources.length && clockInNode >= content.sources[i].confirmedAt) return { ...t, status: "pass", ms: t.ms ?? 18 };
    if (i >= content.sources.length && clockInNode > passThreshold) return { ...t, status: "pass", ms: 18 };
    return t;
  });

  const cellStyle = (src) => {
    const done = clockInNode >= src.confirmedAt;
    return {
      flex: 1, padding: "10px 12px", borderRadius: 4, minWidth: 0,
      background: done ? "var(--ok-dim)" : "var(--bg-2)",
      opacity: done ? 1 : 0.5, transition: "background 0.3s, opacity 0.3s",
    };
  };

  return (
    <div className="agent-grid">
      {/* Agent strip */}
      <div className="agent-strip">
        <div className="agent-strip-l">
          <span className="agent-iter">iteration <b>{content.iteration}</b> of {content.maxIterations}</span>
          <span className="sep">{"·"}</span>
          <span><span className="muted">elapsed</span> <b className="mono">{content.elapsed}s</b></span>
          <span className="sep">{"·"}</span>
          <span><span className="muted">tokens</span> <b className="mono">{content.tokens.toLocaleString()}</b></span>
          <span className="sep">{"·"}</span>
          <span><span className="muted">tool calls</span> <b className="mono">{logsShown}</b></span>
        </div>
        <div className="agent-strip-r">
          <button className="ghost-btn">pause agent</button>
          <button className="ghost-btn">interject</button>
        </div>
      </div>

      {/* Source convergence matrix — full width between strip and thoughts */}
      <div style={{ gridColumn: "1 / -1", display: "flex", gap: 8, padding: "0 0 4px" }}>
        {content.sources.map(src => {
          const done = clockInNode >= src.confirmedAt;
          const inflight = isLive && !done && clockInNode >= (src.confirmedAt - 4);
          return (
            <div key={src.id} style={cellStyle(src)}>
              <div style={{ fontWeight: 600, fontSize: 12 }}>{src.label}</div>
              <div className="muted" style={{ fontSize: 11 }}>{src.sublabel}</div>
              {inflight && !done && (
                <div className="streaming-bar" style={{ marginTop: 6, height: 4 }}>
                  <span className="streaming-bar-fill" />
                </div>
              )}
              {done && (
                <>
                  <div className="mono" style={{ fontWeight: 700, fontSize: 14, marginTop: 6 }}>{src.version}</div>
                  <Pill tone="ok">confirmed</Pill>
                  <div className="muted" style={{ fontSize: 11, marginTop: 4 }}>{src.detail}</div>
                </>
              )}
            </div>
          );
        })}
      </div>

      {/* Convergence meter */}
      <div style={{ gridColumn: "1 / -1", padding: "0 0 4px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div style={{ flex: 1, height: 6, background: "var(--bg-2)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{
              width: (confirmed.length / content.sources.length * 100) + "%",
              height: "100%", borderRadius: 3, transition: "width 0.3s",
              background: allConfirmed ? "var(--ok)" : "var(--accent)",
            }} />
          </div>
          <span className="muted mono" style={{ fontSize: 11, flexShrink: 0 }}>
            {confirmed.length}/{content.sources.length} sources
          </span>
        </div>
      </div>

      {/* Consensus banner */}
      {allConfirmed && content.consensus && (
        <div style={{
          gridColumn: "1 / -1", textAlign: "center", padding: "12px 16px",
          background: "var(--ok-dim)", borderRadius: 4,
        }}>
          <div className="mono" style={{ fontSize: 24, fontWeight: 700 }}>{content.consensus.version}</div>
          <div className="muted" style={{ fontSize: 12, marginTop: 2 }}>
            promoted: {content.consensus.version} {"·"} {content.consensus.sourceCount}/{content.sources.length} sources {"·"} {content.consensus.confidence} confidence
          </div>
        </div>
      )}

      {/* Thought stream */}
      <Panel title="thought stream" className="agent-thoughts" right={
        <span className="muted mono">{thoughts.length} entries</span>
      }>
        <div className="thought-list" ref={thoughtsRef}>
          {thoughts.map((th, i) => (
            <div key={i} className={"thought thought-" + th.k}>
              <div className="thought-meta">
                <span className={"thought-tag thought-tag-" + th.k}>{th.k}</span>
                <span className="thought-time mono">+{th.t}s</span>
              </div>
              <div className="thought-text">{th.text}</div>
            </div>
          ))}
          {isLive && <div className="thought thought-typing"><div className="thought-meta"><span className="thought-tag thought-tag-typing">thinking</span></div><ShimmerLine /></div>}
        </div>
      </Panel>

      {/* Code pane */}
      <Panel title={<><span className="file-tab is-active"><span className="file-dot" />{content.file}</span></>}
        className="agent-code" mono right={<span className="muted mono">schema=RemediationCandidate</span>}>
        <div className="codepane">
          <div className="codepane-gutter">{code.split("\n").map((_, i) => <span key={i}>{i + 1}</span>)}</div>
          <pre className="codepane-text">{code}{isLive && code.length < content.code.length && <span className="caret">{"█"}</span>}</pre>
        </div>
      </Panel>

      {/* Test results */}
      <Panel title="test results" className="agent-tests" right={
        <span className="muted mono">
          {tests.filter(t => t.status === "pass").length} pass {"·"} {tests.filter(t => t.status === "running").length} running
        </span>
      }>
        <ul className="tests">
          {tests.map((t, i) => (
            <li key={i} className={"test test-" + t.status}>
              <span className="test-status">
                {t.status === "pass" && <span className="test-dot test-dot-pass">{"✓"}</span>}
                {t.status === "running" && <span className="test-dot test-dot-run"><span className="gp-spin" /></span>}
              </span>
              <span className="test-name">{t.name}</span>
              <span className="test-ms mono">{t.ms != null ? t.ms + "ms" : "—"}</span>
            </li>
          ))}
        </ul>
      </Panel>

      {/* Tool log */}
      <Panel title="tool log" className="agent-logs" mono>
        <ul className="loglist">
          {logs.map((l, i) => <li key={i}>{l}</li>)}
          {isLive && <li className="loglist-typing"><span className="caret">{"█"}</span></li>}
        </ul>
      </Panel>
    </div>
  );
}

Object.assign(window, { PlanTemplateLookupView, RemediationDiscoveryView });
