// node-views-verify.jsx — specialized view components for CVE remediation verify phase

const { useState } = React;

// ─── SandboxRunView (3-phase Docker sandbox proof) ─────────────────────────

function SandboxRunView({ node, content, progress, isLive, clockInNode }) {
  const [cmdOpen, setCmdOpen] = useState(false);
  const phases = content.phases || [];
  const icons = { before: "⚠", apply: "↻", after: "✓" };
  const tones = { before: "warn", apply: "info", after: "ok" };
  const colors = { warn: "var(--warn)", info: "var(--info)", ok: "var(--ok)" };
  const bgAlpha = { warn: "rgba(245,181,74,.12)", info: "rgba(122,182,255,.12)", ok: "rgba(95,207,144,.12)" };

  const pState = (ph, i) => {
    if (progress >= ph.doneAt) return "complete";
    return (i === 0 || progress >= phases[i - 1].doneAt) ? "active" : "pending";
  };
  const done = phases.filter(p => progress >= p.doneAt).length;
  const showExit = !isLive || progress >= 0.95;
  const showDiv = progress >= 0.90;
  const dur = content.output?.match(/(\d+)ms/);

  return (
    <div className="nv-grid">
      <Panel title="proof pipeline" className="span-3">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 0, padding: "8px 0" }}>
          {phases.map((ph, i) => {
            const st = pState(ph, i), tone = tones[ph.id], c = colors[tone];
            const bdr = st === "complete" ? c : st === "active" ? "var(--accent)" : "var(--line-2)";
            return (
              <React.Fragment key={ph.id}>
                {i > 0 && <div style={{ width: 24, display: "flex", alignItems: "center" }}><div style={{ flex: 1, borderTop: "2px solid var(--line-2)", marginRight: 2 }} /><span style={{ color: "var(--fg-3)", fontSize: 10 }}>▷</span></div>}
                <div style={{ minWidth: 140, padding: "12px 16px", textAlign: "center", border: `2px solid ${bdr}`, borderRadius: 8, background: st === "complete" ? bgAlpha[tone] : "transparent", opacity: st === "pending" ? 0.4 : 1 }}>
                  <div style={{ fontSize: 18, marginBottom: 4 }}>{st === "active" ? <span className="gp-spin" /> : st === "complete" ? icons[ph.id] : "◌"}</div>
                  <div style={{ fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase", letterSpacing: ".08em", marginBottom: 4 }}>{ph.label}</div>
                  <div className="mono" style={{ fontSize: 13, fontWeight: 600, color: st === "complete" ? c : "var(--fg-2)" }}>{st === "complete" ? ph.result : "—"}</div>
                </div>
              </React.Fragment>
            );
          })}
        </div>
      </Panel>

      <Panel title="container" className="span-1">
        <dl className="kv"><dt>image sha</dt><dd className="mono">{content.imageSha}</dd><dt>runtime</dt><dd className="mono">docker</dd></dl>
      </Panel>
      <Panel title="exit code" className="span-1">
        <div className="bigstat">
          <div className={"bigstat-v " + (showExit ? (content.exitCode === 0 ? "ok" : "fail") : "")}>{showExit ? content.exitCode : "—"}</div>
          <div className="bigstat-k">{showExit ? "exit code" : "running"}</div>
        </div>
      </Panel>
      <Panel title="phase detail" className="span-1">
        {phases.map((ph, i) => {
          const st = pState(ph, i), tone = tones[ph.id];
          return (
            <div key={ph.id} style={{ marginBottom: 8 }}>
              <div style={{ fontWeight: 600, fontSize: 11, color: "var(--fg-2)", textTransform: "uppercase", letterSpacing: ".06em", marginBottom: 2 }}>{ph.label}</div>
              <div style={{ fontSize: 11.5, color: "var(--fg-1)", marginBottom: 2 }}>{st === "complete" ? ph.detail : "—"}</div>
              {st === "complete" && ph.id !== "apply" && <Pill tone={tone}>{ph.result}</Pill>}
            </div>
          );
        })}
      </Panel>

      {showDiv && <Panel className="span-3">
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", color: content.divergence.drift === 0 ? "var(--ok)" : "var(--err)" }}>
          <span style={{ fontSize: 14 }}>{content.divergence.drift === 0 ? "✓" : "✕"}</span>
          <span className="mono" style={{ fontSize: 12 }}>{content.divergence.drift} prod drift · {content.divergence.sig}</span>
        </div>
      </Panel>}

      <Panel title="stats" className="span-3">
        <div className="statsbar">
          <div className="stat"><div className="stat-v mono">{done}/3</div><div className="stat-k">phases</div></div>
          <div className="stat"><div className="stat-v mono">{showExit ? content.exitCode : "—"}</div><div className="stat-k">exit code</div></div>
          <div className="stat"><div className="stat-v mono">{showExit && dur ? (parseInt(dur[1]) / 1000).toFixed(1) + "s" : "—"}</div><div className="stat-k">duration</div></div>
          <div className="stat"><div className="stat-v mono">{showDiv ? content.divergence.drift : "—"}</div><div className="stat-k">drift</div></div>
        </div>
      </Panel>

      <Panel title="command" className="span-3" right={<button className="ghost-btn" onClick={() => setCmdOpen(!cmdOpen)}>{cmdOpen ? "collapse" : "expand"}</button>}>
        {cmdOpen ? <pre className="code code-term">$ {content.cmd}</pre> : <span className="muted mono" style={{ fontSize: 12 }}>$ docker run ...</span>}
      </Panel>
    </div>
  );
}

// ─── RetroAnalysisView (post-remediation learning) ─────────────────────────

function RetroAnalysisView({ node, content, progress, isLive }) {
  const resp = (typeof toolResponseFor === "function" ? toolResponseFor(node) : null) || {
    ok: true, signals: ["static_detection_skip=false", "retro_template_lookup_hit=true"],
    suggestions: [
      { id: "S1", text: "Add lab profile java/11-corretto", cite: "outcomes://F12/2026-04" },
      { id: "S2", text: "Pin maven-central mirror for log4j-*", cite: "doc+://policies/registry-pin" },
      { id: "S3", text: "Extend retro KG with log4j family edge", cite: "kg://retros/CVE-2021-45046" },
    ],
  };
  const obs = content.args?.observable_state || {};
  const detectors = content.args?.detector_signals || [];
  const suggestions = resp.suggestions || [];
  const obsEntries = [
    { k: "sandbox", v: obs.sandbox_skipped ? "skipped" : "ok", ok: !obs.sandbox_skipped, at: 0.10 },
    { k: "static detection", v: obs.static_detection_status, ok: obs.static_detection_status === "ok", at: 0.20 },
    { k: "framework map", v: obs.framework_mapping_status, ok: obs.framework_mapping_status === "ok", at: 0.30 },
    { k: "prior hits", v: String(obs.graph_prior_hits), ok: true, at: 0.40 },
  ];
  const suggAt = [0.55, 0.70, 0.85], sigAt = [0.25, 0.45];
  const showSugg = progress >= 0.50;

  return (
    <div className="nv-grid">
      <Panel title="prevention suggestions" className="span-3" right={<span className="muted mono">{showSugg ? suggestions.filter((_, i) => progress >= suggAt[i]).length : 0} suggestions</span>}>
        {!showSugg ? (
          <div className="streaming-bar"><span className="streaming-bar-fill" /><span className="streaming-bar-label mono">evaluating detector signals...</span></div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {suggestions.map((s, i) => progress < suggAt[i] ? null : (
              <div key={s.id} style={{ borderLeft: "3px solid var(--accent)", padding: "10px 14px", background: "var(--bg-2)", borderRadius: "0 6px 6px 0" }}>
                <span className="mono" style={{ fontSize: 10, background: "var(--accent-dim)", color: "var(--accent)", padding: "1px 6px", borderRadius: 3, marginRight: 8 }}>{s.id}</span>
                <span style={{ fontSize: 12, color: "var(--fg-1)" }}>{s.text}</span>
                <div style={{ marginTop: 4 }}><span className="mono" style={{ fontSize: 11, color: "var(--info)" }}>{s.cite} ↗</span></div>
              </div>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="observable state" className="span-1">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {obsEntries.map(e => {
            const vis = !isLive || progress >= e.at;
            return (
              <div key={e.k} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5 }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0, background: vis ? (e.ok ? "var(--ok)" : "var(--warn)") : "var(--mute-2)" }} />
                <span style={{ color: "var(--fg-2)" }}>{e.k}</span>
                <span className="mono" style={{ marginLeft: "auto", color: "var(--fg-1)" }}>{vis ? e.v : "—"}</span>
              </div>
            );
          })}
        </div>
      </Panel>
      <Panel title="detector signals" className="span-2">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {detectors.map((sig, i) => {
            const vis = !isLive || progress >= sigAt[i];
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5, opacity: vis ? 1 : 0.3 }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", flexShrink: 0, background: vis ? (sig.includes("hit") ? "var(--warn)" : "var(--ok)") : "var(--mute-2)" }} />
                <span className="mono" style={{ color: "var(--fg-1)" }}>{vis ? sig : "—"}</span>
              </div>
            );
          })}
        </div>
      </Panel>

      <Panel title="stats" className="span-3">
        <div className="statsbar">
          <div className="stat"><div className="stat-v mono">{suggestions.length}</div><div className="stat-k">suggestions</div></div>
          <div className="stat"><div className="stat-v mono">{detectors.length}</div><div className="stat-k">signals</div></div>
          <div className="stat"><div className="stat-v mono">{obsEntries.filter(e => e.ok).length}/{obsEntries.length}</div><div className="stat-k">checks passed</div></div>
          <div className="stat"><div className="stat-v mono" style={{ color: resp.ok ? "var(--ok)" : "var(--err)" }}>{resp.ok ? "ok" : "fail"}</div><div className="stat-k">overall</div></div>
        </div>
      </Panel>
    </div>
  );
}

// ─── OpenChangeRequestView (ServiceNow CR delivery) ────────────────────────

function OpenChangeRequestView({ node, content, progress, isLive }) {
  const resp = (typeof toolResponseFor === "function" ? toolResponseFor(node) : null) || {
    ok: true, number: "CHG0041997", url: "https://kraken.service-now.com/change_request.do?CHG0041997", attestation: "ed25519:7c91…ab8d",
  };
  const show = !isLive || progress > 0.6;
  const args = content.args || {}, cis = args.cmdb_ci || [], att = args.attachments || [];
  const attType = (u) => u.startsWith("doc+://") ? { l: "doc+", t: "info" } : u.startsWith("artifact://") ? { l: "artifact", t: "ok" } : { l: "ref", t: "info" };

  return (
    <div className="nv-grid">
      <Panel title="change request" className="span-3">
        {show ? (
          <div style={{ display: "flex", gap: 24, alignItems: "flex-start", padding: "4px 0" }}>
            <div>
              <div className="bigstat"><div className="bigstat-v ok">{resp.number}</div><div className="bigstat-k">cr number</div></div>
              <a href={resp.url} target="_blank" rel="noopener" className="mono" style={{ fontSize: 11, color: "var(--info)", textDecoration: "none" }}>{resp.url} ↗</a>
            </div>
            <dl className="kv" style={{ flex: 1 }}>
              <dt>status</dt><dd><Pill tone="info">awaiting CAB</Pill></dd>
              <dt>assigned</dt><dd className="mono">{args.assignment_group}</dd>
              <dt>CIs</dt><dd className="mono">{cis.length}</dd>
              <dt>signed</dt><dd className="mono">{resp.attestation}</dd>
            </dl>
          </div>
        ) : (
          <div style={{ display: "flex", gap: 24, alignItems: "center", padding: "4px 0" }}>
            <div className="bigstat"><div className="bigstat-v">—</div><div className="bigstat-k">awaiting</div></div>
            <div style={{ flex: 1 }}><div className="streaming-bar"><span className="streaming-bar-fill" /><span className="streaming-bar-label mono">opening change request...</span></div></div>
          </div>
        )}
      </Panel>

      <Panel title="description" className="span-1">
        <p style={{ margin: 0, fontSize: 12, color: "var(--fg-1)", lineHeight: 1.5 }}>{args.short_description}</p>
      </Panel>
      <Panel title="assignment" className="span-2">
        <dl className="kv"><dt>group</dt><dd className="mono">{args.assignment_group}</dd><dt>CIs</dt><dd /></dl>
        <div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 3 }}>
          {cis.map((ci, i) => <span key={i} className="mono" style={{ fontSize: 11.5, color: "var(--fg-1)", paddingLeft: 2 }}>{ci}</span>)}
        </div>
      </Panel>

      <Panel title="attachments" className="span-3" right={<span className="muted mono">{att.length} items</span>}>
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {att.map((uri, i) => { const t = attType(uri); return (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11.5 }}>
              <Pill tone={t.t}>{t.l}</Pill><span className="mono" style={{ color: "var(--fg-1)", flex: 1 }}>{uri}</span>{show && <Pill tone="ok">attached</Pill>}
            </div>
          ); })}
        </div>
      </Panel>

      <Panel title="stats" className="span-3">
        <div className="statsbar">
          <div className="stat"><div className="stat-v mono">{show ? resp.number : "—"}</div><div className="stat-k">cr number</div></div>
          <div className="stat"><div className="stat-v mono">{att.length}</div><div className="stat-k">attachments</div></div>
          <div className="stat"><div className="stat-v mono">{show ? resp.attestation?.slice(-8) : "—"}</div><div className="stat-k">attestation</div></div>
          <div className="stat"><div className="stat-v mono">{cis.length}</div><div className="stat-k">CIs</div></div>
        </div>
      </Panel>
    </div>
  );
}

// ─── TierTerminalTrackView (conscious deferral — re-evaluate in +7d) ───────

function TierTerminalTrackView({ node, content, progress, isLive }) {
  const args = content.args || {};
  const triggers = [
    { k: "EPSS", desc: "percentile exceeds threshold" },
    { k: "KEV", desc: "added to CISA Known Exploited list" },
    { k: "Reachability", desc: "new path discovered to exposed asset" },
    { k: "Manual", desc: "analyst escalation or policy override" },
  ];

  return (
    <div className="nv-grid">
      <Panel className="span-3">
        <div style={{ display: "flex", gap: 24, alignItems: "flex-start", padding: "8px 0" }}>
          <div className="bigstat">
            <div className="bigstat-v" style={{ color: "var(--fg-2)" }}>{args.reevaluate_at || "+7d"}</div>
            <div className="bigstat-k">next check</div>
          </div>
          <div style={{ flex: 1, display: "flex", flexDirection: "column", gap: 6, paddingTop: 6 }}>
            <div style={{ fontSize: 12, color: "var(--fg-2)" }}>{args.reason}</div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span className="mono muted" style={{ fontSize: 12 }}>{args.cve_id}</span>
              <Pill tone="info">tracking</Pill>
            </div>
          </div>
        </div>
      </Panel>

      <Panel title="reference" className="span-1">
        <dl className="kv"><dt>cve</dt><dd className="mono">{args.cve_id}</dd><dt>tier</dt><dd className="mono">track</dd><dt>exposure</dt><dd className="mono">none</dd></dl>
      </Panel>
      <Panel title="re-evaluation triggers" className="span-2">
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {triggers.map(t => (
            <div key={t.k} style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: 11.5 }}>
              <span className="mono" style={{ fontWeight: 600, color: "var(--fg-2)", minWidth: 80 }}>{t.k}</span>
              <span style={{ color: "var(--fg-3)" }}>{t.desc}</span>
            </div>
          ))}
        </div>
      </Panel>

      <Panel title="stats" className="span-3">
        <div className="statsbar">
          <div className="stat"><div className="stat-v mono">{args.reevaluate_at || "+7d"}</div><div className="stat-k">window</div></div>
          <div className="stat"><div className="stat-v mono">track</div><div className="stat-k">tier</div></div>
          <div className="stat"><div className="stat-v mono">0</div><div className="stat-k">exposed</div></div>
        </div>
      </Panel>
    </div>
  );
}

Object.assign(window, { SandboxRunView, RetroAnalysisView, OpenChangeRequestView, TierTerminalTrackView });
