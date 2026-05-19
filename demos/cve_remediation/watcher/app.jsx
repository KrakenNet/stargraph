// app.jsx — shell, transport controls, timeline, tweaks

const { useState: useState2, useEffect: useEffect2, useRef: useRef2, useMemo: useMemo2 } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "speed": 1,
  "panelSide": "left",
  "accent": "emerald",
  "density": "regular",
  "wireSimplification": "default"
}/*EDITMODE-END*/;

const ACCENT_MAP = {
  emerald: { accent: "#3ddc97", accentDim: "rgba(61,220,151,.14)", ring: "rgba(61,220,151,.4)" },
  amber:   { accent: "#f5b54a", accentDim: "rgba(245,181,74,.16)", ring: "rgba(245,181,74,.4)" },
  cyan:    { accent: "#5cdfe6", accentDim: "rgba(92,223,230,.14)", ring: "rgba(92,223,230,.4)" },
  violet:  { accent: "#a89cff", accentDim: "rgba(168,156,255,.16)", ring: "rgba(168,156,255,.4)" },
};

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);

  // Optional ?run=<run_id> binds the watcher to a live harbor WS stream. With
  // no query param the page plays a deterministic simulated cve-rem run so the
  // design renders against representative data offline.
  const wsRunId = useMemo2(() => new URLSearchParams(window.location.search).get("run"), []);

  const seedClock = wsRunId
    ? 0
    : WORKGRAPH.nodes.find((n) => n.id === "remediation_discovery").startAt + 26;
  const [clock, setClock] = useState2(seedClock);
  const [playing, setPlaying] = useState2(true);
  const [selectedId, setSelectedId] = useState2(null);

  // Simulated clock — loops back to the start once it reaches the end so the
  // demo keeps showing live activity. Real WS mode (?run=…) disables this.
  useEffect2(() => {
    if (wsRunId) return;
    if (!playing) return;
    const id = setInterval(() => {
      setClock((c) => {
        const next = c + 0.25 * (t.speed || 1);
        if (next >= WORKGRAPH.totalDuration) {
          // brief hold on the final state, then restart the run.
          return 0;
        }
        return next;
      });
    }, 100);
    return () => clearInterval(id);
  }, [playing, t.speed, wsRunId]);

  // Live WS clock: real cve-rem nodes map onto WORKGRAPH stages via
  // REAL_TO_STAGE; each TransitionEvent advances the clock to the
  // matching stage (never backwards). ResultEvent ends the run.
  //
  // The broadcaster on the harbor side is only registered once the first
  // event fires for a run, so an early WS connect gets 1008 "run not
  // found" and closes. We retry every 750ms until the broadcaster
  // appears (or the run terminates).
  //
  // On initial load, peek /v1/runs/{id} — if the run is already
  // terminal, snap straight to the end state (the broadcaster is gone
  // and we have no audit-log replay to drive a from-scratch playback).
  useEffect2(() => {
    if (!wsRunId) return;
    fetch(`/v1/runs/${encodeURIComponent(wsRunId)}`)
      .then((r) => r.ok ? r.json() : null)
      .then((j) => {
        if (j && (j.status === "done" || j.status === "failed" || j.status === "cancelled")) {
          setClock(WORKGRAPH.totalDuration);
          setPlaying(false);
        }
      })
      .catch(() => {});
  }, [wsRunId]);

  useEffect2(() => {
    if (!wsRunId) return;
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/v1/runs/${wsRunId}/stream`;
    const stageStart = new Map(WORKGRAPH.nodes.map((n) => [n.id, n.startAt]));
    let ws = null;
    let stopped = false;
    let retryTimer = null;

    const handleFrame = (ev) => {
      let frame;
      try { frame = JSON.parse(ev.data); } catch { return; }
      if (frame.type === "transition" && frame.to_node) {
        const stageId = REAL_TO_STAGE[frame.to_node] || frame.to_node;
        const startAt = stageStart.get(stageId);
        if (startAt != null) {
          setClock((c) => Math.max(c, startAt + 0.1));
        }
      } else if (frame.type === "result") {
        setClock(WORKGRAPH.totalDuration);
        setPlaying(false);
        stopped = true;
      }
    };

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(url);
      ws.onmessage = handleFrame;
      ws.onclose = (e) => {
        if (stopped) return;
        // 1008 "run not found" → broadcaster not yet registered; retry.
        // Anything else: terminal — let the page sit on its current state.
        if (e.code === 1008 && /not found/i.test(e.reason || "")) {
          retryTimer = setTimeout(connect, 750);
        }
      };
    };
    connect();

    return () => {
      stopped = true;
      if (retryTimer) clearTimeout(retryTimer);
      if (ws) ws.close();
    };
  }, [wsRunId]);

  // figure out "current" node based on clock
  const currentNode =
    WORKGRAPH.nodes.find((n) => clock >= n.startAt && clock < n.endAt && n.type !== "start" && n.type !== "end")
    || WORKGRAPH.nodes[WORKGRAPH.nodes.length - 1];

  const viewNode =
    (selectedId && WORKGRAPH.nodes.find((n) => n.id === selectedId)) || currentNode;
  const viewStatus = statusFor(viewNode, clock);

  const accent = ACCENT_MAP[t.accent] || ACCENT_MAP.emerald;

  return (
    <div
      className="app"
      data-side={t.panelSide}
      data-density={t.density}
      style={{
        "--accent": accent.accent,
        "--accent-dim": accent.accentDim,
        "--accent-ring": accent.ring,
      }}
    >
      <TopBar
        currentNode={currentNode}
        clock={clock}
        playing={playing}
        onPlay={() => setPlaying((p) => !p)}
      />

      <main className="main" data-side={t.panelSide}>
        <GraphPanel
          clock={clock}
          selectedId={viewNode.id}
          onSelect={(id) => {
            setSelectedId(id);
            setPlaying(false);
          }}
          side={t.panelSide}
        />

        <section className="view" data-screen-label={"node " + viewNode.label}>
          <NodeView node={viewNode} status={viewStatus} clock={clock} />
        </section>
      </main>

      <Timeline
        clock={clock}
        onScrub={(v) => { setClock(v); setSelectedId(null); }}
        selectedId={selectedId}
        onClearSelection={() => setSelectedId(null)}
        viewingLive={!selectedId}
      />

      <TweaksPanel>
        <TweakSection label="Playback" />
        <TweakSlider
          label="Speed"
          value={t.speed}
          min={0.25}
          max={4}
          step={0.25}
          unit="×"
          onChange={(v) => setTweak("speed", v)}
        />

        <TweakSection label="Layout" />
        <TweakRadio
          label="Graph side"
          value={t.panelSide}
          options={["left", "right"]}
          onChange={(v) => setTweak("panelSide", v)}
        />
        <TweakRadio
          label="Density"
          value={t.density}
          options={["compact", "regular", "comfy"]}
          onChange={(v) => setTweak("density", v)}
        />

        <TweakSection label="Theme" />
        <TweakColor
          label="Accent"
          value={t.accent}
          options={["emerald", "amber", "cyan", "violet"]}
          swatches={[ACCENT_MAP.emerald.accent, ACCENT_MAP.amber.accent, ACCENT_MAP.cyan.accent, ACCENT_MAP.violet.accent]}
          onChange={(v) => setTweak("accent", v)}
        />
      </TweaksPanel>
    </div>
  );
}

// TweakColor wrapper — the starter expects hex strings; we want named accents.
// Re-implement as a small swatch row.
function TweakColor({ label, value, options, swatches, onChange }) {
  return (
    <div className="twk-row">
      <div className="twk-lbl"><span>{label}</span><span className="twk-val">{value}</span></div>
      <div className="twk-swatches">
        {options.map((opt, i) => (
          <button
            key={opt}
            className={"twk-swatch " + (opt === value ? "is-on" : "")}
            style={{ background: swatches[i] }}
            onClick={() => onChange(opt)}
            aria-label={opt}
          />
        ))}
      </div>
    </div>
  );
}

// ─── Top Bar ────────────────────────────────────────────────────────────────

function TopBar({ currentNode, clock, playing, onPlay }) {
  const pct = Math.min(100, (clock / WORKGRAPH.totalDuration) * 100);
  return (
    <header className="topbar">
      <div className="topbar-l">
        <div className="brand">
          <span className="brand-mark" aria-hidden>◐</span>
          <span className="brand-name">WorkGraph</span>
          <span className="brand-sub">run watcher</span>
        </div>
        <span className="bread mono">
          <span className="muted">org/</span>kraken
          <span className="muted">/</span>cve-rem
          <span className="muted">/runs/</span>{WORKGRAPH.runId}
        </span>
      </div>

      <div className="topbar-c">
        <div className="current-node">
          <span className="current-label">now running</span>
          <span className="current-name">{currentNode.label}</span>
          <span className="current-bar">
            <span className="current-bar-fill" style={{ width: pct + "%" }} />
          </span>
          <span className="current-time mono">
            {Math.floor(clock)}s / {WORKGRAPH.totalDuration}s
          </span>
        </div>
      </div>

      <div className="topbar-r">
        <button className="ghost-btn" onClick={onPlay}>
          {playing ? "❚❚ pause" : "▶ resume"}
        </button>
        <button className="ghost-btn">share</button>
        <button className="primary-btn">stop run</button>
      </div>
    </header>
  );
}

// ─── Timeline ──────────────────────────────────────────────────────────────

function Timeline({ clock, onScrub, selectedId, onClearSelection, viewingLive }) {
  const trackRef = useRef2(null);

  const handleClick = (e) => {
    const rect = trackRef.current.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const frac = Math.max(0, Math.min(1, x / rect.width));
    onScrub(frac * WORKGRAPH.totalDuration);
  };

  return (
    <footer className="timeline">
      <div className="timeline-l">
        <span className="tl-label">timeline</span>
        {!viewingLive && (
          <button className="tl-jump" onClick={onClearSelection}>
            ⟲ jump to live
          </button>
        )}
      </div>

      <div className="timeline-track" ref={trackRef} onClick={handleClick}>
        {WORKGRAPH.nodes.map((n) => {
          if (n.type === "start" || n.type === "end") return null;
          const leftPct = (n.startAt / WORKGRAPH.totalDuration) * 100;
          const widthPct = ((n.duration || 0) / WORKGRAPH.totalDuration) * 100;
          const s = statusFor(n, clock);
          return (
            <span
              key={n.id}
              className={"tl-seg is-" + s + (n.id === selectedId ? " is-selected" : "")}
              style={{ left: leftPct + "%", width: widthPct + "%" }}
              title={n.label}
            >
              <span className="tl-seg-label">{n.label}</span>
            </span>
          );
        })}
        <span
          className="tl-cursor"
          style={{ left: (clock / WORKGRAPH.totalDuration) * 100 + "%" }}
        />
      </div>

      <div className="timeline-r mono">
        {Math.floor(clock)}s
      </div>
    </footer>
  );
}

// ─── Launcher (CVE input + recent runs) ────────────────────────────────────

const CVE_GRAPH_ID = "graph:cve-rem-pipeline";
const QUICK_CVES = [
  "CVE-2021-44228",  // Log4Shell
  "CVE-2023-44487",  // HTTP/2 Rapid Reset
  "CVE-2024-3094",   // xz-utils backdoor
];

function Launcher() {
  const [cveId, setCveId] = useState2("CVE-2021-44228");
  const [runs, setRuns] = useState2([]);
  const [err, setErr] = useState2("");
  const [submitting, setSubmitting] = useState2(false);

  const loadRuns = async () => {
    try {
      const res = await fetch("/v1/runs?limit=25");
      if (!res.ok) return;
      const j = await res.json();
      setRuns(Array.isArray(j.items) ? j.items : []);
    } catch (e) { /* network blip — leave list as-is */ }
  };

  useEffect2(() => {
    loadRuns();
    const id = setInterval(loadRuns, 2500);
    return () => clearInterval(id);
  }, []);

  const submit = async () => {
    const id = (cveId || "").trim().toUpperCase();
    if (!/^CVE-\d{4}-\d{4,}$/.test(id)) {
      setErr(`bad cve id ${JSON.stringify(id)} — expected CVE-YYYY-NNNN`);
      return;
    }
    setErr("");
    setSubmitting(true);
    try {
      const res = await fetch("/v1/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          graph_id: CVE_GRAPH_ID,
          params: { cve_id: id, trigger_kind: "manual" },
        }),
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        setErr(`POST /v1/runs → ${res.status} ${j.detail || JSON.stringify(j)}`);
        setSubmitting(false);
        return;
      }
      window.location.search = "?run=" + encodeURIComponent(j.run_id);
    } catch (e) {
      setErr(String(e));
      setSubmitting(false);
    }
  };

  return (
    <div className="launcher">
      <div className="launcher-card">
        <div className="launcher-head">
          <span className="launcher-brand-mark">◐</span>
          <span className="launcher-brand">cve-rem · run watcher</span>
          <span className="launcher-sub">graph:cve-rem-pipeline</span>
        </div>

        <div className="launcher-title">Run a CVE</div>
        <div className="launcher-row">
          <input
            className="launcher-input"
            placeholder="CVE-2021-44228"
            value={cveId}
            onChange={(e) => setCveId(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") submit(); }}
          />
          <button className="launcher-go" onClick={submit} disabled={submitting}>
            {submitting ? "starting…" : "▶ run"}
          </button>
        </div>
        <div className="launcher-quick">
          <span style={{ color: "var(--fg-3)" }}>quick:</span>
          {QUICK_CVES.map((q) => (
            <button key={q} onClick={() => setCveId(q)}>{q}</button>
          ))}
        </div>
        <div className="launcher-err">{err}</div>

        <div className="launcher-title">Recent runs</div>
        {runs.length === 0 ? (
          <div style={{ color: "var(--fg-3)", fontSize: 12 }}>none yet — start one above</div>
        ) : (
          <ul className="launcher-runs">
            {runs.map((r) => (
              <a key={r.run_id} className="launcher-run" href={"?run=" + encodeURIComponent(r.run_id)}>
                <span className="launcher-run-id">{r.run_id}</span>
                <span className={"launcher-run-status is-" + r.status}>{r.status}</span>
                <span className="launcher-run-trig mono">{r.trigger_source || "—"}</span>
                <span className="launcher-run-time">{fmtStarted(r.started_at)}</span>
              </a>
            ))}
          </ul>
        )}

        <div className="launcher-foot">
          <span>cve-rem · {runs.length} run{runs.length === 1 ? "" : "s"} loaded</span>
          <a href="?demo=1">or watch simulated demo →</a>
        </div>
      </div>
    </div>
  );
}

function fmtStarted(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const now = Date.now();
  const ageSec = Math.floor((now - d.getTime()) / 1000);
  if (ageSec < 60) return ageSec + "s ago";
  if (ageSec < 3600) return Math.floor(ageSec / 60) + "m ago";
  if (ageSec < 86400) return Math.floor(ageSec / 3600) + "h ago";
  return d.toISOString().slice(0, 16).replace("T", " ");
}

// ─── Mode selector ─────────────────────────────────────────────────────────

function Root() {
  const qs = new URLSearchParams(window.location.search);
  const runId = qs.get("run");
  const isDemo = qs.has("demo");
  if (runId) return <LiveApp runId={runId} />;
  if (isDemo) return <App />;
  return <Launcher />;
}

ReactDOM.createRoot(document.getElementById("root")).render(<Root />);
