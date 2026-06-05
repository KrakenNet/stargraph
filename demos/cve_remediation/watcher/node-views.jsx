// node-views.jsx — per-node-type live viewports

const { useState, useEffect, useRef, useMemo } = React;

// ─── shared bits ────────────────────────────────────────────────────────────

function ViewHeader({ node, status }) {
  const TYPE_LABEL = {
    source: "data source",
    llm: "language model",
    graph_traverse: "graph traversal",
    agent_loop: "agent loop",
    tool: "tool call",
    decision: "decision gate",
    start: "entry",
    end: "exit",
  };
  return (
    <header className="nv-head">
      <div className="nv-head-row">
        <div className="nv-eyebrow">
          <span className="nv-type">{TYPE_LABEL[node.type] || node.type}</span>
          <span className="nv-sep">/</span>
          <span className="nv-id">node·{node.id}</span>
        </div>
        <StatusPill status={status} />
      </div>
      <h1 className="nv-title">{node.label}</h1>
    </header>
  );
}

function StatusPill({ status }) {
  const m = {
    done:    { c: "done",    t: "completed" },
    running: { c: "running", t: "running" },
    pending: { c: "pending", t: "queued" },
    skipped: { c: "skipped", t: "skipped" },
  }[status] || { c: "pending", t: status };
  return (
    <span className={"nv-pill is-" + m.c}>
      {status === "running" && <span className="nv-pill-pulse" />}
      {m.t}
    </span>
  );
}

function Panel({ title, right, children, className = "", scroll = false, mono = false }) {
  return (
    <section className={"panel " + className}>
      {(title || right) && (
        <header className="panel-h">
          <span className="panel-t">{title}</span>
          {right && <span className="panel-r">{right}</span>}
        </header>
      )}
      <div className={"panel-b " + (scroll ? "is-scroll " : "") + (mono ? "is-mono" : "")}>
        {children}
      </div>
    </section>
  );
}

function Pending({ node }) {
  return (
    <div className="nv-pending">
      <div className="nv-pending-card">
        <div className="nv-pending-icon">◌</div>
        <div className="nv-pending-title">{node.label} hasn't started yet</div>
        <div className="nv-pending-sub">
          waiting on upstream nodes · estimated start in {Math.max(0, Math.round(node.startAt))}s
        </div>
      </div>
    </div>
  );
}

function Skipped({ node }) {
  return (
    <div className="nv-pending">
      <div className="nv-pending-card">
        <div className="nv-pending-icon">—</div>
        <div className="nv-pending-title">{node.label} was not taken</div>
        <div className="nv-pending-sub">
          the upstream decision gate routed elsewhere
        </div>
      </div>
    </div>
  );
}

// ─── source (DB) ────────────────────────────────────────────────────────────

function SourceView({ node, content, progress, isLive }) {
  const visibleRows = isLive && progress < 0.85 ? 0 : content.rows.length;
  return (
    <div className="nv-grid nv-grid--source">
      <Panel title="connection" className="span-1">
        <dl className="kv">
          <dt>system</dt><dd className="mono">postgres</dd>
          <dt>cluster</dt><dd className="mono">{content.system}</dd>
          <dt>role</dt><dd className="mono">{content.role}</dd>
          <dt>isolation</dt><dd className="mono">read committed</dd>
        </dl>
      </Panel>

      <Panel title="request" right={<span className="muted mono">cveId = "CVE-2021-44228"</span>} className="span-2" mono>
        <pre className="code">{content.query}</pre>
      </Panel>

      <Panel
        title="result"
        right={<span className="muted mono">{visibleRows} record{visibleRows === 1 ? "" : "s"}</span>}
        className="span-3"
      >
        {visibleRows === 0 ? (
          <div className="streaming-bar">
            <span className="streaming-bar-fill" />
            <span className="streaming-bar-label mono">
              streaming · fetched {Math.round(7 * progress)} of 7 references
            </span>
          </div>
        ) : (
          <div className="rowset">
            <div className="rowset-h mono">
              <span>cve_id</span><span>summary</span><span>severity</span><span>list</span><span>contact</span><span>refs</span>
            </div>
            {content.rows.map((r, i) => (
              <div className="rowset-r mono" key={i}>
                <span className="rowset-id">{r.id}</span>
                <span>{r.subject}</span>
                <span><Pill tone="warn">{r.priority}</Pill></span>
                <span><Pill tone="info">{r.plan}</Pill></span>
                <span className="muted">{r.email}</span>
                <span className="muted">{r.attachments}</span>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="stats" className="span-3">
        <div className="statsbar">
          {content.stats.map((s) => (
            <div className="stat" key={s.k}>
              <div className="stat-v mono">{s.v}</div>
              <div className="stat-k">{s.k}</div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}

// ─── llm ────────────────────────────────────────────────────────────────────

function LLMView({ node, content, progress, isLive }) {
  // stream tokens proportional to progress when live
  const fullResp = content.streamingResponse;
  const shown = isLive
    ? fullResp.slice(0, Math.floor(fullResp.length * Math.min(1, progress / 0.85)))
    : fullResp;
  const reasoningShown = isLive
    ? content.reasoning.slice(0, Math.ceil(content.reasoning.length * Math.min(1, progress / 0.7)))
    : content.reasoning;

  return (
    <div className="nv-grid nv-grid--llm">
      <Panel title="model" className="span-1">
        <dl className="kv">
          <dt>model</dt><dd className="mono">{content.model}</dd>
          <dt>temperature</dt><dd className="mono">0.0</dd>
          <dt>max_tokens</dt><dd className="mono">512</dd>
          <dt>prompt tokens</dt><dd className="mono">{content.promptTokens.toLocaleString()}</dd>
          <dt>output tokens</dt>
          <dd className="mono">{Math.floor((shown.length / 4))}</dd>
        </dl>
      </Panel>

      <Panel
        title="system prompt"
        right={<button className="ghost-btn">expand</button>}
        className="span-2"
      >
        <p className="prose-clip">{content.systemPreview}</p>
      </Panel>

      <Panel title="reasoning trace" className="span-3" right={
        <span className="muted mono">{reasoningShown.length} / {content.reasoning.length}</span>
      }>
        <ol className="reason">
          {reasoningShown.map((r, i) => (
            <li key={i}><span className="reason-n">{i + 1}</span>{r}</li>
          ))}
          {isLive && reasoningShown.length < content.reasoning.length && (
            <li className="reason-typing">
              <span className="reason-n">{reasoningShown.length + 1}</span>
              <ShimmerLine />
            </li>
          )}
        </ol>
      </Panel>

      <Panel title="response" className="span-3" mono right={
        isLive && shown.length < fullResp.length
          ? <span className="muted mono">streaming…</span>
          : <span className="muted mono">complete</span>
      }>
        <pre className="code code-json">
          {shown}
          {isLive && shown.length < fullResp.length && <span className="caret">█</span>}
        </pre>
      </Panel>
    </div>
  );
}

// ─── graph traversal ────────────────────────────────────────────────────────

function GraphTraverseView({ node, content, progress, isLive }) {
  const total = content.visited.length;
  const shown = isLive ? Math.max(1, Math.floor(total * Math.min(1, progress / 0.9))) : total;
  const current = content.visited[Math.min(shown - 1, total - 1)];

  return (
    <div className="nv-grid nv-grid--search">
      <Panel title="index" className="span-1">
        <dl className="kv">
          <dt>root</dt><dd className="mono">{content.root}</dd>
          <dt>indexed</dt><dd>{content.indexedAt}</dd>
          <dt>strategy</dt><dd className="mono">embed + bm25</dd>
          <dt>nodes</dt><dd className="mono">{content.nodesExpanded} expanded</dd>
          <dt>edges</dt><dd className="mono">{content.edgesFollowed} followed</dd>
        </dl>
      </Panel>

      <Panel title="cursor" className="span-2">
        <div className="cursor-card">
          <div className="cursor-label">currently reading</div>
          <div className="cursor-path mono">{current.path}</div>
          <div className="cursor-hit mono">
            <span className="cursor-arrow">→</span>
            <span>{current.hit}</span>
          </div>
        </div>
      </Panel>

      <Panel title="retrieved" className="span-3" right={
        <span className="muted mono">{shown} / {total}</span>
      } scroll>
        <ul className="retrieved">
          {content.visited.slice(0, shown).map((v, i) => (
            <li key={i} className={i === shown - 1 ? "is-current" : ""}>
              <span className="retrieved-score mono">{v.score.toFixed(2)}</span>
              <span className="retrieved-path mono">{v.path}</span>
              <span className="retrieved-hit mono">{v.hit}</span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="graph" className="span-3">
        <MiniTraverseGraph visited={content.visited} shown={shown} />
      </Panel>
    </div>
  );
}

function MiniTraverseGraph({ visited, shown }) {
  // arrange visited nodes around a center "query" node
  const cx = 280, cy = 110, r = 80;
  return (
    <svg className="mini-graph" viewBox="0 0 560 220">
      <circle cx={cx} cy={cy} r="18" fill="var(--accent-dim)" stroke="var(--accent)" />
      <text x={cx} y={cy + 4} textAnchor="middle" className="mini-label">query</text>
      {visited.map((v, i) => {
        const angle = (i / visited.length) * Math.PI * 2 - Math.PI / 2;
        const x = cx + Math.cos(angle) * (r + 70);
        const y = cy + Math.sin(angle) * (r + 30);
        const active = i < shown;
        const current = i === shown - 1;
        return (
          <g key={i} opacity={active ? 1 : 0.35}>
            <line
              x1={cx} y1={cy} x2={x} y2={y}
              stroke={current ? "var(--accent)" : active ? "var(--edge-bright)" : "var(--edge)"}
              strokeWidth={current ? 1.6 : 1}
              strokeDasharray={active ? "none" : "2 3"}
            />
            <circle
              cx={x} cy={y} r={current ? 7 : 5}
              fill={current ? "var(--accent)" : active ? "var(--ok)" : "var(--mute-2)"}
            />
            <text x={x} y={y - 11} textAnchor="middle" className="mini-label">
              {v.path.split("/").pop()}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ─── agent loop (the hero view) ─────────────────────────────────────────────

function AgentLoopView({ node, content, progress, isLive, clockInNode }) {
  // thoughts stream in based on clockInNode
  const thoughts = content.thoughts.filter(t => clockInNode >= t.t);
  const thoughtsRef = useRef(null);
  useEffect(() => {
    if (thoughtsRef.current) {
      thoughtsRef.current.scrollTop = thoughtsRef.current.scrollHeight;
    }
  }, [thoughts.length]);

  // typewriter on code — duration-relative so the reveal scales with the node.
  const code = useMemo(() => {
    if (!isLive) return content.code;
    const dur = node.duration || 40;
    const startReveal = dur * 0.4;
    const span = Math.max(1, dur - startReveal - 2);
    const local = Math.max(0, clockInNode - startReveal);
    const frac = Math.min(1, local / span);
    const cutAt = Math.floor(content.code.length * frac);
    return content.code.slice(0, Math.max(cutAt, 1));
  }, [clockInNode, isLive, content.code, node.duration]);

  const logsShown = Math.max(1, Math.floor(content.logs.length * Math.min(1, clockInNode / Math.max(1, node.duration - 2))));
  const logs = content.logs.slice(0, logsShown);

  // Running test flips to pass when the node is ~85% complete.
  const passThreshold = (node.duration || 40) * 0.85;
  const tests = content.tests.map((t) => {
    if (t.status === "running" && clockInNode > passThreshold) return { ...t, status: "pass", ms: 18 };
    return t;
  });

  return (
    <div className="agent-grid">
      {/* status strip */}
      <div className="agent-strip">
        <div className="agent-strip-l">
          <span className="agent-iter">iteration <b>{content.iteration}</b> of {content.maxIterations}</span>
          <span className="sep">·</span>
          <span><span className="muted">elapsed</span> <b className="mono">{content.elapsed}s</b></span>
          <span className="sep">·</span>
          <span><span className="muted">tokens</span> <b className="mono">{content.tokens.toLocaleString()}</b></span>
          <span className="sep">·</span>
          <span><span className="muted">tool calls</span> <b className="mono">{logsShown}</b></span>
        </div>
        <div className="agent-strip-r">
          <button className="ghost-btn">pause agent</button>
          <button className="ghost-btn">interject</button>
        </div>
      </div>

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
          {isLive && (
            <div className="thought thought-typing">
              <div className="thought-meta">
                <span className="thought-tag thought-tag-typing">thinking</span>
              </div>
              <ShimmerLine />
            </div>
          )}
        </div>
      </Panel>

      <Panel
        title={<>
          <span className="file-tab is-active">
            <span className="file-dot" />
            {content.file}
          </span>
          <span className="file-tab muted">advisory_refs.json</span>
          <span className="file-tab muted">registry_probe.json</span>
        </>}
        className="agent-code"
        right={
          <span className="muted mono">
            schema=RemediationCandidate · json
          </span>
        }
        mono
      >
        <div className="codepane">
          <div className="codepane-gutter">
            {code.split("\n").map((_, i) => (
              <span key={i}>{i + 1}</span>
            ))}
          </div>
          <pre className="codepane-text">{code}</pre>
        </div>
      </Panel>

      <Panel title="test results" className="agent-tests" right={
        <span className="muted mono">
          {tests.filter(t => t.status === "pass").length} pass · {tests.filter(t => t.status === "fail").length} fail · {tests.filter(t => t.status === "running").length} running
        </span>
      }>
        <ul className="tests">
          {tests.map((t, i) => (
            <li key={i} className={"test test-" + t.status}>
              <span className="test-status">
                {t.status === "pass" && <span className="test-dot test-dot-pass">✓</span>}
                {t.status === "fail" && <span className="test-dot test-dot-fail">✕</span>}
                {t.status === "running" && <span className="test-dot test-dot-run"><span className="gp-spin" /></span>}
              </span>
              <span className="test-name">{t.name}</span>
              <span className="test-ms mono">{t.ms != null ? t.ms + "ms" : "—"}</span>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="tool log" className="agent-logs" mono>
        <ul className="loglist">
          {logs.map((l, i) => (
            <li key={i}>{l}</li>
          ))}
          {isLive && (
            <li className="loglist-typing">
              <span className="caret">█</span>
            </li>
          )}
        </ul>
      </Panel>
    </div>
  );
}

// ─── tool ───────────────────────────────────────────────────────────────────

function ToolView({ node, content, progress, isLive }) {
  const showResponse = !isLive || progress > 0.6;
  if (node.id === "sandbox_run") {
    const outLen = content.output.length;
    const visible = isLive ? content.output.slice(0, Math.floor(outLen * Math.min(1, progress / 0.9))) : content.output;
    return (
      <div className="nv-grid nv-grid--tool">
        <Panel title="command" className="span-2" mono>
          <pre className="code">$ {content.cmd}</pre>
        </Panel>
        <Panel title="exit" className="span-1">
          <div className="bigstat">
            <div className={"bigstat-v " + (content.exitCode === 0 ? "ok" : "fail")}>
              {isLive && progress < 0.95 ? "—" : content.exitCode}
            </div>
            <div className="bigstat-k">{isLive && progress < 0.95 ? "running" : "exit code"}</div>
          </div>
        </Panel>
        <Panel title="stdout" className="span-3" mono>
          <pre className="code code-term">
            {visible}
            {isLive && visible.length < outLen && <span className="caret">█</span>}
          </pre>
        </Panel>
      </div>
    );
  }
  return (
    <div className="nv-grid nv-grid--tool">
      <Panel title="tool" className="span-1">
        <dl className="kv">
          <dt>name</dt><dd className="mono">{content.tool}</dd>
          <dt>kind</dt><dd>external</dd>
          <dt>auth</dt><dd className="mono">oauth · scope:write</dd>
        </dl>
      </Panel>
      <Panel title="arguments" className="span-2" mono>
        <pre className="code code-json">{JSON.stringify(content.args, null, 2)}</pre>
      </Panel>
      <Panel title="response" className="span-3" mono right={
        <span className="muted mono">{showResponse ? "received" : "awaiting…"}</span>
      }>
        {showResponse ? (
          <pre className="code code-json">{JSON.stringify(toolResponseFor(node), null, 2)}</pre>
        ) : (
          <div className="streaming-bar">
            <span className="streaming-bar-fill" />
            <span className="streaming-bar-label mono">awaiting response from {content.tool}…</span>
          </div>
        )}
      </Panel>
    </div>
  );
}

// ─── decision ───────────────────────────────────────────────────────────────

function DecisionView({ node, content, progress, isLive }) {
  return (
    <div className="nv-grid nv-grid--decision">
      <Panel title="condition" className="span-3" mono>
        <pre className="code">{content.condition}</pre>
      </Panel>
      <Panel title="evaluated to" className="span-1">
        <div className="bigstat">
          <div className="bigstat-v ok">true</div>
          <div className="bigstat-k">boolean</div>
        </div>
      </Panel>
      <Panel title="branches" className="span-2">
        <ul className="branches">
          {content.branches.map((b, i) => (
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

// ─── sentinels ──────────────────────────────────────────────────────────────

function SentinelView({ node }) {
  return (
    <div className="nv-pending">
      <div className="nv-pending-card">
        <div className="nv-pending-icon">{node.type === "start" ? "▶" : "■"}</div>
        <div className="nv-pending-title">{node.type === "start" ? "run started" : "run terminated"}</div>
        <div className="nv-pending-sub">{WORKGRAPH.runId}</div>
      </div>
    </div>
  );
}

// ─── shimmer line (typing indicator) ───────────────────────────────────────

function ShimmerLine() {
  return (
    <div className="shimmer">
      <span /><span /><span />
    </div>
  );
}

function Pill({ tone, children }) {
  return <span className={"pill pill-" + tone}>{children}</span>;
}

// Per-node-id synthetic tool response. Real WS mode replaces this with the
// ToolResultEvent payload; in simulated mode it stands in so the design
// renders fully-populated.
function toolResponseFor(node) {
  if (node.id === "open_change_request") {
    return {
      ok: true,
      table: "change_request",
      number: "CHG0041997",
      sys_id: "7c91d2f4a3b14e29ad1f8b2c0e6a4b58",
      url: "https://kraken.service-now.com/change_request.do?CHG0041997",
      attached_doc_plus: "doc+://collection/vuln-summaries/CVE-2021-44228",
      attestation: "ed25519:7c91…ab8d",
      createdAt: new Date().toISOString(),
    };
  }
  if (node.id === "retro_analysis") {
    return {
      ok: true,
      signals: ["static_detection_skip=false", "retro_template_lookup_hit=true"],
      suggestions: [
        { id: "S1", text: "Add lab profile java/11-corretto",     cite: "outcomes://F12/2026-04" },
        { id: "S2", text: "Pin maven-central mirror for log4j-*", cite: "doc+://policies/registry-pin" },
        { id: "S3", text: "Extend retro KG with log4j family edge", cite: "kg://retros/CVE-2021-45046" },
      ],
    };
  }
  return { ok: true, status: "queued", node: node.id };
}

// ─── dispatcher ─────────────────────────────────────────────────────────────

function NodeView({ node, status, clock }) {
  if (status === "pending") return <><ViewHeader node={node} status={status} /><Pending node={node} /></>;
  if (status === "skipped") return <><ViewHeader node={node} status={status} /><Skipped node={node} /></>;

  if (node.type === "start" || node.type === "end") {
    return <><ViewHeader node={node} status={status} /><SentinelView node={node} /></>;
  }

  const content = NODE_CONTENT[node.id];
  if (!content) {
    return <><ViewHeader node={node} status={status} /><Pending node={node} /></>;
  }
  const isLive = status === "running";
  const progress = isLive
    ? Math.max(0, Math.min(1, (clock - node.startAt) / Math.max(1, node.duration)))
    : 1;
  const clockInNode = Math.max(0, clock - node.startAt);

  const NODE_VIEW = {
    intake_fetch:          window.IntakeFetchView,
    extract_trusted:       window.ExtractTrustedView,
    correlate_assets:      window.CorrelateAssetsView,
    ssvc_evaluate:         window.SSVCEvaluateView,
    plan_template_lookup:  window.PlanTemplateLookupView,
    remediation_discovery: window.RemediationDiscoveryView,
    sandbox_run:           window.SandboxRunView,
    retro_analysis:        window.RetroAnalysisView,
    open_change_request:   window.OpenChangeRequestView,
    tier_terminal_track:   window.TierTerminalTrackView,
  };

  let Body = NODE_VIEW[node.id];
  if (!Body) {
    switch (node.type) {
      case "source":         Body = SourceView; break;
      case "llm":            Body = LLMView; break;
      case "graph_traverse": Body = GraphTraverseView; break;
      case "agent_loop":     Body = AgentLoopView; break;
      case "tool":           Body = ToolView; break;
      case "decision":       Body = DecisionView; break;
      default:               Body = Pending;
    }
  }

  return (
    <>
      <ViewHeader node={node} status={status} />
      <Body node={node} content={content} progress={progress} isLive={isLive} clockInNode={clockInNode} />
    </>
  );
}

Object.assign(window, { NodeView, Panel, StatusPill, ShimmerLine, Pill, ViewHeader, Pending, Skipped });
