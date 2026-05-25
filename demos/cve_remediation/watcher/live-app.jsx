// live-app.jsx — `?run=<id>` mode: real DAG + real WS events, no clock playback.
//
// Differs from app.jsx (simulated) in three ways:
//   1. Topology comes from GET /watch/api/graph (loaded IR doc, all nodes + edges).
//   2. Past events come from GET /watch/api/run/{id}/events (per-run JSONL tap).
//   3. Live updates come from WS /v1/runs/{id}/stream and drive per-node status
//      directly from from_node/to_node — no bucket-and-clock indirection.
//
// Per-node viewers are driven by REAL captured data only:
//   • Topology + rules: GET /watch/api/graph
//   • Past events: GET /watch/api/run/{id}/events (per-run JSONL tap)
//   • Live events: WS /v1/runs/{id}/stream
//   • Per-step state: GET /watch/api/run/{id}/checkpoints (SQLiteCheckpointer)
// The old hand-authored NODE_CONTENT viewer is intentionally not consulted
// — it was the source of the "60s of fake thoughts on a 15s real run" bug.

const { useState: useStateL, useEffect: useEffectL, useRef: useRefL, useMemo: useMemoL } = React;

// ─── State delta + timing derivation ──────────────────────────────────────
//
// Each checkpoint row is `(step, last_node, state, ts)`. last_node is the
// node that just finished at this step boundary. Diffing state[step] against
// state[step-1] yields the exact set of state fields THIS node wrote.
function buildStateDeltas(checkpoints) {
  const byNode = new Map(); // node_id → {step, fields:{k:v}, ts}
  let prev = {};
  for (const ckpt of checkpoints) {
    const cur = ckpt.state || {};
    const fields = {};
    for (const k of Object.keys(cur)) {
      const a = JSON.stringify(prev[k]);
      const b = JSON.stringify(cur[k]);
      if (a !== b) fields[k] = cur[k];
    }
    // Also surface fields PRESENT now but absent before, even if newval is
    // falsy — they were still written by this node.
    if (ckpt.last_node) {
      byNode.set(ckpt.last_node, {
        step: ckpt.step,
        ts: ckpt.ts,
        fields,
        next_action: ckpt.next_action || null,
      });
    }
    prev = cur;
  }
  return byNode;
}

// Compute per-node timing: { node_id → {ts_in, ts_out, elapsed_ms} } from
// the transition stream. ts_in = ts of the transition that targeted this
// node; ts_out = ts of the next transition that left it.
function buildTimings(events) {
  const map = new Map();
  for (const ev of events) {
    if (ev.type !== "transition") continue;
    if (ev.to_node && !map.has(ev.to_node)) {
      map.set(ev.to_node, { ts_in: ev.ts, ts_out: null, elapsed_ms: null });
    }
    if (ev.from_node && map.has(ev.from_node)) {
      const t = map.get(ev.from_node);
      if (!t.ts_out) {
        t.ts_out = ev.ts;
        try {
          t.elapsed_ms = new Date(t.ts_out).getTime() - new Date(t.ts_in).getTime();
        } catch { /* noop */ }
      }
    }
  }
  return map;
}

function fmtElapsed(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(2)}s`;
  const m = Math.floor(ms / 60_000);
  const s = ((ms % 60_000) / 1000).toFixed(1);
  return `${m}m${s}s`;
}

// ─── Topology helpers ──────────────────────────────────────────────────────

function topoSort(nodes /*, edges */) {
  // Use IR declaration order. The cve-rem graph is intentionally written in
  // pipeline-reading order in harbor.yaml (intake → extract → correlate →
  // ssvc → planning → sandbox → CR → retro). A pure topo sort flips that
  // around for nodes with cycle-back retries (e.g. correlate_assets receives
  // a retry edge from remediation_discovery), which destroys the intended
  // reading order. The watcher's left panel is a reading aid, not a strict
  // execution-order replay, so the YAML order is the right default.
  return nodes.map((n) => n.id);
}

function applyEventToStatus(prev, ev) {
  // Pure reducer: take a node-status map and one Event, return the new map.
  const next = new Map(prev);
  if (ev.type === "transition") {
    if (ev.from_node && ev.from_node !== "__start__") next.set(ev.from_node, "done");
    if (ev.to_node) next.set(ev.to_node, "running");
  } else if (ev.type === "result") {
    // Snap every still-pending/running to done (terminal).
    for (const [k, v] of next) {
      if (v !== "skipped" && v !== "failed") next.set(k, "done");
    }
  } else if (ev.type === "error" && ev.scope === "node") {
    // No node id on the event itself; fall back to "current running" mark.
    for (const [k, v] of next) {
      if (v === "running") next.set(k, "failed");
    }
  }
  return next;
}

// ─── Live header ───────────────────────────────────────────────────────────

function LiveTopBar({ runId, status, currentLabel, nodeCount, eventsCount, onShare, elapsedMs, completedCount, wsState, wsFrames }) {
  const wsColor = wsState === "open" ? "var(--ok)" : wsState === "closed" ? "var(--fg-3)" : "var(--warn)";
  const wsLabel = wsState === "open" ? "live" : wsState === "reconnecting" ? "reconnect" : wsState === "closed" ? "off" : wsState;
  return (
    <header className="topbar">
      <div className="topbar-l">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◐</span>
          <span className="brand-name">WorkGraph</span>
          <span className="brand-sub">run watcher · live</span>
        </div>
        <span className="bread mono">
          <span className="muted">org/</span>kraken
          <span className="muted">/</span>cve-rem
          <span className="muted">/runs/</span>{runId}
        </span>
      </div>

      <div className="topbar-c">
        <div className="current-node">
          <span className="current-label">{status === "done" ? "finished" : "now running"}</span>
          <span className="current-name">{currentLabel || "—"}</span>
          <span className="current-time mono">
            {fmtElapsed(elapsedMs)} · {completedCount}/{nodeCount} nodes · {eventsCount} events
          </span>
        </div>
      </div>

      <div className="topbar-r">
        <span
          className="mono"
          title={`WebSocket ${wsState} · ${wsFrames} frames received`}
          style={{
            fontSize: 11,
            color: wsColor,
            border: `1px solid ${wsColor}`,
            background: "transparent",
            borderRadius: 4,
            padding: "2px 6px",
            marginRight: 8,
          }}
        >
          ws:{wsLabel} · {wsFrames}f
        </span>
        <span className={"nv-pill is-" + (status || "pending")} style={{ marginRight: 8 }}>
          {status === "running" && <span className="nv-pill-pulse" />}
          {status || "—"}
        </span>
        <a className="ghost-btn" href="/watch/">← runs</a>
        <button className="ghost-btn" onClick={onShare}>copy link</button>
      </div>
    </header>
  );
}

// ─── Edge overlay ──────────────────────────────────────────────────────────
// SVG layer drawn over the node list. After the list lays out, we measure
// each row's bounding box and draw a curve from source to target. Rows with
// fan-out (multiple outgoing edges) get one curve per target; fan-ins
// converge on the target's left rail dot.

// Compact branch summary rendered between two rows when the source node
// fans out to multiple targets. Shows the targets + their rule labels in
// one small strip instead of drawing N crossing curves across the panel.
function BranchSummary({ source, edges, nodeStatus, byId, onJump }) {
  if (!edges || edges.length === 0) return null;
  return (
    <div
      style={{
        margin: "2px 0 8px 38px",
        padding: "4px 8px",
        borderLeft: "2px dashed var(--edge)",
        fontSize: 11,
        color: "var(--fg-3)",
      }}
    >
      <span className="mono" style={{ color: "var(--fg-3)" }}>branches from {source}:</span>{" "}
      {edges.map((e, i) => {
        const tgtStatus = nodeStatus.get(e.target) || "pending";
        const fired = (nodeStatus.get(source) === "done") && tgtStatus !== "pending";
        const targetExists = byId.has(e.target);
        return (
          <span
            key={`${e.via_rule}-${e.target}-${i}`}
            className="mono"
            style={{
              marginRight: 12,
              color: fired ? "var(--accent)" : "var(--fg-3)",
              opacity: fired ? 1 : 0.6,
            }}
          >
            <span style={{ color: "var(--fg-3)" }}>[{e.kind}]</span>{" "}
            {targetExists ? (
              <a
                onClick={(ev) => { ev.preventDefault(); onJump(e.target); }}
                href="#"
                style={{ color: "inherit", textDecoration: fired ? "underline" : "none" }}
              >
                {e.target}
              </a>
            ) : (
              <span>{e.target}</span>
            )}
            {e.via_rule && <span style={{ color: "var(--fg-3)" }}> · {e.via_rule}</span>}
          </span>
        );
      })}
    </div>
  );
}

// ─── Live graph panel (all real nodes) ─────────────────────────────────────

// Map cve-rem family → 2-char glyph (matches the design palette).
const FAMILY_GLYPH = {
  branch: "▸",
  gate: "◇",
  llm: "LM",
  broker: "BR",
  kg: "GR",
  hitl: "HL",
  sandbox: "SB",
  artifact: "AR",
  decision: "?",
  tool: "FN",
};

// Wall-clock formatter — same shape as the design (HH:MM:SS).
function fmtWall(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    const h = String(d.getHours()).padStart(2, "0");
    const m = String(d.getMinutes()).padStart(2, "0");
    const s = String(d.getSeconds()).padStart(2, "0");
    return `${h}:${m}:${s}`;
  } catch { return null; }
}

function fmtSec(ms) {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
}

// Extract a short "operation" label for a node from its kind / config / events.
// Real data only — falls back to the kind name.
function opLabelFor(node, events) {
  const tool = events.find((e) => e.type === "tool_call");
  if (tool) return `${tool.namespace}.${tool.tool_name}`;
  const cfg = node.config || {};
  if (cfg.model_name) return `model · ${cfg.model_name}`;
  if (cfg.tool) return `tool · ${cfg.tool}`;
  if (cfg.namespace) return cfg.namespace;
  const k = (node.kind || "").split(":").pop().split(".").pop();
  return k || "node";
}

// Actor: stable label for "who" is doing the work — derived from kind suffix.
function actorFor(node) {
  const k = (node.kind || "");
  if (k.includes("DSPy") || k.includes("Planner") || k.includes("Critic")) return "dspy · LM";
  if (k.includes("Broker")) return "broker · Nautilus";
  if (k.includes("Sandbox")) return "sandbox · runner";
  if (k.includes("Hitl") || k.includes("Interrupt")) return "human · analyst";
  if (k.includes("Artifact") || k.includes("Emit")) return "artifact · store";
  if (k.includes("Graph") || k.includes("KG") || k.includes("Vec") || k.includes("Retrieval")) return "kg · graph";
  if (k.includes("Halt") || k.includes("Gate") || k.includes("Trust")) return "policy · gate";
  return "node · NodeBase";
}

// Live "current action" line for a running step. Reads the most recent
// tool_call / token / transition and renders verb + target.
function currentActionFor(events, status) {
  if (status !== "running") return null;
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i];
    if (e.type === "tool_call") return { verb: "CALLING", target: `${e.namespace}.${e.tool_name}` };
    if (e.type === "token")     return { verb: "STREAMING", target: `token #${e.index}` };
    if (e.type === "transition" && e.to_node) return { verb: "ENTERED", target: e.to_node };
  }
  return { verb: "RUNNING", target: "(awaiting first event)" };
}

// Iteration strip — only meaningful for agent-loop family nodes. We derive
// "iteration count" honestly from token events (none captured → no strip
// rendered) so we don't fabricate progress.
function iterationsFor(node, events, status) {
  const family = familyFor(node);
  if (family !== "llm") return null;
  const toolCalls = events.filter((e) => e.type === "tool_call");
  if (toolCalls.length === 0) return null;
  return toolCalls.map((tc, i) => ({
    n: i + 1,
    status: events.find((e) => e.type === "tool_result" && e.call_id === tc.call_id)
      ? "done"
      : (i === toolCalls.length - 1 && status === "running" ? "running" : "pending"),
  }));
}

// Highlights — short bullet list of REAL state-delta keys (the fields this
// node actually wrote). Falls back to a single "(no state changes)" line.
function highlightsFor(delta, events) {
  const fields = delta?.fields ? Object.entries(delta.fields) : [];
  if (fields.length === 0) {
    const toolResults = events.filter((e) => e.type === "tool_result");
    if (toolResults.length > 0) {
      return toolResults.slice(0, 3).map((r) =>
        r.ok ? `tool ${r.call_id.slice(0, 8)} → ok` : `tool ${r.call_id.slice(0, 8)} → ${r.error || "err"}`,
      );
    }
    return null;
  }
  // Show top 3 most-informative fields. Prefer non-empty values; keep ordering
  // stable so the live UI doesn't jitter.
  const ranked = fields.sort(([, a], [, b]) => {
    const av = (a == null || a === "" || (Array.isArray(a) && a.length === 0)) ? 0 : 1;
    const bv = (b == null || b === "" || (Array.isArray(b) && b.length === 0)) ? 0 : 1;
    return bv - av;
  });
  return ranked.slice(0, 3).map(([k, v]) => {
    let pretty;
    if (v == null) pretty = "null";
    else if (typeof v === "boolean") pretty = v ? "true" : "false";
    else if (typeof v === "number") pretty = String(v);
    else if (Array.isArray(v)) pretty = `[${v.length}]`;
    else if (typeof v === "object") pretty = `{${Object.keys(v).length}}`;
    else pretty = String(v).slice(0, 60);
    return `${k} → ${pretty}`;
  });
}

function statsFor(events, delta) {
  const stats = [];
  const tools = events.filter((e) => e.type === "tool_call").length;
  const tokens = events.filter((e) => e.type === "token").length;
  const errs = events.filter((e) => e.type === "error").length;
  const fields = delta?.fields ? Object.keys(delta.fields).length : 0;
  if (fields > 0) stats.push({ k: "fields", v: fields });
  if (tools > 0)  stats.push({ k: "tools", v: tools });
  if (tokens > 0) stats.push({ k: "tokens", v: tokens });
  if (errs > 0)   stats.push({ k: "errors", v: errs });
  return stats.length ? stats : null;
}

function LiveGraphPanel({ topo, nodeStatus, nodeEvents, selectedId, onSelect, currentRunningId, timingByNode, stateDeltaByNode, runStatus, runElapsedMs }) {
  const scrollRef = useRefL(null);
  const rowRefs = useRefL(new Map());

  // Pre-bucket edges by source for inline branch-summary rendering.
  const edgesBySource = useMemoL(() => {
    const m = new Map();
    for (const e of topo.edges) {
      if (!m.has(e.source)) m.set(e.source, []);
      m.get(e.source).push(e);
    }
    return m;
  }, [topo.edges]);

  useEffectL(() => {
    if (!currentRunningId || !scrollRef.current) return;
    const el = rowRefs.current.get(currentRunningId);
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
  }, [currentRunningId]);

  const total = topo.order.length;
  const doneCount = Array.from(nodeStatus.values()).filter((s) => s === "done").length;
  const runningCount = Array.from(nodeStatus.values()).filter((s) => s === "running").length;

  // Start time = ts_in of the first node we have timing for.
  const firstTs = (() => {
    for (const id of topo.order) {
      const t = timingByNode.get(id);
      if (t?.ts_in) return t.ts_in;
    }
    return null;
  })();
  const startedWall = fmtWall(firstTs);

  return (
    <aside className="graph-panel" data-side="left">
      <header className="gp-head">
        <div className="gp-head-row">
          <div className="gp-title">
            <span className="gp-dot" />
            <span className="gp-name">{topo.graph_id}</span>
          </div>
          <span className="gp-live">
            <span className="gp-pulse" />{runStatus === "done" ? "complete" : runStatus === "failed" ? "failed" : "live"}
          </span>
        </div>
        <div className="gp-meta">
          <span className="gp-runid mono">{topo.graph_hash.slice(0, 12)}</span>
          <span className="gp-sep">·</span>
          {startedWall && <><span>started {startedWall}</span><span className="gp-sep">·</span></>}
          <span className="mono">{fmtElapsed(runElapsedMs)} elapsed</span>
          <span className="gp-sep">·</span>
          <span>{doneCount} done</span>
          <span className="gp-sep">·</span>
          <span>{runningCount} running</span>
          <span className="gp-sep">·</span>
          <span>{total - doneCount - runningCount} queued</span>
        </div>
        {/* RunMiniMap removed — replaced by BottomBus (Phase 4) */}
      </header>

      <div className="gp-scroll" ref={scrollRef} style={{ position: "relative" }}>
        <div className="gp-rows">
          {topo.order.map((id, idx) => {
            const node = topo.byId.get(id);
            const status = nodeStatus.get(id) || "pending";
            const evs = nodeEvents.get(id) || [];
            const outEdges = (edgesBySource.get(id) || []).filter(
              (e) => e.kind !== "halt" && e.kind !== "interrupt",
            );
            const nextId = topo.order[idx + 1];
            const isLast = idx === topo.order.length - 1;
            const showBranchSummary =
              outEdges.length > 1
              || (outEdges.length === 1 && outEdges[0].target !== nextId);
            return (
              <React.Fragment key={id}>
                <div
                  data-node-id={id}
                  ref={(el) => {
                    if (el) rowRefs.current.set(id, el);
                    else rowRefs.current.delete(id);
                  }}
                >
                  <LiveStepCard
                    node={node}
                    stepNum={idx + 1}
                    status={status}
                    selected={id === selectedId}
                    onSelect={onSelect}
                    events={evs}
                    timing={timingByNode.get(id) || null}
                    delta={stateDeltaByNode.get(id) || null}
                    isLast={isLast}
                  />
                </div>
                {showBranchSummary && (
                  <BranchSummary
                    source={id}
                    edges={outEdges}
                    nodeStatus={nodeStatus}
                    byId={topo.byId}
                    onJump={onSelect}
                  />
                )}
              </React.Fragment>
            );
          })}
          {(runStatus === "done" || runStatus === "failed" || runStatus === "cancelled") && (
            <button className={"sc-card is-summary" + (selectedId === "__summary__" ? " is-selected" : "")} data-node-id="__summary__" onClick={() => onSelect("__summary__")} type="button" style={{ marginTop: 8 }}>
              <span className="sc-name">Run summary</span>
              <span className="sc-type">Final summary</span>
            </button>
          )}
        </div>
      </div>
    </aside>
  );
}

function RunMiniMap({ order, byId, nodeStatus, selectedId, onSelect }) {
  return (
    <div className="gp-mini">
      {order.map((id) => {
        const status = nodeStatus.get(id) || "pending";
        const node = byId.get(id);
        return (
          <button
            key={id}
            className={"gp-mini-seg is-" + status + (id === selectedId ? " is-selected" : "")}
            style={{ flex: 1 }}
            title={`${id} · ${status}`}
            onClick={() => onSelect(id)}
            type="button"
          >
            <span className="gp-mini-dot" />
          </button>
        );
      })}
    </div>
  );
}

function LiveStepCard({ node, stepNum, status, selected, onSelect, events, timing, delta, isLast }) {
  const [now, setNow] = useStateL(Date.now());
  useEffectL(() => {
    if (status !== "running" || !timing?.ts_in) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [status, timing?.ts_in]);

  const family = familyFor(node);
  const glyph = FAMILY_GLYPH[family] || "·";
  const typeLabel = FAMILY_LABEL[family] || family;
  const actor = actorFor(node);
  const op = opLabelFor(node, events);
  const profile = (typeof NODE_PROFILE !== "undefined" && NODE_PROFILE[node.id]) || null;
  const subtitle = profile ? profile.role.split(/[.;]/)[0] : null;
  const current = currentActionFor(events, status);
  const iterations = iterationsFor(node, events, status);
  const highlights = highlightsFor(delta, events);
  const stats = statsFor(events, delta);

  // Timing.
  const startedWall = fmtWall(timing?.ts_in);
  const endedWall = fmtWall(timing?.ts_out);
  let elapsedMs = timing?.elapsed_ms;
  if (elapsedMs == null && timing?.ts_in && status === "running") {
    try { elapsedMs = now - new Date(timing.ts_in).getTime(); } catch {}
  }
  // Progress bar for running steps — without an estimate we just animate
  // "indeterminate" by capping at 90% so the user sees motion.
  const progressPct = status === "running"
    ? Math.min(90, Math.floor((elapsedMs || 0) / 1000) * 4)
    : 0;

  return (
    <div className={"sc-wrap " + (isLast ? "is-last" : "")}>
      <div className="sc-rail">
        <div className={"sc-rail-dot is-" + status}>
          {status === "running" && <span className="sc-rail-pulse" />}
        </div>
        {!isLast && <div className={"sc-rail-line is-" + status} />}
      </div>

      <button
        className={"sc-card is-" + status + (selected ? " is-selected" : "")}
        onClick={() => onSelect(node.id)}
        type="button"
      >
        <div className="sc-top">
          <div className="sc-top-l">
            <span className="sc-step mono">{String(stepNum).padStart(2, "0")}</span>
            <span className={"sc-glyph is-" + status}>{glyph}</span>
            <span className="sc-name">{profile?.title || node.id}</span>
          </div>
          <span className={"sc-status is-" + status}>
            {status === "running" && <span className="gp-spin" />}
            {status === "done"    && <span className="sc-ok">✓</span>}
            {status === "failed"  && <span className="sc-skip">✕</span>}
            {status === "pending" && <span className="sc-pend">◌</span>}
            <span className="sc-status-t">{status}</span>
          </span>
        </div>

        <div className="sc-type-row">
          <span className="sc-type mono">{typeLabel}</span>
          <span className="sc-sep">·</span>
          <span className="sc-actor mono">{actor}</span>
        </div>

        <div className="sc-op mono">{op}</div>
        {subtitle && <div className="sc-desc">{subtitle}</div>}

        {current && (
          <div className="sc-current">
            <span className="sc-current-dot" />
            <span className="sc-current-verb mono">{current.verb}</span>
            <span className="sc-current-target mono">{current.target}</span>
          </div>
        )}

        {iterations && (
          <div className="sc-iters">
            <span className="sc-iters-label">iterations</span>
            <span className="sc-iters-row">
              {iterations.map((it) => (
                <span key={it.n} className={"sc-iter is-" + it.status}>
                  <span className="sc-iter-n mono">{it.n}</span>
                </span>
              ))}
            </span>
          </div>
        )}

        {highlights && (status === "running" || status === "done") && (
          <ul className="sc-hl">
            {highlights.map((h, i) => (
              <li key={i}><span className="sc-hl-bullet">›</span><span className="mono" style={{ fontSize: 11 }}>{h}</span></li>
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
                <div className="sc-progress-bar" style={{ width: progressPct + "%" }} />
              </div>
              <span className="sc-foot-t mono">{fmtSec(elapsedMs)} elapsed</span>
            </>
          )}
          {status === "done" && startedWall && (
            <>
              <span className="sc-foot-t mono">
                {startedWall}{endedWall ? ` → ${endedWall}` : ""}
              </span>
              <span className="sc-foot-t mono sc-foot-dur">{fmtSec(elapsedMs)}</span>
            </>
          )}
          {status === "pending" && (
            <span className="sc-foot-t mono muted">queued</span>
          )}
          {status === "skipped" && (
            <span className="sc-foot-t mono muted">branch not taken</span>
          )}
          {status === "failed" && (
            <span className="sc-foot-t mono" style={{ color: "var(--err)" }}>failed{elapsedMs != null ? ` · ${fmtSec(elapsedMs)}` : ""}</span>
          )}
        </div>
      </button>
    </div>
  );
}

// ─── Live node detail ──────────────────────────────────────────────────────

// Map kinds onto a visual "family". Decides which heading icon + colour
// the panel uses and whether the design's hero NODE_CONTENT viewer is
// applicable.
function familyFor(node) {
  // Prefer the hand-authored profile so the badge matches the documented
  // role of the node; fall back to a kind-heuristic for nodes that don't
  // yet have a profile entry.
  if (typeof NODE_PROFILE !== "undefined" && NODE_PROFILE[node.id]) {
    return NODE_PROFILE[node.id].family;
  }
  const k = (node.kind || "").toLowerCase();
  if (k.endsWith(":passthroughstub")) return "branch";
  if (k.endsWith(":haltnewgatenode")) return "gate";
  if (k.includes("dspy") || k.includes("planner") || k.includes("critic") || k.includes("extract") || k.includes("classify") || k.includes("critique")) return "llm";
  if (k.includes("broker")) return "broker";
  if (k.includes("graph") || k.includes("vec_search") || k.includes("kg_") || k.includes("retrieval") || k.includes("framework") || k.includes("template")) return "kg";
  if (k.includes("hitl") || k.includes("interrupt")) return "hitl";
  if (k.includes("sandbox")) return "sandbox";
  if (k.includes("artifact") || k.includes("emit")) return "artifact";
  if (k.includes("ssvc") || k.includes("evaluate") || k.includes("dispatch") || k.includes("quarantine") || k.includes("trust")) return "decision";
  return "tool";
}

const FAMILY_LABEL = {
  branch: "branch / passthrough",
  gate: "halt-new gate",
  llm: "language-model node",
  broker: "broker (Nautilus)",
  kg: "knowledge-graph traversal",
  hitl: "human-in-the-loop",
  sandbox: "sandbox runtime",
  artifact: "artifact emit",
  decision: "decision / rule eval",
  tool: "tool call",
};

function ProfilePanel({ node }) {
  const config = node.config || {};
  const entries = Object.entries(config);
  const profile = (typeof NODE_PROFILE !== "undefined" && NODE_PROFILE[node.id]) || null;
  const badge = profile && typeof FAMILY_BADGE !== "undefined"
    ? FAMILY_BADGE[profile.family]
    : null;
  return (
    <section className="panel">
      <header className="panel-h">
        <span className="panel-t">node profile</span>
        {badge && (
          <span
            className="mono"
            style={{
              padding: "1px 6px",
              borderRadius: 3,
              background: badge.color + "22",
              border: "1px solid " + badge.color,
              color: badge.color,
              fontSize: 10,
              letterSpacing: 0.5,
            }}
          >
            {badge.label}
          </span>
        )}
      </header>
      <div className="panel-b">
        {!profile ? (
          <div className="muted">no profile authored for this node id</div>
        ) : (
          <>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{profile.title}</div>
            <div style={{ color: "var(--fg-2)", fontSize: 13, lineHeight: 1.5, marginBottom: 10 }}>
              {profile.role}
            </div>

            {profile.inputs && profile.inputs.length > 0 && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <div style={{ color: "var(--fg-3)", marginBottom: 2 }}>inputs</div>
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {profile.inputs.map((x, i) => <li key={i} className="mono">{x}</li>)}
                </ul>
              </div>
            )}

            {profile.outputs && profile.outputs.length > 0 && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <div style={{ color: "var(--fg-3)", marginBottom: 2 }}>outputs</div>
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {(Array.isArray(profile.outputs) ? profile.outputs : [profile.outputs]).map((x, i) =>
                    <li key={i} className="mono">{x}</li>
                  )}
                </ul>
              </div>
            )}

            {profile.capabilities && profile.capabilities.length > 0 && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <div style={{ color: "var(--fg-3)", marginBottom: 2 }}>capabilities required</div>
                <div>
                  {profile.capabilities.map((c, i) => (
                    <span
                      key={i}
                      className="mono"
                      style={{
                        display: "inline-block",
                        padding: "1px 6px",
                        marginRight: 4,
                        marginBottom: 2,
                        border: "1px solid var(--edge)",
                        borderRadius: 3,
                        fontSize: 11,
                      }}
                    >
                      {c}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {profile.side_effects && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <div style={{ color: "var(--fg-3)" }}>side effects</div>
                <div className="mono" style={{ fontSize: 12 }}>{profile.side_effects}</div>
              </div>
            )}

            {profile.evidence && profile.evidence.length > 0 && (
              <div style={{ fontSize: 12, marginBottom: 6 }}>
                <div style={{ color: "var(--fg-3)", marginBottom: 2 }}>evidence</div>
                <ul style={{ margin: 0, paddingLeft: 16 }}>
                  {profile.evidence.map((x, i) => <li key={i} className="mono" style={{ fontSize: 11 }}>{x}</li>)}
                </ul>
              </div>
            )}

            {profile.cite && (
              <div style={{ fontSize: 11, color: "var(--fg-3)", marginTop: 6 }}>
                <span style={{ color: "var(--fg-3)" }}>cite:</span> {profile.cite}
              </div>
            )}
          </>
        )}

        <div style={{ borderTop: "1px solid var(--edge)", marginTop: 10, paddingTop: 8, fontSize: 11 }}>
          <div style={{ color: "var(--fg-3)", marginBottom: 2 }}>IR binding</div>
          <div className="mono" style={{ fontSize: 11, wordBreak: "break-all" }}>
            {node.kind}
          </div>
          {node.spec && (
            <div className="mono" style={{ fontSize: 11, color: "var(--fg-3)" }}>
              spec: {node.spec}
            </div>
          )}
          {entries.length > 0 && (
            <details style={{ marginTop: 6 }}>
              <summary style={{ cursor: "pointer", color: "var(--fg-3)" }}>config ({entries.length} keys)</summary>
              <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "2px 12px", margin: "4px 0 0 0" }}>
                {entries.map(([k, v]) => (
                  <React.Fragment key={k}>
                    <dt className="mono" style={{ color: "var(--fg-3)" }}>{k}</dt>
                    <dd className="mono" style={{ margin: 0 }}>
                      {typeof v === "object" ? JSON.stringify(v) : String(v)}
                    </dd>
                  </React.Fragment>
                ))}
              </dl>
            </details>
          )}
        </div>
      </div>
    </section>
  );
}

function RoutingPanel({ node, edges, byId, firedRuleIds }) {
  const outRules = node.rules || [];
  const incoming = edges.filter((e) => e.target === node.id);
  const outgoing = edges.filter((e) => e.source === node.id);
  return (
    <section className="panel">
      <header className="panel-h">
        <span className="panel-t">branch routing</span>
        <span className="panel-r mono">{outgoing.length} out · {incoming.length} in</span>
      </header>
      <div className="panel-b is-mono is-scroll" style={{ maxHeight: 240, fontSize: 12 }}>
        {outRules.length === 0 && outgoing.length === 0 ? (
          <span className="muted">terminal node — no outgoing rules</span>
        ) : outRules.map((r) => (
          <div
            key={r.id}
            style={{
              padding: "6px 0",
              borderBottom: "1px solid var(--edge)",
              opacity: firedRuleIds.has(r.id) ? 1 : 0.55,
            }}
          >
            <div>
              <span className={"nv-pill is-" + (firedRuleIds.has(r.id) ? "done" : "pending")} style={{ marginRight: 6, fontSize: 10 }}>
                {firedRuleIds.has(r.id) ? "fired" : "idle"}
              </span>
              <b>{r.id}</b>
            </div>
            <div style={{ color: "var(--fg-3)", fontSize: 11, margin: "2px 0" }}>
              when: {r.when || <span className="muted">always</span>}
            </div>
            {r.actions.map((a, i) => {
              const targets = a.kind === "parallel"
                ? (a.targets || []).join(", ")
                : (a.target || a.reason || a.prompt || "—");
              return (
                <div key={i} style={{ paddingLeft: 12 }}>
                  <span style={{ color: "var(--accent)" }}>→</span> <b>{a.kind}</b> {targets}
                </div>
              );
            })}
          </div>
        ))}
        {incoming.length > 0 && (
          <div style={{ paddingTop: 8, marginTop: 8, borderTop: "1px solid var(--edge)" }}>
            <div style={{ color: "var(--fg-3)" }}>incoming edges ({incoming.length}):</div>
            {incoming.map((e, i) => (
              <div key={i} style={{ paddingLeft: 12 }}>
                <span style={{ color: "var(--accent)" }}>←</span> {e.source} <span className="muted">via {e.via_rule} ({e.kind})</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function ActivityPanel({ events }) {
  const toolCalls = events.filter((e) => e.type === "tool_call");
  const toolResults = events.filter((e) => e.type === "tool_result");
  const tokens = events.filter((e) => e.type === "token");
  const errors = events.filter((e) => e.type === "error");
  const transitions = events.filter((e) => e.type === "transition");
  const tokenText = tokens.map((t) => t.token).join("");
  return (
    <section className="panel">
      <header className="panel-h">
        <span className="panel-t">live activity</span>
        <span className="panel-r mono">
          {transitions.length} trans · {toolCalls.length} tool · {tokens.length} tok · {errors.length} err
        </span>
      </header>
      <div className="panel-b is-scroll" style={{ maxHeight: 420 }}>
        {events.length === 0 && (
          <div className="muted" style={{ padding: 8 }}>
            no events captured for this node yet
          </div>
        )}
        {transitions.length > 0 && (
          <div style={{ padding: 8, borderBottom: "1px solid var(--edge)" }}>
            <div style={{ color: "var(--fg-3)", marginBottom: 4 }}>transitions</div>
            {transitions.map((t, i) => (
              <div key={i} className="mono" style={{ fontSize: 12, padding: "2px 0" }}>
                [{fmtTs(t.ts)}] <b>{t.from_node}</b> → <b>{t.to_node}</b>{" "}
                <span className="muted">rule={t.rule_id}</span>
                {t.reason && <span className="muted"> · {t.reason}</span>}
              </div>
            ))}
          </div>
        )}
        {toolCalls.length > 0 && (
          <div style={{ padding: 8, borderBottom: "1px solid var(--edge)" }}>
            <div style={{ color: "var(--fg-3)", marginBottom: 4 }}>tool calls</div>
            {toolCalls.map((c) => {
              const result = toolResults.find((r) => r.call_id === c.call_id);
              return (
                <div key={c.call_id} style={{ marginBottom: 8 }}>
                  <div className="mono" style={{ fontSize: 12 }}>
                    <span className={"nv-pill is-" + (result ? (result.ok ? "done" : "failed") : "running")} style={{ marginRight: 6, fontSize: 10 }}>
                      {result ? (result.ok ? "ok" : "err") : "…"}
                    </span>
                    <b>{c.namespace}.{c.tool_name}</b>
                    <span className="muted"> · {c.call_id.slice(0, 12)}</span>
                  </div>
                  <pre className="code code-json" style={{ fontSize: 11, margin: "4px 0", maxHeight: 160, overflow: "auto" }}>
{JSON.stringify(c.args, null, 2)}
                  </pre>
                  {result && (
                    <pre className="code code-json" style={{ fontSize: 11, margin: 0, background: "var(--accent-dim)", padding: 6, maxHeight: 200, overflow: "auto" }}>
{JSON.stringify(result.result ?? result.error, null, 2)}
                    </pre>
                  )}
                </div>
              );
            })}
          </div>
        )}
        {tokenText.length > 0 && (
          <div style={{ padding: 8, borderBottom: "1px solid var(--edge)" }}>
            <div style={{ color: "var(--fg-3)", marginBottom: 4 }}>LM token stream ({tokens.length} tok)</div>
            <pre className="code" style={{ fontSize: 11, whiteSpace: "pre-wrap", maxHeight: 200, overflow: "auto" }}>{tokenText}</pre>
          </div>
        )}
        {errors.length > 0 && (
          <div style={{ padding: 8 }}>
            <div style={{ color: "var(--err)", marginBottom: 4 }}>errors</div>
            {errors.map((e, i) => (
              <div key={i} className="mono" style={{ fontSize: 12, padding: "2px 0", color: "var(--err)" }}>
                [{fmtTs(e.ts)}] <b>{e.scope}</b>: {e.message}{e.recoverable ? " (recoverable)" : ""}
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

// ─── Real-data panels (no hand-authored content) ─────────────────────────

function TimingPanel({ timing, status }) {
  // Pulls ts_in/ts_out from the real transition stream. If the node hasn't
  // started, we say so honestly; if it's running, we count up from ts_in.
  const [now, setNow] = useStateL(Date.now());
  useEffectL(() => {
    if (status !== "running" || !timing?.ts_in) return;
    const t = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(t);
  }, [status, timing?.ts_in]);

  let elapsed = null;
  if (timing?.elapsed_ms != null) {
    elapsed = timing.elapsed_ms;
  } else if (timing?.ts_in && status === "running") {
    try { elapsed = now - new Date(timing.ts_in).getTime(); } catch {}
  }

  return (
    <section className="panel">
      <header className="panel-h">
        <span className="panel-t">timing</span>
      </header>
      <div className="panel-b" style={{ fontSize: 12 }}>
        {!timing?.ts_in ? (
          <div className="muted">node has not yet entered (no transition captured)</div>
        ) : (
          <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "2px 12px", margin: 0 }}>
            <dt className="muted">entered</dt>
            <dd className="mono" style={{ margin: 0 }}>{fmtTs(timing.ts_in)}</dd>
            <dt className="muted">exited</dt>
            <dd className="mono" style={{ margin: 0 }}>{timing.ts_out ? fmtTs(timing.ts_out) : <i className="muted">running…</i>}</dd>
            <dt className="muted">elapsed</dt>
            <dd className="mono" style={{ margin: 0, color: "var(--accent)" }}>{fmtElapsed(elapsed)}</dd>
          </dl>
        )}
      </div>
    </section>
  );
}

function OutcomePanel({ node, delta, status }) {
  // The set of state fields written at this node's checkpoint step.
  // Empty delta + done status = the node returned `{}` — show that honestly
  // rather than fabricating a result.
  const entries = delta?.fields ? Object.entries(delta.fields) : [];
  return (
    <section className="panel">
      <header className="panel-h">
        <span className="panel-t">state delta</span>
        {delta?.step != null && <span className="panel-r mono">step {delta.step}</span>}
      </header>
      <div className="panel-b" style={{ fontSize: 12 }}>
        {!delta ? (
          status === "pending" ? (
            <div className="muted">no checkpoint yet (node hasn't run)</div>
          ) : (
            <div className="muted">no checkpoint recovered for this node</div>
          )
        ) : entries.length === 0 ? (
          <div className="muted">
            node returned no state changes (idempotent / gate passed / short-circuit)
          </div>
        ) : (
          <dl style={{ display: "grid", gridTemplateColumns: "max-content 1fr", gap: "2px 12px", margin: 0 }}>
            {entries.map(([k, v]) => (
              <React.Fragment key={k}>
                <dt className="mono" style={{ color: "var(--fg-3)" }}>{k}</dt>
                <dd className="mono" style={{ margin: 0, wordBreak: "break-word" }}>{renderStateValue(v)}</dd>
              </React.Fragment>
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}

function renderStateValue(v) {
  if (v == null) return <i className="muted">null</i>;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return String(v);
  if (typeof v === "string") {
    if (v.length > 400) {
      return (
        <details>
          <summary className="mono">{v.slice(0, 120)}… <span className="muted">({v.length} chars)</span></summary>
          <pre className="code" style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>{v}</pre>
        </details>
      );
    }
    return v;
  }
  if (Array.isArray(v)) {
    if (v.length === 0) return <i className="muted">[] (empty)</i>;
    return (
      <details open={v.length <= 4}>
        <summary className="mono">{v.length} item{v.length === 1 ? "" : "s"}</summary>
        <pre className="code code-json" style={{ fontSize: 11, margin: "2px 0" }}>
{JSON.stringify(v, null, 2)}
        </pre>
      </details>
    );
  }
  return (
    <details>
      <summary className="mono">object · {Object.keys(v).length} keys</summary>
      <pre className="code code-json" style={{ fontSize: 11, margin: "2px 0" }}>
{JSON.stringify(v, null, 2)}
      </pre>
    </details>
  );
}

// ─── Per-node specialised views ────────────────────────────────────────────
// halt_new_gate: the first node of the cve-rem flow. Reads a Postgres
// fleet-ledger table for an active halt-new entry and short-circuits the
// pipeline if found. Has no LLM, no tool dispatch — just a single durable
// read + two state writes (halt_new_active, halt_reason).
function HaltNewGateView({ node, status, delta, timing }) {
  const haltActive = delta?.fields?.halt_new_active === true;
  const haltReason = delta?.fields?.halt_reason || "";
  const ranButNoDelta = !!delta && Object.keys(delta?.fields || {}).length === 0;
  const ttlMin = (node.config && node.config.ttl_minutes) || 30; // documented default

  return (
    <section className="panel">
      <header className="panel-h">
        <span className="panel-t">halt-new gate · verdict</span>
        <span className="panel-r mono">{fmtElapsed(timing?.elapsed_ms)}</span>
      </header>
      <div className="panel-b" style={{ padding: 12, fontSize: 13, lineHeight: 1.55 }}>
        <div style={{ marginBottom: 10 }}>
          <span className="muted">checks:&nbsp;</span>
          <span className="mono">
            SELECT * FROM <b>cve_rem_halt_new_ledger</b>
            <br />
            WHERE severity='halt' AND fired_at &gt; NOW() - {ttlMin}m
          </span>
        </div>

        {status === "pending" && (
          <div className="muted">gate has not yet executed — waiting on run start.</div>
        )}

        {status === "running" && (
          <div style={{ color: "var(--accent)" }}>
            querying fleet ledger… (run start)
          </div>
        )}

        {(status === "done" || status === "failed") && (
          <>
            {haltActive ? (
              <div style={{
                border: "1px solid var(--err)",
                background: "rgba(255,90,90,.06)",
                borderRadius: 4,
                padding: 10,
              }}>
                <div style={{ fontWeight: 600, color: "var(--err)", marginBottom: 4 }}>
                  HALT-NEW ACTIVE
                </div>
                <div className="mono" style={{ fontSize: 12, whiteSpace: "pre-wrap" }}>
                  {haltReason || "(no reason recorded)"}
                </div>
                <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                  downstream sandbox / progressive-execute nodes will short-circuit
                </div>
              </div>
            ) : ranButNoDelta ? (
              <div style={{
                border: "1px solid var(--edge)",
                borderRadius: 4,
                padding: 10,
              }}>
                <div style={{ fontWeight: 600, color: "var(--accent)", marginBottom: 4 }}>
                  GATE PASSED
                </div>
                <div className="muted" style={{ fontSize: 12 }}>
                  no active halt-new entry within the last {ttlMin}m
                  — or POSTGRES_DSN unset / table absent (silent-skip).
                  Node returned <span className="mono">{"{}"}</span> — pipeline continues.
                </div>
              </div>
            ) : (
              <div className="muted">unexpected outcome — see state-delta panel</div>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function LiveNodeView({ node, status, events, topo, allEvents, runStatus, delta, timing, runState, runTerminal }) {
  if (!node) {
    return (
      <div className="nv-pending">
        <div className="nv-pending-card">
          <div className="nv-pending-icon">◌</div>
          <div className="nv-pending-title">Select a node</div>
          <div className="nv-pending-sub">pick a step on the left to inspect it</div>
        </div>
      </div>
    );
  }

  const family = familyFor(node);
  const familyLabel = FAMILY_LABEL[family] || family;
  const kindShort = (node.kind || "").split(":").pop().split(".").pop();
  const profile = (typeof NODE_PROFILE !== "undefined" && NODE_PROFILE[node.id]) || null;

  // Set of rule_ids that fired for THIS node id during this run; the
  // routing panel uses it to dim idle branches and brighten taken ones.
  const firedRuleIds = useMemoL(() => {
    const s = new Set();
    for (const ev of allEvents) {
      if (ev.type === "transition" && ev.from_node === node.id && ev.rule_id) {
        s.add(ev.rule_id);
      }
    }
    return s;
  }, [allEvents, node.id]);

  // Dispatcher: priority-id → family → OutcomePanel fallback.
  const Panel = (typeof window.panelForNode === "function"
    ? window.panelForNode(node)
    : null) || OutcomePanel;

  const panelProps = {
    node,
    profile,
    status,
    delta,
    events,
    timing,
    runState: runState || {},
    runTerminal: !!runTerminal,
  };

  return (
    <>
      <header className="nv-head">
        <div className="nv-head-row">
          <div className="nv-eyebrow">
            <span className="nv-type">{familyLabel}</span>
            <span className="nv-sep">/</span>
            <span className="nv-id">node·{node.id}</span>
          </div>
          <span className={"nv-pill is-" + status}>
            {status === "running" && <span className="nv-pill-pulse" />}
            {status}
          </span>
        </div>
        <h1 className="nv-title">{profile ? profile.title : node.id}</h1>
        <div className="mono" style={{ color: "var(--fg-3)", fontSize: 12, marginTop: 4 }}>
          {node.id} · {kindShort} · {fmtElapsed(timing?.elapsed_ms)}
        </div>
      </header>

      <div style={{ borderBottom: "1px solid var(--edge)", padding: 12 }}>
        <Panel {...panelProps} />
      </div>

      <div
        className="nv-grid"
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
          alignItems: "start",
          gap: 12,
          padding: 12,
          minWidth: 0,
          overflow: "hidden",
        }}
      >
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <ProfilePanel node={node} />
          <TimingPanel timing={timing} status={status} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          <OutcomePanel node={node} delta={delta} status={status} />
          <RoutingPanel node={node} edges={topo.edges} byId={topo.byId} firedRuleIds={firedRuleIds} />
        </div>
        <div style={{ minWidth: 0 }}>
          <ActivityPanel events={events} />
        </div>
      </div>
    </>
  );
}

function fmtTs(iso) {
  if (!iso) return "";
  try { return new Date(iso).toISOString().slice(11, 23); } catch { return String(iso); }
}

// ─── URL-driven node selection ────────────────────────────────────────────
// Owns the ?node= query param. replaceState only (NFR-12) — no back-button
// stack pollution. Validates against topoOrder + sentinel __summary__.

function useUrlNodeSelection(topoOrder) {
  const validSet = useMemoL(() => {
    const s = new Set(topoOrder);
    s.add("__summary__");
    return s;
  }, [topoOrder]);

  const readFromUrl = () => {
    const params = new URLSearchParams(window.location.search);
    const raw = params.get("node");
    if (raw && validSet.has(raw)) return raw;
    return null;
  };

  const [selectedId, setSelectedIdRaw] = useStateL(readFromUrl);

  const setSelectedId = (id) => {
    const safeId = (id && validSet.has(id)) ? id : null;
    setSelectedIdRaw(safeId);
    const params = new URLSearchParams(window.location.search);
    if (safeId) {
      params.set("node", safeId);
    } else {
      params.delete("node");
    }
    history.replaceState(null, "", "?" + params.toString());
  };

  useEffectL(() => {
    const onPop = () => {
      const params = new URLSearchParams(window.location.search);
      const raw = params.get("node");
      if (raw && validSet.has(raw)) {
        setSelectedIdRaw(raw);
      } else {
        setSelectedIdRaw(null);
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [validSet]);

  return [selectedId, setSelectedId];
}

// ─── LiveApp root ──────────────────────────────────────────────────────────

function LiveApp({ runId }) {
  const [topo, setTopo] = useStateL(null); // {graph_id, graph_hash, nodes, edges, byId, order}
  const [nodeStatus, setNodeStatus] = useStateL(new Map());
  const [nodeEvents, setNodeEvents] = useStateL(new Map());
  const [allEvents, setAllEvents] = useStateL([]);
  const [runStatus, setRunStatus] = useStateL("pending");
  const [selectedId, setSelectedId] = useUrlNodeSelection(topo ? topo.order : []);
  const [err, setErr] = useStateL("");
  const [checkpoints, setCheckpoints] = useStateL([]);

  // Refs mirror the latest status / last-running so handleEvent can attribute
  // non-transition events (tool_call, token, error) to the right node without
  // depending on stale closure values from a previous render.
  const lastRunningRef = useRefL(null);
  const lastToNodeRef = useRefL(null);
  const runStatusRef = useRefL("pending");
  // Dedupe across JSONL replay + WS live + JSONL fallback poll. Keyed by
  // (step, type, ts, from_node, to_node) — a tuple unique per emit.
  const seenKeysRef = useRefL(new Set());

  // Visible diagnostics. Surfaces "WS open + N frames" or "WS closed" so
  // the user can tell at a glance whether live updates are flowing.
  const [wsState, setWsState] = useStateL("connecting");
  const [wsFrames, setWsFrames] = useStateL(0);

  // For status book-keeping the "current running" node is whichever id is
  // most-recently marked "running" by a TransitionEvent.to_node.
  const currentRunningId = useMemoL(() => {
    for (const [id, st] of nodeStatus) {
      if (st === "running") return id;
    }
    return null;
  }, [nodeStatus]);

  const handleEvent = (ev) => {
    // Cheap dedupe. Same event arrives via /events JSONL replay (page load
    // or fallback poll) AND via WS live stream — collapse to one entry.
    const key = [
      ev.type,
      ev.step,
      ev.ts,
      ev.from_node || "",
      ev.to_node || "",
      ev.tool_name || "",
      ev.call_id || "",
      ev.index != null ? ev.index : "",
    ].join("|");
    if (seenKeysRef.current.has(key)) return;
    seenKeysRef.current.add(key);

    if (ev.type === "transition") {
      lastToNodeRef.current = ev.to_node;
      lastRunningRef.current = ev.to_node;
    }
    setAllEvents((prev) => prev.concat([ev]));
    setNodeEvents((prev) => {
      const next = new Map(prev);
      const push = (nid, e) => {
        if (!nid) return;
        const arr = next.get(nid) ? next.get(nid).slice() : [];
        arr.push(e);
        next.set(nid, arr);
      };
      if (ev.type === "transition") {
        push(ev.from_node, ev);
        push(ev.to_node, ev);
      } else {
        const target = lastRunningRef.current || lastToNodeRef.current;
        push(target, ev);
      }
      return next;
    });
    setNodeStatus((prev) => applyEventToStatus(prev, ev));
    if (ev.type === "result") {
      setRunStatus(ev.status || "done");
      runStatusRef.current = ev.status || "done";
    }
  };

  // Keep the ref in sync so the WS effect (which doesn't depend on runStatus)
  // can still gate reconnect attempts.
  useEffectL(() => { runStatusRef.current = runStatus; }, [runStatus]);

  // Fetch topology + replay past audit, then open the WS.
  useEffectL(() => {
    let cancelled = false;
    (async () => {
      try {
        const tres = await fetch("/watch/api/graph");
        if (!tres.ok) throw new Error("graph fetch " + tres.status);
        const tjson = await tres.json();
        const byId = new Map(tjson.nodes.map((n) => [n.id, n]));
        const order = topoSort(tjson.nodes, tjson.edges);
        if (cancelled) return;
        setTopo({
          graph_id: tjson.graph_id,
          graph_hash: tjson.graph_hash,
          nodes: tjson.nodes,
          edges: tjson.edges,
          byId,
          order,
        });
      } catch (e) {
        setErr("topology: " + e.message);
        return;
      }

      // Replay any past events for this run so a refresh on a finished
      // run still paints the full DAG. We feed each event through the
      // single handleEvent path so cursor + dedupe stay consistent with
      // the WS pump.
      try {
        const ares = await fetch(`/watch/api/run/${encodeURIComponent(runId)}/events`);
        if (ares.ok) {
          const aj = await ares.json();
          const past = Array.isArray(aj.events) ? aj.events : [];
          if (!cancelled) {
            for (const ev of past) handleEvent(ev);
          }
        }
      } catch { /* tap may not exist yet — live WS will catch up */ }

      // Fetch per-step checkpoints (real durable state from SQLiteCheckpointer).
      try {
        const cres = await fetch(`/watch/api/run/${encodeURIComponent(runId)}/checkpoints`);
        if (cres.ok) {
          const cj = await cres.json();
          if (!cancelled && Array.isArray(cj.checkpoints)) {
            setCheckpoints(cj.checkpoints);
          }
        }
      } catch { /* checkpointer may not have any rows yet */ }

      // Peek run status (so we know whether to keep retrying the WS).
      try {
        const rres = await fetch(`/v1/runs/${encodeURIComponent(runId)}`);
        if (rres.ok) {
          const rj = await rres.json();
          if (rj.status) setRunStatus(rj.status);
        }
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [runId]);

  // Re-poll checkpoints when transition events arrive (cheap: 70-step run
  // = ~70 rows, single sqlite query). Keeps the state-delta panel current
  // without hand-rolling a state-stream channel.
  const lastTransitionStep = allEvents.filter((e) => e.type === "transition").length;
  useEffectL(() => {
    if (!runId) return;
    let cancelled = false;
    (async () => {
      try {
        const cres = await fetch(`/watch/api/run/${encodeURIComponent(runId)}/checkpoints`);
        if (cres.ok) {
          const cj = await cres.json();
          if (!cancelled && Array.isArray(cj.checkpoints)) {
            setCheckpoints(cj.checkpoints);
          }
        }
      } catch {}
    })();
    return () => { cancelled = true; };
  }, [runId, lastTransitionStep]);

  // Derive per-node state delta + per-node timing.
  const stateDeltaByNode = useMemoL(() => buildStateDeltas(checkpoints), [checkpoints]);
  const timingByNode = useMemoL(() => buildTimings(allEvents), [allEvents]);

  // Latest full state snapshot (for panels that need cross-node fields).
  const runState = checkpoints.length > 0 ? (checkpoints[checkpoints.length - 1].state || {}) : {};
  // Terminal flag — true once the run can no longer produce new events.
  const runTerminal = runStatus === "done" || runStatus === "failed" || runStatus === "cancelled";

  // Auto-switch to __summary__ on terminal transition (fires once on false→true).
  const prevRunTerminal = useRefL(false);
  useEffectL(() => {
    if (runTerminal && !prevRunTerminal.current) setSelectedId("__summary__");
    prevRunTerminal.current = runTerminal;
  }, [runTerminal]);

  // Dev-time coverage assertion (localhost only).
  useEffectL(() => {
    if (window.location.hostname === "localhost") window.assertNodeCoverage(topo);
  }, []);

  // Total elapsed for the run = first transition ts → last transition ts (or now).
  const runElapsedMs = useMemoL(() => {
    const firstTs = allEvents.length ? allEvents[0].ts : null;
    if (!firstTs) return null;
    const resultEv = allEvents.find((e) => e.type === "result");
    if (resultEv && typeof resultEv.run_duration_ms === "number") {
      return resultEv.run_duration_ms;
    }
    if (runStatus === "done" || runStatus === "failed") {
      const last = allEvents[allEvents.length - 1];
      try { return new Date(last.ts).getTime() - new Date(firstTs).getTime(); } catch { return null; }
    }
    try { return Date.now() - new Date(firstTs).getTime(); } catch { return null; }
  }, [allEvents, runStatus]);

  const completedCount = useMemoL(() => {
    let n = 0;
    for (const v of nodeStatus.values()) if (v === "done") n += 1;
    return n;
  }, [nodeStatus]);

  // Live pump = WebSocket + JSONL fallback poll.
  //
  // Strategy:
  //   • Open the WS as soon as we have topo. No cursor query string —
  //     keep the handshake simple so any malformed-cursor close path is
  //     impossible. Events emitted before the server-side broadcaster
  //     subscriber attaches are bridged by the polling fallback below.
  //   • While the run is non-terminal, also poll `/watch/api/run/{id}/events`
  //     every 1s and feed every event through handleEvent. Dedupe keys in
  //     seenKeysRef ensure each event is rendered exactly once regardless
  //     of which channel delivered it first.
  //   • On any WS close, reconnect (250ms → 500 → 1s → 2s backoff)
  //     until run is terminal.
  useEffectL(() => {
    if (!topo) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/v1/runs/${encodeURIComponent(runId)}/stream`;

    let ws = null;
    let stopped = false;
    let retryTimer = null;
    let pollTimer = null;
    let attempts = 0;

    const isTerminal = () => {
      const s = runStatusRef.current;
      return s === "done" || s === "failed" || s === "cancelled";
    };

    const pollJsonl = async () => {
      try {
        const ares = await fetch(`/watch/api/run/${encodeURIComponent(runId)}/events`);
        if (!ares.ok) return;
        const aj = await ares.json();
        const past = Array.isArray(aj.events) ? aj.events : [];
        for (const ev of past) handleEvent(ev);
      } catch { /* swallow */ }
    };

    const schedulePoll = () => {
      if (stopped) return;
      pollTimer = setTimeout(async () => {
        await pollJsonl();
        if (!isTerminal()) schedulePoll();
        else setWsState("closed");
      }, 1000);
    };

    const connect = () => {
      if (stopped) return;
      attempts += 1;
      try { ws = new WebSocket(url); } catch (e) { return; }
      setWsState("connecting");
      ws.onopen = () => {
        attempts = 0;
        setWsState("open");
      };
      ws.onmessage = (ev) => {
        let frame;
        try { frame = JSON.parse(ev.data); } catch { return; }
        setWsFrames((n) => n + 1);
        handleEvent(frame);
      };
      ws.onerror = () => { /* surface via onclose */ };
      ws.onclose = (e) => {
        if (stopped) return;
        setWsState("reconnecting");
        if (isTerminal()) { setWsState("closed"); return; }
        const delay = Math.min(2000, 250 * Math.pow(1.6, Math.min(attempts, 5)));
        retryTimer = setTimeout(connect, delay);
      };
    };

    connect();
    schedulePoll();

    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (pollTimer) clearTimeout(pollTimer);
      if (ws) {
        try { ws.onmessage = null; ws.onclose = null; ws.close(); } catch {}
      }
    };
  }, [topo, runId]);

  if (!topo) {
    return (
      <div className="app">
        <header className="topbar">
          <div className="topbar-l">
            <div className="brand">
              <span className="brand-mark">◐</span>
              <span className="brand-name">WorkGraph</span>
              <span className="brand-sub">loading…</span>
            </div>
          </div>
        </header>
        <main className="main">
          <div className="nv-pending">
            <div className="nv-pending-card">
              <div className="nv-pending-icon">◌</div>
              <div className="nv-pending-title">loading topology</div>
              <div className="nv-pending-sub">GET /watch/api/graph</div>
              {err && <div className="nv-pending-sub" style={{ color: "var(--err)" }}>{err}</div>}
            </div>
          </div>
        </main>
      </div>
    );
  }

  // Default selection: explicit user click > currently-running node > last
  // node that received a transition (for terminal runs this is the natural
  // "final node" view) > first declared node.
  const lastTransitionTarget = (() => {
    for (let i = allEvents.length - 1; i >= 0; i--) {
      const ev = allEvents[i];
      if (ev.type === "transition" && ev.to_node) return ev.to_node;
    }
    return null;
  })();
  const visibleNode = (selectedId && topo.byId.get(selectedId))
    || (currentRunningId && topo.byId.get(currentRunningId))
    || (lastTransitionTarget && topo.byId.get(lastTransitionTarget))
    || topo.nodes[0];
  const visibleStatus = nodeStatus.get(visibleNode.id) || "pending";
  const visibleEvents = nodeEvents.get(visibleNode.id) || [];

  return (
    <div
      className="app"
      data-side="left"
      style={{
        "--accent": "#3ddc97",
        "--accent-dim": "rgba(61,220,151,.14)",
        "--accent-ring": "rgba(61,220,151,.4)",
      }}
    >
      <LiveTopBar
        runId={runId}
        status={runStatus}
        currentLabel={currentRunningId || (runStatus === "done" ? "complete" : "queued")}
        nodeCount={topo.nodes.length}
        eventsCount={allEvents.length}
        elapsedMs={runElapsedMs}
        completedCount={completedCount}
        wsState={wsState}
        wsFrames={wsFrames}
        onShare={() => { try { navigator.clipboard.writeText(window.location.href); } catch {} }}
      />
      <HeaderGantt topo={topo} nodeTimings={timingByNode} runStartTs={allEvents.length ? allEvents[0].ts : null} nodeStatus={nodeStatus} selectedId={selectedId} onSelect={setSelectedId} />
      <main className="main" data-side="left">
        <LiveGraphPanel
          topo={topo}
          nodeStatus={nodeStatus}
          nodeEvents={nodeEvents}
          selectedId={selectedId || visibleNode.id}
          onSelect={setSelectedId}
          currentRunningId={currentRunningId}
          timingByNode={timingByNode}
          stateDeltaByNode={stateDeltaByNode}
          runStatus={runStatus}
          runElapsedMs={runElapsedMs}
        />
        <section className="view" data-screen-label={selectedId === "__summary__" ? "summary" : "node " + visibleNode.id}>
          {selectedId === "__summary__"
            ? <FinalSummaryPanel runState={runState} events={allEvents} runTerminal={runTerminal} />
            : <LiveNodeView
                node={visibleNode}
                status={visibleStatus}
                events={visibleEvents}
                topo={topo}
                allEvents={allEvents}
                runStatus={runStatus}
                delta={stateDeltaByNode.get(visibleNode.id) || null}
                timing={timingByNode.get(visibleNode.id) || null}
                runState={runState}
                runTerminal={runTerminal}
              />
          }
        </section>
      </main>
      <BottomBus topo={topo} nodeStatus={nodeStatus} selectedId={selectedId} onSelect={setSelectedId} />
    </div>
  );
}

Object.assign(window, { LiveApp, OutcomePanel });
