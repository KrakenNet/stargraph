// bottom-bus.jsx — phase-grouped segmented timeline pinned to viewport bottom.
// Each pipeline phase gets a labeled group; nodes render as thin colored segments
// within their phase. A cursor tracks the currently-running node.

function BottomBus({ topo, nodeStatus, selectedId, onSelect, nodeTimings }) {
  const phaseMap = window.usePhaseMap();

  const nodes = topo.order;
  const total = nodes.length;
  if (total === 0) return null;

  if (!phaseMap) {
    return (
      <footer className="timeline">
        <div className="timeline-l"><span className="tl-label">timeline</span></div>
        <div className="timeline-track"><div className="streaming-bar"><span className="streaming-bar-fill" /><span className="streaming-bar-label mono">loading phases…</span></div></div>
        <div className="timeline-r mono">—</div>
      </footer>
    );
  }

  // Group nodes by phase, preserving order.
  const grouped = new Map();
  for (const phase of window.PHASE_ORDER) grouped.set(phase, []);
  grouped.set("_other", []);

  for (const id of nodes) {
    const phase = phaseMap.phaseFor.get(id) || "_other";
    if (!grouped.has(phase)) grouped.set(phase, []);
    grouped.get(phase).push(id);
  }

  // Find currently running node for cursor.
  let cursorPhase = null;
  let cursorIdxInPhase = null;
  for (const [phase, ids] of grouped) {
    for (let i = 0; i < ids.length; i++) {
      if (nodeStatus.get(ids[i]) === "running") {
        cursorPhase = phase;
        cursorIdxInPhase = i;
        break;
      }
    }
    if (cursorPhase) break;
  }

  const doneCount = Array.from(nodeStatus.values()).filter(s => s === "done").length;
  const isViewingSelected = !!selectedId;

  return (
    <footer className="timeline">
      <div className="timeline-l">
        <span className="tl-label">timeline</span>
        {isViewingSelected && (
          <button className="tl-jump" onClick={() => onSelect(null)}>⟲ live</button>
        )}
      </div>

      <div className="tl-phases">
        {window.PHASE_ORDER.map(phase => {
          const ids = grouped.get(phase) || [];
          if (ids.length === 0) return null;
          const label = (window.PHASE_LABEL[phase] || phase).replace(/Phase \d+: /, "");
          const phaseDone = ids.filter(id => nodeStatus.get(id) === "done").length;
          const phaseRunning = ids.some(id => nodeStatus.get(id) === "running");

          // Weight node widths by elapsed_ms (executed nodes); pending/skipped get min weight
          const MIN_WEIGHT = 1;
          const weights = ids.map((id) => {
            const t = nodeTimings && nodeTimings.get ? nodeTimings.get(id) : null;
            const ms = (t && typeof t.elapsed_ms === "number") ? t.elapsed_ms : 0;
            return Math.max(ms, MIN_WEIGHT);
          });
          const sumW = weights.reduce((a, b) => a + b, 0) || 1;

          return (
            <div key={phase} className={"tl-phase" + (phaseRunning ? " is-active" : "")}>
              <span className="tl-phase-label">{label}</span>
              <div className="tl-phase-track">
                {ids.map((id, i) => {
                  const st = nodeStatus.get(id) || "pending";
                  const isCursor = phase === cursorPhase && i === cursorIdxInPhase;
                  const t = nodeTimings && nodeTimings.get ? nodeTimings.get(id) : null;
                  const ms = (t && typeof t.elapsed_ms === "number") ? t.elapsed_ms : null;
                  const pct = (weights[i] / sumW) * 100;
                  const titleStr = id + " · " + st + (ms != null ? ` · ${(ms / 1000).toFixed(2)}s` : "");
                  return (
                    <button
                      key={id}
                      type="button"
                      className={"tl-node is-" + st + (id === selectedId ? " is-selected" : "") + (isCursor ? " is-cursor" : "")}
                      data-node-id={id}
                      title={titleStr}
                      onClick={() => onSelect(id)}
                      style={{ flex: `${weights[i]} ${weights[i]} 0`, minWidth: ms != null ? 4 : 2 }}
                    />
                  );
                })}
              </div>
            </div>
          );
        })}
        {(grouped.get("_other") || []).length > 0 && (
          <div className="tl-phase">
            <span className="tl-phase-label">other</span>
            <div className="tl-phase-track">
              {grouped.get("_other").map(id => {
                const st = nodeStatus.get(id) || "pending";
                return (
                  <button
                    key={id}
                    type="button"
                    className={"tl-node is-" + st + (id === selectedId ? " is-selected" : "")}
                    data-node-id={id}
                    title={id + " · " + st}
                    onClick={() => onSelect(id)}
                  />
                );
              })}
            </div>
          </div>
        )}
      </div>

      <div className="timeline-r mono">{doneCount}/{total}</div>
    </footer>
  );
}

window.BottomBus = BottomBus;
