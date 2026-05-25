// header-gantt.jsx — horizontal Gantt-style timing strip in header area.

const { useState: useStateHG, useEffect: useEffectHG, useRef: useRefHG } = React;

function HeaderGantt({ topo, nodeTimings, runStartTs, nodeStatus, selectedId, onSelect }) {
  const [now, setNow] = useStateHG(Date.now);
  const rafRef = useRefHG(null);

  // RAF ticker: re-render to grow running bar width
  useEffectHG(() => {
    const hasRunning = Array.from(nodeStatus.values()).some(s => s === "running");
    if (!hasRunning) return;
    const tick = () => {
      setNow(Date.now());
      rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => { if (rafRef.current) cancelAnimationFrame(rafRef.current); };
  }, [nodeStatus]);

  // Empty state
  if (!nodeTimings || nodeTimings.size === 0 || !runStartTs) {
    return <div className="hg-strip" aria-label="run timing not yet available" />;
  }

  const startMs = new Date(runStartTs).getTime();
  const endMs = now;
  const totalSpan = Math.max(endMs - startMs, 1);

  return (
    <div className="hg-strip">
      {topo.order.map(id => {
        const t = nodeTimings.get(id);
        if (!t || !t.ts_in) return null;
        const status = nodeStatus.get(id) || "pending";
        if (status === "pending") return null;

        const inMs = new Date(t.ts_in).getTime();
        const outMs = t.ts_out ? new Date(t.ts_out).getTime() : now;
        const elapsed_ms = outMs - inMs;
        const pctLeft = ((inMs - startMs) / totalSpan * 100).toFixed(2) + "%";
        const pctWidth = Math.max(0.3, (outMs - inMs) / totalSpan * 100).toFixed(2) + "%";

        const classes = "hg-bar"
          + (status === "running" ? " is-running" : "")
          + (status === "done" ? " is-done" : "")
          + (status === "failed" ? " is-failed" : "")
          + (id === selectedId ? " is-selected" : "");

        return (
          <button
            key={id}
            type="button"
            className={classes}
            data-node-id={id}
            title={id + " · " + Math.round(elapsed_ms) + "ms"}
            style={{ left: pctLeft, width: pctWidth }}
            onClick={() => onSelect(id)}
          />
        );
      })}
    </div>
  );
}

window.HeaderGantt = HeaderGantt;
