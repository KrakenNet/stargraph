// summary-panel.jsx — reusable primitives for FinalSummaryPanel (and others)
// Buildless React 18 + Babel-standalone. All exports via window.* globals.

function Collapsible({ title, children }) {
  return (
    <details className="collapsible">
      <summary className="collapsible-title">{title}</summary>
      <div className="collapsible-body">{children}</div>
    </details>
  );
}

function CopyButton({ value }) {
  const [copied, setCopied] = React.useState(false);

  const handleClick = () => {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1000);
    });
  };

  return (
    <button className="copy-btn" onClick={handleClick} title="Copy to clipboard">
      {copied ? "copied" : "copy"}
    </button>
  );
}

function DiagnosticBlock({ title, rows }) {
  return (
    <div className="diagnostic-block">
      {title && <h4 className="diagnostic-block-title">{title}</h4>}
      <dl className="diagnostic-block-dl">
        {rows.map((row, i) => (
          <React.Fragment key={i}>
            <dt className={row.danger ? "diagnostic-danger" : ""}>{row.label}</dt>
            <dd className={row.danger ? "diagnostic-danger" : ""}>{row.value}</dd>
          </React.Fragment>
        ))}
      </dl>
    </div>
  );
}

function EmptyState({ text }) {
  return <p className="empty-state">{text}</p>;
}

// ─── FinalSummaryPanel ────────────────────────────────────────────────────

function FinalSummaryPanel({ runState, events, runTerminal }) {
  const missing = <i className="muted">&mdash;</i>;

  if (!runTerminal) {
    return (
      <div data-panel-id="__summary__">
        <p className="muted">run not yet terminal &mdash; summary populates on completion</p>
      </div>
    );
  }

  const resultEv = events.find(e => e.type === "result");
  const durationMs = resultEv && resultEv.payload ? resultEv.payload.run_duration_ms : null;

  const s = runState || {};

  // CR block
  const crBlock = (
    <DiagnosticBlock title="Change Request" rows={[
      { label: "CR ID", value: s.cr_correlation_id || missing },
      { label: "Status", value: s.cr_status || missing },
      { label: "Lifecycle States", value: s.cr_lifecycle_states ? JSON.stringify(s.cr_lifecycle_states) : missing },
    ]} />
  );

  // Hosts block
  const hostNames = s.affected_host_names || [];
  const hostsBlock = (
    <DiagnosticBlock title="Hosts" rows={[
      { label: "Count", value: hostNames.length > 0 ? String(hostNames.length) : missing },
      { label: "Names", value: hostNames.length > 0 ? hostNames.join(", ") : missing },
      { label: "CMDB Query Count", value: s.cmdb_query_count != null ? String(s.cmdb_query_count) : missing },
    ]} />
  );

  // Sandbox block
  const retryAttempts = s.sandbox_retry_attempts || [];
  const sandboxBlock = (
    <DiagnosticBlock title="Sandbox" rows={[
      { label: "Status", value: s.sandbox_status || missing },
      { label: "Quarantined", value: s.sandbox_quarantined != null ? String(s.sandbox_quarantined) : missing },
      { label: "Retry Attempts", value: retryAttempts.length > 0 ? String(retryAttempts.length) : missing },
      { label: "Probe Latency", value: s.sandbox_probe_latency_ms != null ? s.sandbox_probe_latency_ms + "ms" : missing },
    ]} />
  );

  // Retro block
  const failureSignals = s.retro_failure_signals || [];
  const preventionSuggestions = s.retro_prevention_suggestions || [];
  const retroBlock = (
    <DiagnosticBlock title="Retrospective" rows={[
      { label: "Retro ID", value: s.retro_id || missing },
      { label: "Outcome", value: s.retro_outcome || missing },
      { label: "Failure Signals", value: failureSignals.length > 0 ? String(failureSignals.length) : missing },
      { label: "Prevention Suggestions", value: preventionSuggestions.length > 0 ? String(preventionSuggestions.length) : missing },
    ]} />
  );

  // Attestation block (FR-SUM3: krakntrust_key_id is TOP-LEVEL)
  const jws = s.run_attestation_jws || "";
  const jwsDisplay = jws.length > 24 ? jws.slice(0, 12) + "..." + jws.slice(-12) : (jws || missing);
  const attestBlock = (
    <DiagnosticBlock title="Attestation" rows={[
      { label: "Key ID", value: s.krakntrust_key_id || missing },
      { label: "JWS", value: jwsDisplay },
      { label: "Boot Session", value: s.boot_session_id || missing },
    ]} />
  );

  // Doctrine block (FR-SUM4: doctrine_manifest_hash is TOP-LEVEL)
  const doctrineBlock = (
    <DiagnosticBlock title="Doctrine" rows={[
      { label: "Manifest Hash", value: s.doctrine_manifest_hash || missing },
    ]} />
  );

  return (
    <div data-panel-id="__summary__">
      <h2 style={{ margin: "0 0 12px" }}>Run Summary</h2>
      {durationMs != null && <p className="mono" style={{ fontSize: 12, marginBottom: 12 }}>Duration: {durationMs}ms</p>}
      {crBlock}
      {hostsBlock}
      {sandboxBlock}
      {retroBlock}
      {attestBlock}
      {doctrineBlock}
    </div>
  );
}

window.Collapsible = Collapsible;
window.CopyButton = CopyButton;
window.DiagnosticBlock = DiagnosticBlock;
window.EmptyState = EmptyState;
window.FinalSummaryPanel = FinalSummaryPanel;
