// bottom-bus.jsx — phase-grouped segment strip pinned to viewport bottom.
// MIRROR: keep segment markup in sync with RunMiniMap in live-app.jsx (D8 duplication)

const { useEffect: useEffectL, useLayoutEffect: useLayoutEffectL } = React;

function BbSegment({ id, status, selected, onClick }) {
  return (
    <button
      type="button"
      className={"bb-seg" + (status === "running" ? " is-current" : "") + (status === "done" ? " is-done" : "") + (status === "failed" ? " is-failed" : "") + (selected ? " is-selected" : "")}
      data-node-id={id}
      title={id + " · " + status}
      onClick={() => onClick(id)}
    />
  );
}

function BottomBus({ topo, nodeStatus, selectedId, onSelect }) {
  const phaseMap = window.usePhaseMap();

  useEffectL(() => {
    performance.mark("bus.mount");
  }, []);

  useLayoutEffectL(() => {
    performance.mark("bus.painted");
  }, []);

  if (!phaseMap) {
    return (
      <div className="bb" data-bus-state="loading">
        <span className="bb-label">phase map loading</span>
      </div>
    );
  }

  // Group nodes by phase
  const grouped = new Map();
  for (const phase of window.PHASE_ORDER) {
    grouped.set(phase, []);
  }
  grouped.set("_other", []);

  for (const id of topo.order) {
    const phase = phaseMap.phaseFor.get(id) || "_other";
    if (!grouped.has(phase)) grouped.set(phase, []);
    grouped.get(phase).push(id);
  }

  return (
    <div className="bb" data-bus-state="ready">
      {window.PHASE_ORDER.map(phase => {
        const ids = grouped.get(phase) || [];
        if (ids.length === 0) return null;
        return (
          <div key={phase} className="bb-phase">
            <span className="bb-label">{(window.PHASE_LABEL[phase] || phase).replace(/Phase \d+: /, "")}</span>
            {ids.map(id => (
              <BbSegment
                key={id}
                id={id}
                status={nodeStatus.get(id) || "pending"}
                selected={id === selectedId}
                onClick={onSelect}
              />
            ))}
          </div>
        );
      })}
      {(grouped.get("_other") || []).length > 0 && (
        <div className="bb-phase">
          <span className="bb-label">other</span>
          {grouped.get("_other").map(id => (
            <BbSegment
              key={id}
              id={id}
              status={nodeStatus.get(id) || "pending"}
              selected={id === selectedId}
              onClick={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}

window.BottomBus = BottomBus;
