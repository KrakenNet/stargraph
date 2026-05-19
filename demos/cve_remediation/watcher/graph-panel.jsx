// graph-panel.jsx — rich vertical run timeline (workgraph as step cards)

const STATUS_LABEL = {
  done: "completed",
  running: "running",
  pending: "queued",
  skipped: "skipped",
};

const TYPE_GLYPH = {
  start:          "▶",
  end:            "■",
  source:         "DB",
  llm:            "LM",
  graph_traverse: "GR",
  agent_loop:     "AG",
  tool:           "FN",
  decision:       "?",
};

const TYPE_LABEL = {
  start:          "entry",
  end:            "exit",
  source:         "data source",
  llm:            "language model",
  graph_traverse: "graph traversal",
  agent_loop:     "agent loop",
  tool:           "tool call",
  decision:       "decision gate",
};

function statusFor(node, clock) {
  if (node.type === "start") return "done";
  if (node.type === "end")   return clock >= WORKGRAPH.totalDuration ? "done" : "pending";
  if (clock >= node.endAt)   return "done";
  if (clock >= node.startAt) return "running";
  // Branch not taken — the upstream decision gate chose a different label.
  if (node.branchOf) {
    const gate = WORKGRAPH.nodes.find((g) => g.id === node.branchOf);
    const gateContent = (typeof NODE_CONTENT !== "undefined") && NODE_CONTENT[node.branchOf];
    if (gate && gateContent && gateContent.branches) {
      const taken = gateContent.branches.find((b) => b.taken);
      if (taken && node.branchLabel && taken.label !== node.branchLabel) {
        return "skipped";
      }
    }
  }
  return "pending";
}

function nodeProgress(node, clock) {
  if (clock <= node.startAt) return 0;
  if (clock >= node.endAt)   return 1;
  return (clock - node.startAt) / (node.endAt - node.startAt);
}

// Group nodes into "stem" rows. Branches (branchOf set) collapse into a 2-column row.
function buildRows() {
  const rows = [];
  const branchCols = {}; // gateId -> branch nodes accumulator
  for (const n of WORKGRAPH.nodes) {
    if (n.branchOf) {
      const existing = rows.find(r => r.kind === "branches" && r.gateId === n.branchOf);
      if (existing) existing.nodes.push(n);
      else rows.push({ kind: "branches", gateId: n.branchOf, nodes: [n] });
    } else {
      rows.push({ kind: "stem", node: n });
    }
  }
  return rows;
}

function GraphPanel({ clock, selectedId, onSelect, side }) {
  const rows = buildRows();
  const scrollRef = React.useRef(null);

  // auto-scroll running card into view (only when nothing is manually selected)
  React.useEffect(() => {
    if (!scrollRef.current) return;
    if (selectedId) return;
    const running = WORKGRAPH.nodes.find(n => statusFor(n, clock) === "running");
    if (!running) return;
    const el = scrollRef.current.querySelector(`[data-node-id="${running.id}"]`);
    if (!el) return;
    const cont = scrollRef.current;
    const r = el.getBoundingClientRect();
    const cr = cont.getBoundingClientRect();
    if (r.top < cr.top + 40 || r.bottom > cr.bottom - 40) {
      cont.scrollTo({
        top: el.offsetTop - cont.clientHeight / 2 + el.offsetHeight / 2,
        behavior: "smooth",
      });
    }
  }, [clock, selectedId]);

  const stepNumbers = {};
  let counter = 0;
  for (const n of WORKGRAPH.nodes) {
    if (n.type === "start" || n.type === "end") continue;
    counter += 1;
    stepNumbers[n.id] = counter;
  }

  return (
    <aside className="graph-panel" data-side={side}>
      <header className="gp-head">
        <div className="gp-head-row">
          <div className="gp-title">
            <span className="gp-dot" />
            <span className="gp-name">{WORKGRAPH.name}</span>
          </div>
          <span className="gp-live">
            <span className="gp-pulse" />live
          </span>
        </div>
        <div className="gp-meta">
          <span className="gp-runid mono">{WORKGRAPH.runId}</span>
          <span className="gp-sep">·</span>
          <span>started {WORKGRAPH.startedAt}</span>
          <span className="gp-sep">·</span>
          <span>{Math.round(clock)}s elapsed</span>
        </div>
        <RunMiniMap clock={clock} selectedId={selectedId} onSelect={onSelect} />
      </header>

      <div className="gp-scroll" ref={scrollRef}>
        <div className="gp-rows">
          {rows.map((r, i) => {
            if (r.kind === "stem") {
              const isLast = i === rows.length - 1;
              return (
                <div key={r.node.id} data-node-id={r.node.id}>
                  <StepCard
                    node={r.node}
                    stepNum={stepNumbers[r.node.id]}
                    status={statusFor(r.node, clock)}
                    progress={nodeProgress(r.node, clock)}
                    selected={r.node.id === selectedId}
                    onSelect={onSelect}
                    isLast={isLast}
                    clock={clock}
                  />
                </div>
              );
            }
            // branches row
            const gate = WORKGRAPH.nodes.find(n => n.id === r.gateId);
            const gateDone = statusFor(gate, clock) === "done";
            return (
              <div key={"br-" + r.gateId} className="gp-branches">
                <BranchFork gateDone={gateDone} />
                <div className="gp-branches-row">
                  {r.nodes.map(n => (
                    <div key={n.id} data-node-id={n.id}>
                      <StepCard
                        node={n}
                        stepNum={stepNumbers[n.id]}
                        status={statusFor(n, clock)}
                        progress={nodeProgress(n, clock)}
                        selected={n.id === selectedId}
                        onSelect={onSelect}
                        compact
                        isLast
                        clock={clock}
                      />
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <footer className="gp-foot">
        <Legend />
      </footer>
    </aside>
  );
}

// helper — convert HH:MM:SS + offset seconds to HH:MM:SS
function fmtClock(baseStr, offsetSec) {
  const [h, m, s] = baseStr.split(":").map(Number);
  let total = h * 3600 + m * 60 + s + Math.floor(offsetSec);
  const hh = String(Math.floor(total / 3600) % 24).padStart(2, "0");
  const mm = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const ss = String(total % 60).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

// ─── Step card ──────────────────────────────────────────────────────────────

function StepCard({ node, stepNum, status, progress, selected, onSelect, isLast, compact, clock }) {
  const isSentinel = node.type === "start" || node.type === "end";
  const stats = node.stats ? node.stats() : null;
  const elapsedInNode = Math.max(0, Math.min(node.duration || 0, progress * (node.duration || 0)));
  const startedClock = node.startAt != null ? fmtClock(WORKGRAPH.startedAt, node.startAt) : null;
  const endedClock = node.endAt != null ? fmtClock(WORKGRAPH.startedAt, node.endAt) : null;
  const currentAction = (status === "running" && node.currentAction)
    ? node.currentAction(Math.max(0, clock - node.startAt))
    : null;

  return (
    <div className={"sc-wrap " + (isLast ? "is-last " : "") + (compact ? "is-compact" : "")}>
      <div className="sc-rail">
        <div className={"sc-rail-dot is-" + status}>
          {status === "running" && <span className="sc-rail-pulse" />}
        </div>
        {!isLast && <div className={"sc-rail-line is-" + status} />}
      </div>

      <button
        className={"sc-card is-" + status + (selected ? " is-selected" : "") + (isSentinel ? " is-sentinel" : "") + (node.loop ? " has-loop" : "")}
        onClick={() => onSelect(node.id)}
        type="button"
      >
        <div className="sc-top">
          <div className="sc-top-l">
            {!isSentinel && <span className="sc-step mono">{String(stepNum).padStart(2, "0")}</span>}
            <span className={"sc-glyph is-" + status}>{TYPE_GLYPH[node.type]}</span>
            <span className="sc-name">{node.label}</span>
          </div>
          <span className={"sc-status is-" + status}>
            {status === "running" && <span className="gp-spin" />}
            {status === "done"    && <span className="sc-ok">✓</span>}
            {status === "skipped" && <span className="sc-skip">—</span>}
            {status === "pending" && <span className="sc-pend">◌</span>}
            <span className="sc-status-t">{STATUS_LABEL[status]}</span>
          </span>
        </div>

        {!isSentinel && (
          <>
            <div className="sc-type-row">
              <span className="sc-type mono">{TYPE_LABEL[node.type]}</span>
              <span className="sc-sep">·</span>
              <span className="sc-actor mono">{node.actor}</span>
            </div>

            {node.op && <div className="sc-op mono">{node.op}</div>}
            {node.subtitle && <div className="sc-desc">{node.subtitle}</div>}

            {/* current action for the running node */}
            {currentAction && (
              <div className="sc-current">
                <span className="sc-current-dot" />
                <span className="sc-current-verb mono">{currentAction.verb}</span>
                <span className="sc-current-target mono">{currentAction.target}</span>
              </div>
            )}

            {/* iteration strip for agent_loop */}
            {node.iterations && (
              <div className="sc-iters">
                <span className="sc-iters-label">iterations</span>
                <span className="sc-iters-row">
                  {node.iterations.map((it) => (
                    <span
                      key={it.n}
                      className={"sc-iter is-" + it.status}
                      title={it.summary || ("iteration " + it.n)}
                    >
                      <span className="sc-iter-n mono">{it.n}</span>
                    </span>
                  ))}
                </span>
              </div>
            )}

            {/* highlights — key results / what happened */}
            {node.highlights && (status === "running" || status === "done") && (
              <ul className="sc-hl">
                {node.highlights.map((h, i) => (
                  <li key={i}><span className="sc-hl-bullet">›</span><span>{h}</span></li>
                ))}
              </ul>
            )}

            {stats && (
              <div className="sc-stats">
                {stats.map((s) => (
                  <span key={s.k} className="sc-stat">
                    <span className="sc-stat-v mono">{s.v}</span>
                    <span className="sc-stat-k">{s.k}</span>
                  </span>
                ))}
              </div>
            )}

            <div className="sc-foot">
              {status === "running" && (
                <>
                  <div className="sc-progress">
                    <div className="sc-progress-bar" style={{ width: (progress * 100).toFixed(1) + "%" }} />
                  </div>
                  <span className="sc-foot-t mono">
                    {elapsedInNode.toFixed(0)}s / {node.duration}s
                  </span>
                </>
              )}
              {status === "done" && (
                <>
                  <span className="sc-foot-t mono">
                    {startedClock} → {endedClock}
                  </span>
                  <span className="sc-foot-t mono sc-foot-dur">{node.duration}s</span>
                </>
              )}
              {status === "pending" && (
                <span className="sc-foot-t mono muted">
                  queued · ~ start +{Math.round(node.startAt)}s · est. {node.duration}s
                </span>
              )}
              {status === "skipped" && (
                <span className="sc-foot-t mono muted">branch not taken</span>
              )}
            </div>
            {node.loop && (
              <span className="sc-loop mono" title="this node may retry">
                <span className="sc-loop-arrow">↻</span> retry on fail
              </span>
            )}
          </>
        )}

        {isSentinel && (
          <div className="sc-sentinel mono">{node.subtitle}</div>
        )}
      </button>
    </div>
  );
}

// ─── Branch fork (the inline ⤧ visual) ──────────────────────────────────────

function BranchFork({ gateDone }) {
  return (
    <svg className="gp-fork" viewBox="0 0 280 28" preserveAspectRatio="none">
      <path
        d="M 140 0 L 140 8 Q 140 14 134 14 L 70 14 Q 64 14 64 20 L 64 28"
        fill="none"
        stroke={gateDone ? "var(--accent)" : "var(--edge)"}
        strokeWidth="1.4"
      />
      <path
        d="M 140 8 Q 140 14 146 14 L 216 14 Q 222 14 222 20 L 222 28"
        fill="none"
        stroke="var(--mute-2)"
        strokeWidth="1.2"
        strokeDasharray="3 3"
      />
    </svg>
  );
}

// ─── Run mini-map (compact horizontal strip in header) ──────────────────────

function RunMiniMap({ clock, selectedId, onSelect }) {
  const nodes = WORKGRAPH.nodes.filter(n => n.type !== "start" && n.type !== "end");
  return (
    <div className="gp-mini">
      {nodes.map(n => {
        const s = statusFor(n, clock);
        return (
          <button
            key={n.id}
            className={"gp-mini-seg is-" + s + (n.id === selectedId ? " is-selected" : "")}
            style={{ flex: n.duration || 1 }}
            title={n.label}
            onClick={() => onSelect(n.id)}
          >
            <span className="gp-mini-dot" />
          </button>
        );
      })}
    </div>
  );
}

function Legend() {
  return (
    <div className="legend">
      <span className="lg"><span className="lg-dot lg-done" /> done</span>
      <span className="lg"><span className="lg-dot lg-run" /> running</span>
      <span className="lg"><span className="lg-dot lg-pend" /> queued</span>
      <span className="lg"><span className="lg-dot lg-skip" /> skipped</span>
    </div>
  );
}

Object.assign(window, { GraphPanel, statusFor, nodeProgress });
