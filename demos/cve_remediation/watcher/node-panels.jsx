// node-panels.jsx — per-node panel dispatcher + shared constants.
//
// Dispatch order (D1): priority-id → cargonet-id → family → OutcomePanel fallback.
// All panels receive uniform 7-prop shape (D2):
//   {node, profile, status, delta, events, timing, runState, runTerminal}
// Exposed on window.* for buildless React (no module imports).

// ─── ID sets ──────────────────────────────────────────────────────────────

const PRIORITY_IDS = new Set([
  "intake_fetch",
  "correlate_assets",
  "sandbox_run",
  "create_change_request",
  "write_retrospective",
  "krakntrust_attest",
  "drift_watch_spawn",
]);

const CARGONET_IDS = new Set([
  "cargonet_lab_telemetry",
  "emit_sandbox_evidence",
  "cargonet_writeback",
]);

// ─── Stub panels ──────────────────────────────────────────────────────────

function UnimplementedPanel({ node }) {
  return <div data-panel-id={node.id}>panel for {node.id} not yet wired</div>;
}

// ─── NFR-4 perf instrumentation ───────────────────────────────────────────

// Playwright reads via performance.getEntriesByName for NFR-4 <100ms swap budget
function usePanelMountMark(node) {
  const { useEffect } = window.React;
  useEffect(() => { performance.mark(`panel.${node.id}.mounted`); }, []);
}

// ─── IntakeFetchPanel (FR-P1) ─────────────────────────────────────────────

/**
 * IntakeFetchPanel — bespoke panel for the intake_fetch priority node.
 *
 * Receives the uniform 7-prop shape per D2:
 *   {node, profile, status, delta, events, timing, runState, runTerminal}
 *
 * Renders advisory source data (GET url, fields, candidate products,
 * affected versions, refs, CPE URIs, error banner, raw advisory toggle).
 */
function IntakeFetchPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const { useState } = React;
  const [rawOpen, setRawOpen] = useState(false);

  // FR-PK2: header row
  const headerRow = (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
      <strong>{profile.title}</strong>
      {profile.family && <span style={{ background: "#e2e8f0", borderRadius: "4px", padding: "2px 6px", fontSize: "12px" }}>{profile.family}</span>}
      <span style={{
        borderRadius: "4px",
        padding: "2px 8px",
        fontSize: "12px",
        background: status === "failed" ? "#fee2e2" : status === "running" ? "#fef3c7" : status === "done" ? "#d1fae5" : "#f1f5f9",
        color: status === "failed" ? "#991b1b" : status === "running" ? "#92400e" : status === "done" ? "#065f46" : "#475569",
      }}>{status}</span>
      {timing && timing.elapsed_ms != null && <span style={{ fontSize: "12px", color: "#64748b" }}>{timing.elapsed_ms}ms</span>}
    </div>
  );

  // FR-PK3: lifecycle gate
  if (status === "pending") {
    return <div data-panel-id="intake_fetch">{headerRow}<p>{emptyCopy("source", "pending")}</p></div>;
  }
  if (status === "running" && !delta) {
    return <div data-panel-id="intake_fetch">{headerRow}<p>{emptyCopy("source", "running_empty")}</p></div>;
  }
  if (status === "failed") {
    return (
      <div data-panel-id="intake_fetch">
        {headerRow}
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "12px" }}>
          {runState.last_intake_error || emptyCopy("source", "failed")}
        </div>
      </div>
    );
  }

  // Advisory fields for FR-P1.2
  const advisoryFields = [
    { key: "cve_vendor", label: "Vendor" },
    { key: "cve_product", label: "Product" },
    { key: "fixed_version", label: "Fixed Version" },
    { key: "vulnerability_status", label: "Vulnerability Status" },
    { key: "install_channel", label: "Install Channel" },
    { key: "osv_package_name", label: "OSV Package Name" },
  ];

  // CPE URIs field name in state is advisory_cpe_uris
  const cpeUris = runState.advisory_cpe_uris || runState.cpe_uris || [];

  return (
    <div data-panel-id="intake_fetch">
      {headerRow}

      {/* FR-P1.1: GET block */}
      {runState.raw_source_url && (
        <div style={{ marginBottom: "12px" }}>
          <span style={{ fontWeight: "bold", marginRight: "8px" }}>GET</span>
          <code>{runState.raw_source_url}</code>
        </div>
      )}

      {/* FR-P1.2: Advisory fields 2-col table */}
      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: "12px" }}>
        <tbody>
          {advisoryFields
            .filter(f => runState[f.key] != null && runState[f.key] !== "")
            .map(f => (
              <tr key={f.key} data-field={f.key}>
                <td style={{ padding: "4px 8px", fontWeight: "500", width: "50%" }}>{f.label}</td>
                <td style={{ padding: "4px 8px" }}>{runState[f.key]}</td>
              </tr>
            ))}
        </tbody>
      </table>

      {/* FR-P1.3: Candidate products chip row */}
      <div style={{ marginBottom: "12px" }}>
        <strong style={{ display: "block", marginBottom: "4px" }}>Candidate Products</strong>
        {runState.candidate_products && runState.candidate_products.length > 0 ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "4px" }}>
            {runState.candidate_products.map((p, i) => (
              <span key={i} style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{p}</span>
            ))}
          </div>
        ) : (
          <span style={{ color: "#64748b", fontStyle: "italic" }}>no candidate products found</span>
        )}
      </div>

      {/* FR-P1.4: Affected versions */}
      <div style={{ marginBottom: "12px" }}>
        <strong style={{ display: "block", marginBottom: "4px" }}>Affected Versions</strong>
        {runState.exact_affected_versions && runState.exact_affected_versions.length > 0 && (
          <ul style={{ margin: "0 0 8px 16px", padding: 0 }}>
            {runState.exact_affected_versions.map((v, i) => <li key={i}>{v}</li>)}
          </ul>
        )}
        {runState.affected_version_ranges && runState.affected_version_ranges.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>From</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>To</th>
              </tr>
            </thead>
            <tbody>
              {runState.affected_version_ranges.map((r, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{r.from || r.introduced || r[0] || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{r.to || r.fixed || r[1] || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* FR-P1.5: Advisory refs */}
      {runState.advisory_refs && runState.advisory_refs.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Advisory References</strong>
          <ul style={{ margin: "0 0 0 16px", padding: 0 }}>
            {runState.advisory_refs.map((ref, i) => <li key={i}>{ref}</li>)}
          </ul>
        </div>
      )}

      {/* FR-P1.6: CPE URIs */}
      {cpeUris.length > 0 && (
        <div data-field="cpe_uris" style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>CPE URIs</strong>
          <ul style={{ margin: "0 0 0 16px", padding: 0, fontFamily: "monospace", fontSize: "12px" }}>
            {cpeUris.map((uri, i) => <li key={i}>{uri}</li>)}
          </ul>
        </div>
      )}

      {/* FR-P1.7: Error banner */}
      {runState.last_intake_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "12px" }}>
          {runState.last_intake_error}
        </div>
      )}

      {/* FR-P1.8: Collapsible raw advisory */}
      {runState.raw_source_body && (
        <div style={{ marginBottom: "12px" }}>
          <button
            onClick={() => setRawOpen(!rawOpen)}
            style={{ background: "none", border: "1px solid #cbd5e1", borderRadius: "4px", padding: "4px 12px", cursor: "pointer" }}
          >
            {rawOpen ? "Hide" : "View"} raw advisory
          </button>
          {rawOpen && (
            <pre style={{ fontFamily: "monospace", fontSize: "11px", background: "#f8fafc", padding: "8px", borderRadius: "4px", overflowX: "auto", marginTop: "8px", maxHeight: "400px", overflow: "auto" }}>
              {typeof runState.raw_source_body === "string" ? runState.raw_source_body : JSON.stringify(runState.raw_source_body, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ─── CorrelateAssetsPanel (FR-P2) ─────────────────────────────────────────

function CorrelateAssetsPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const Collapsible = window.Collapsible;
  const EmptyState = window.EmptyState;

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id="correlate_assets"><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  const cmdbFields = [
    { key: "cmdb_software_name", label: "Software Name" },
    { key: "cmdb_software_sys_id", label: "Software Sys ID" },
    { key: "cmdb_match_score", label: "Match Score" },
    { key: "cmdb_match_quality", label: "Match Quality" },
    { key: "cmdb_ci_version", label: "CI Version" },
    { key: "cmdb_version_gate_status", label: "Version Gate Status" },
  ];

  const hosts = runState.affected_host_names || [];
  const correlationMap = runState.cargonet_correlation_map || [];

  return (
    <div data-panel-id="correlate_assets">
      {/* FR-P2.1: Disposition pill */}
      {runState.disposition && (
        <div style={{ marginBottom: "8px" }}>
          <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.disposition}</span>
        </div>
      )}

      {/* FR-P2.2: CMDB block */}
      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: "12px" }}>
        <tbody>
          {cmdbFields.filter(f => runState[f.key] != null && runState[f.key] !== "").map(f => (
            <tr key={f.key}>
              <td style={{ padding: "4px 8px", fontWeight: "500", width: "50%" }}>{f.label}</td>
              <td style={{ padding: "4px 8px" }}>{String(runState[f.key])}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {/* FR-P2.3: Affected hosts table */}
      <div style={{ marginBottom: "12px" }}>
        <strong style={{ display: "block", marginBottom: "4px" }}>Affected Hosts</strong>
        {hosts.length > 0 ? (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <tbody>
              {hosts.map((h, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{h}</td>
                  {runState.substrate_filter && runState.substrate_filter[h] && (
                    <td style={{ padding: "4px 8px", fontSize: "12px", color: "#64748b" }}>{runState.substrate_filter[h]}</td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <EmptyState text="no hosts matched" />
        )}
      </div>

      {/* FR-P2.4: CMDB query count */}
      {runState.cmdb_query_count != null && (
        <div style={{ marginBottom: "12px" }}>
          <strong>Rows returned:</strong> {runState.cmdb_query_count}
        </div>
      )}

      {/* FR-P2.5: CargoNet correlation block */}
      {runState.cargonet_node_count > 0 && (
        <div style={{ marginBottom: "12px", padding: "8px", background: "#f0fdf4", borderRadius: "4px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>CargoNet Correlation</strong>
          {runState.cargonet_lab_ref && <div><span style={{ fontWeight: "500" }}>Lab ref:</span> {runState.cargonet_lab_ref}</div>}
          <div><span style={{ fontWeight: "500" }}>Node count:</span> {runState.cargonet_node_count}</div>
          {correlationMap.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "8px" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Source</th>
                  <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Target</th>
                </tr>
              </thead>
              <tbody>
                {correlationMap.map((row, i) => (
                  <tr key={i}>
                    <td style={{ padding: "4px 8px" }}>{row.source || row[0] || ""}</td>
                    <td style={{ padding: "4px 8px" }}>{row.target || row[1] || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* FR-P2.6: Agent trace */}
      {runState.correlate_agent_trace && (
        <Collapsible title="Correlate Agent Trace">
          <pre style={{ fontFamily: "monospace", fontSize: "11px", whiteSpace: "pre-wrap" }}>
            {typeof runState.correlate_agent_trace === "string" ? runState.correlate_agent_trace : JSON.stringify(runState.correlate_agent_trace, null, 2)}
          </pre>
        </Collapsible>
      )}

      {/* FR-P2.8: Error banners */}
      {runState.last_cmdb_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_cmdb_error}
        </div>
      )}
      {runState.last_cargonet_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_cargonet_error}
        </div>
      )}
    </div>
  );
}

// ─── SandboxRunPanel (FR-P3) ──────────────────────────────────────────────

function SandboxRunPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const EmptyState = window.EmptyState;

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id="sandbox_run"><p>{emptyCopy("sandbox", lifecycle)}</p></div>;
  }

  const probePhases = ["baseline", "apply", "rollback", "reapply"];
  const probeSteps = runState.sandbox_probe_steps || {};
  const retryAttempts = runState.sandbox_retry_attempts || [];
  const staticDetection = runState.static_detection_per_host || [];

  return (
    <div data-panel-id="sandbox_run">
      {/* FR-P3.1: Header enums */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "12px", alignItems: "center" }}>
        {runState.sandbox_runtime && <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.sandbox_runtime}</span>}
        {runState.sandbox_status && <span style={{ background: "#f1f5f9", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.sandbox_status}</span>}
      </div>

      {/* FR-P3.2: 4-row probe table */}
      <div style={{ marginBottom: "12px" }}>
        <strong style={{ display: "block", marginBottom: "4px" }}>Probe Steps</strong>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
          <thead>
            <tr>
              {["Phase", "Status", "Observed", "Expected", "Latency", "Digest", "URI", "OK"].map(h => (
                <th key={h} style={{ textAlign: "left", padding: "4px 6px", borderBottom: "1px solid #e2e8f0" }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {probePhases.map(phase => {
              const step = probeSteps[phase];
              if (!step) return (
                <tr key={phase}>
                  <td style={{ padding: "4px 6px" }}>{phase}</td>
                  <td colSpan={7} style={{ padding: "4px 6px", color: "#64748b", fontStyle: "italic" }}>probe not yet run</td>
                </tr>
              );
              return (
                <tr key={phase}>
                  <td style={{ padding: "4px 6px" }}>{phase}</td>
                  <td style={{ padding: "4px 6px" }}>{step.status || ""}</td>
                  <td style={{ padding: "4px 6px" }}>{step.observed_version || ""}</td>
                  <td style={{ padding: "4px 6px" }}>{step.expected_version || ""}</td>
                  <td style={{ padding: "4px 6px" }}>{step.latency_ms != null ? step.latency_ms : ""}</td>
                  <td style={{ padding: "4px 6px", fontFamily: "monospace", fontSize: "11px" }}>{step.digest || ""}</td>
                  <td style={{ padding: "4px 6px" }}>{step.uri || ""}</td>
                  <td style={{ padding: "4px 6px" }}>{step.ok != null ? (step.ok ? "✓" : "✗") : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* FR-P3.3: Quarantine banner */}
      {runState.sandbox_quarantined === true && (
        <div style={{ background: "#fef3c7", border: "1px solid #fbbf24", borderRadius: "4px", padding: "8px", color: "#92400e", marginBottom: "12px" }}>
          <strong>Quarantined:</strong> {runState.sandbox_quarantine_reason || "reason not provided"}
        </div>
      )}

      {/* FR-P3.4: Retry attempts */}
      {retryAttempts.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Retry Attempts</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <tbody>
              {retryAttempts.map((a, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{a.attempt || i + 1}</td>
                  <td style={{ padding: "4px 8px" }}>{a.status || a.result || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{a.reason || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-P3.5: Static detection per host */}
      {staticDetection.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Static Detection</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <tbody>
              {staticDetection.map((row, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{row.host || row[0] || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{row.status || row[1] || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-P3.6: Probe latency */}
      {runState.sandbox_probe_latency_ms != null && (
        <div style={{ marginBottom: "12px" }}>
          <strong>Probe latency:</strong> {runState.sandbox_probe_latency_ms}ms
        </div>
      )}

      {/* FR-P3.7: Error banner */}
      {runState.last_sandbox_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_sandbox_error}
        </div>
      )}
    </div>
  );
}

// ─── CreateChangeRequestPanel (FR-P4) ─────────────────────────────────────

function CreateChangeRequestPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const Collapsible = window.Collapsible;

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id="create_change_request"><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  const requestBody = runState.cr_request_body || {};
  const lifecycleStates = runState.cr_lifecycle_states || [];

  return (
    <div data-panel-id="create_change_request">
      {/* FR-P4.1: CR header */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "12px", alignItems: "center" }}>
        {runState.cr_correlation_id && <code style={{ fontSize: "13px" }}>{runState.cr_correlation_id}</code>}
        {runState.cr_status && <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.cr_status}</span>}
      </div>

      {/* FR-P4.2: Posted body fields table */}
      {Object.keys(requestBody).length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Request Body</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <tbody>
              {Object.entries(requestBody).map(([k, v]) => {
                const strVal = typeof v === "string" ? v : JSON.stringify(v);
                const truncated = strVal.length > 120;
                return (
                  <tr key={k}>
                    <td style={{ padding: "4px 8px", fontWeight: "500", width: "30%", verticalAlign: "top" }}>{k}</td>
                    <td style={{ padding: "4px 8px" }}>
                      {truncated ? (
                        <Collapsible title={strVal.slice(0, 120) + "…"}>
                          <pre style={{ whiteSpace: "pre-wrap", fontSize: "11px", margin: 0 }}>{strVal}</pre>
                        </Collapsible>
                      ) : strVal}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-P4.3: ServiceNow response */}
      {runState.servicenow_response && (
        <Collapsible title="ServiceNow Response">
          <pre style={{ whiteSpace: "pre-wrap", fontSize: "11px" }}>
            {typeof runState.servicenow_response === "string" ? runState.servicenow_response : JSON.stringify(runState.servicenow_response, null, 2)}
          </pre>
        </Collapsible>
      )}

      {/* FR-P4.4: Sub-records */}
      <div style={{ display: "flex", gap: "16px", marginBottom: "12px", flexWrap: "wrap" }}>
        {runState.task_ci_link_count != null && <div><strong>CI Links:</strong> {runState.task_ci_link_count}</div>}
        {runState.change_task_count != null && <div><strong>Change Tasks:</strong> {runState.change_task_count}</div>}
        {runState.cr_service_lookup_status && <div><strong>Service Lookup:</strong> {runState.cr_service_lookup_status}</div>}
      </div>

      {/* FR-P4.5: Lifecycle chip row */}
      {lifecycleStates.length > 0 && (
        <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", marginBottom: "12px" }}>
          {lifecycleStates.map((s, i) => (
            <span key={i} style={{ background: "#f1f5f9", borderRadius: "4px", padding: "2px 8px", fontSize: "12px" }}>{s}</span>
          ))}
        </div>
      )}

      {/* FR-P4.6: Error banners */}
      {runState.last_cr_link_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_cr_link_error}
        </div>
      )}
      {runState.last_cr_lifecycle_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_cr_lifecycle_error}
        </div>
      )}
    </div>
  );
}

// ─── WriteRetrospectivePanel (FR-P5) ──────────────────────────────────────

function WriteRetrospectivePanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const EmptyState = window.EmptyState;

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id="write_retrospective"><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  const failureSignals = runState.retro_failure_signals || [];
  const preventionSuggestions = runState.retro_prevention_suggestions || [];

  return (
    <div data-panel-id="write_retrospective">
      {/* FR-P5.1: Retro header */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "12px", alignItems: "center" }}>
        {runState.retro_id && <code style={{ fontSize: "13px" }}>{runState.retro_id}</code>}
        {runState.retro_outcome && <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.retro_outcome}</span>}
      </div>

      {/* FR-P5.2: Artifact ref */}
      {runState.retro_payload_artifact_ref && (
        <div style={{ marginBottom: "12px" }}>
          <strong>Artifact:</strong> <code>{runState.retro_payload_artifact_ref}</code>
        </div>
      )}

      {/* FR-P5.3: Failure signals table */}
      {failureSignals.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Failure Signals</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Kind</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Detail</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Evidence</th>
              </tr>
            </thead>
            <tbody>
              {failureSignals.map((s, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{s.kind || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{s.detail || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{s.evidence || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-P5.4: Prevention suggestions table */}
      {preventionSuggestions.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Prevention Suggestions</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Category</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Suggestion</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Rationale</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Cited Signals</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Confidence</th>
              </tr>
            </thead>
            <tbody>
              {preventionSuggestions.map((s, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{s.category || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{s.suggestion || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{s.rationale || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{Array.isArray(s.cited_signals) ? s.cited_signals.join(", ") : (s.cited_signals || "")}</td>
                  <td style={{ padding: "4px 8px" }}>{s.confidence_bp != null ? (s.confidence_bp / 100).toFixed(0) + "%" : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-P5.5: Failure analysis */}
      {runState.retro_failure_analysis && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Failure Analysis</strong>
          <pre className="code" style={{ whiteSpace: "pre-wrap", fontSize: "11px", background: "#f8fafc", padding: "8px", borderRadius: "4px" }}>
            {runState.retro_failure_analysis}
          </pre>
        </div>
      )}

      {/* FR-P5.6: Prior retro context */}
      {(runState.prior_retro_count != null || runState.prior_retro_retrieval_status) && (
        <div style={{ marginBottom: "12px", padding: "8px", background: "#f8fafc", borderRadius: "4px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Prior Retrospectives</strong>
          {runState.prior_retro_count != null && <div>Count: {runState.prior_retro_count}</div>}
          {runState.prior_retro_outcomes && <div>Outcomes: {Array.isArray(runState.prior_retro_outcomes) ? runState.prior_retro_outcomes.join(", ") : runState.prior_retro_outcomes}</div>}
          {runState.prior_retro_retrieval_status && <div>Retrieval status: {runState.prior_retro_retrieval_status}</div>}
          {runState.prior_retro_retrieval_mode && <div>Retrieval mode: {runState.prior_retro_retrieval_mode}</div>}
        </div>
      )}

      {/* FR-P5.7: Error banners */}
      {runState.last_retro_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_retro_error}
        </div>
      )}
      {runState.retro_analysis_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.retro_analysis_error}
        </div>
      )}
    </div>
  );
}

// ─── KrakntrustAttestPanel (FR-P6) ───────────────────────────────────────

function KrakntrustAttestPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const CopyButton = window.CopyButton;

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id="krakntrust_attest"><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  const jws = runState.run_attestation_jws || "";
  const jwsTruncated = jws.length > 24 ? jws.slice(0, 12) + "…" + jws.slice(-12) : jws;

  return (
    <div data-panel-id="krakntrust_attest">
      {/* FR-P6.1: Trust chain */}
      <div style={{ marginBottom: "12px" }}>
        <strong style={{ display: "block", marginBottom: "4px" }}>Trust Chain</strong>
        {runState.krakntrust_key_id && <div><span style={{ fontWeight: "500" }}>Key ID:</span> <code>{runState.krakntrust_key_id}</code></div>}
        {runState.boot_session_id && <div><span style={{ fontWeight: "500" }}>Boot Session:</span> <code>{runState.boot_session_id}</code></div>}
        {runState.prompt_artifact_id && <div><span style={{ fontWeight: "500" }}>Prompt Artifact:</span> <code>{runState.prompt_artifact_id}</code></div>}
      </div>

      {/* FR-P6.2: JWS block — truncated, copy only */}
      {jws && (
        <div style={{ marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
          <strong>JWS:</strong>
          <code style={{ fontSize: "12px", fontFamily: "monospace" }}>{jwsTruncated}</code>
          <CopyButton value={runState.run_attestation_jws} />
        </div>
      )}

      {/* FR-P6.3: Artifact refs */}
      {runState.run_attestation_artifact_ref && (
        <div style={{ marginBottom: "8px" }}>
          <span style={{ fontWeight: "500" }}>Artifact ref:</span> <code>{runState.run_attestation_artifact_ref}</code>
        </div>
      )}
      {runState.run_attestation_attachment_sys_id && (
        <div style={{ marginBottom: "8px" }}>
          <span style={{ fontWeight: "500" }}>Attachment sys_id:</span> <code>{runState.run_attestation_attachment_sys_id}</code>
        </div>
      )}

      {/* FR-P6.4: Doctrine manifest hash */}
      {runState.doctrine_manifest_hash && (
        <div style={{ marginBottom: "12px" }}>
          <span style={{ fontWeight: "500" }}>Doctrine manifest hash:</span> <code style={{ fontFamily: "monospace", fontSize: "12px" }}>{runState.doctrine_manifest_hash}</code>
        </div>
      )}

      {/* FR-P6.5: Error banner */}
      {runState.last_attestation_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_attestation_error}
        </div>
      )}
    </div>
  );
}

// ─── DriftWatchSpawnPanel (FR-P7) ─────────────────────────────────────────

function DriftWatchSpawnPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const EmptyState = window.EmptyState;

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id="drift_watch_spawn"><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  const driftEvents = runState.drift_events || [];

  return (
    <div data-panel-id="drift_watch_spawn">
      {/* FR-P7.1: Child run link */}
      <div style={{ marginBottom: "12px" }}>
        <strong>Child Run:</strong>{" "}
        {runState.drift_child_run_id ? (
          <a href={`/watch/?run=${runState.drift_child_run_id}`} target="_blank" rel="noopener noreferrer">
            {runState.drift_child_run_id}
          </a>
        ) : (
          <span style={{ color: "#64748b", fontStyle: "italic" }}>not spawned</span>
        )}
      </div>

      {/* FR-P7.2: Spawn path */}
      {runState.drift_spawn_path && (
        <div style={{ marginBottom: "12px" }}>
          <strong>Spawn path:</strong> <code>{runState.drift_spawn_path}</code>
        </div>
      )}

      {/* FR-P7.3: Watch window */}
      {runState.drift_watch_window_hours != null && (
        <div style={{ marginBottom: "12px" }}>
          <strong>Monitoring window:</strong> {runState.drift_watch_window_hours}h
        </div>
      )}

      {/* FR-P7.4: Drift events list */}
      {driftEvents.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Drift Events</strong>
          <ul style={{ margin: "0 0 0 16px", padding: 0 }}>
            {driftEvents.map((ev, i) => (
              <li key={i}>{typeof ev === "string" ? ev : JSON.stringify(ev)}</li>
            ))}
          </ul>
        </div>
      )}

      {/* FR-P7.5: Error banner */}
      {runState.last_drift_spawn_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_drift_spawn_error}
        </div>
      )}
    </div>
  );
}

// ─── CargonetFamilyPanel (FR-F-CARGONET) ──────────────────────────────────

const CARGONET_DIAGNOSTIC_FIELDS_FULL = [
  { key: "cargonet_lab_ref", label: "Lab ref" },
  { key: "cargonet_proxy_ref", label: "Proxy ref" },
  { key: "cargonet_node_count", label: "Node count" },
  { key: "cargonet_correlation_map", label: "Correlation map" },
  { key: "last_cargonet_error", label: "Error", danger: true },
  { key: "cargonet_writeback_done", label: "Writeback done" },
  { key: "verify_probe_method", label: "Probe method" },
];

function CargonetFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const React = window.React;
  const CopyButton = window.CopyButton;
  const DiagnosticBlock = window.DiagnosticBlock;
  const EmptyState = window.EmptyState;

  // Base header: family badge, status pill, timing
  const headerRow = (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
      <span style={{ background: "#e2e8f0", borderRadius: "4px", padding: "2px 6px", fontSize: "12px" }}>cargonet</span>
      <span style={{
        borderRadius: "4px",
        padding: "2px 8px",
        fontSize: "12px",
        background: status === "failed" ? "#fee2e2" : status === "running" ? "#fef3c7" : status === "done" ? "#d1fae5" : "#f1f5f9",
        color: status === "failed" ? "#991b1b" : status === "running" ? "#92400e" : status === "done" ? "#065f46" : "#475569",
      }}>{status}</span>
      {timing && timing.elapsed_ms != null && <span style={{ fontSize: "12px", color: "#64748b" }}>{timing.elapsed_ms}ms</span>}
    </div>
  );

  // Per-node switch
  switch (node.id) {
    case "cargonet_lab_telemetry": {
      // FR-F-CARGONET.2: iterate non-empty delta keys; tool_call events; diagnostic fallback
      const toolCallEvents = events.filter(e => e.type === "tool_call");
      const deltaFields = delta && delta.fields ? Object.keys(delta.fields).filter(k => delta.fields[k] != null) : [];

      if (deltaFields.length > 0 || toolCallEvents.length > 0) {
        return (
          <div data-panel-id={node.id}>
            {headerRow}
            {deltaFields.length > 0 && (
              <div style={{ marginBottom: "12px" }}>
                <strong style={{ display: "block", marginBottom: "4px" }}>Delta Fields</strong>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                  <tbody>
                    {deltaFields.map(k => (
                      <tr key={k}>
                        <td style={{ padding: "4px 8px", fontWeight: "500" }}>{k}</td>
                        <td style={{ padding: "4px 8px" }}>{typeof delta.fields[k] === "object" ? JSON.stringify(delta.fields[k]) : String(delta.fields[k])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {toolCallEvents.length > 0 && (
              <div style={{ marginBottom: "12px" }}>
                <strong style={{ display: "block", marginBottom: "4px" }}>Tool Calls</strong>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                  <thead>
                    <tr>
                      <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Tool</th>
                      <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {toolCallEvents.map((e, i) => (
                      <tr key={i}>
                        <td style={{ padding: "4px 8px" }}>{e.tool || e.name || ""}</td>
                        <td style={{ padding: "4px 8px" }}>{e.status || e.result || ""}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      }

      // done + empty delta → diagnostic block
      if (status === "done") {
        const diagRows = CARGONET_DIAGNOSTIC_FIELDS_FULL
          .map(f => ({ label: f.label, value: runState[f.key], danger: f.danger }))
          .filter(r => r.value != null && r.value !== "" && r.value !== false);
        if (diagRows.length === 0) {
          return (
            <div data-panel-id={node.id}>
              {headerRow}
              <EmptyState text="cargonet broker unreached — no telemetry, no proxy refs" />
            </div>
          );
        }
        return (
          <div data-panel-id={node.id}>
            {headerRow}
            <DiagnosticBlock title="Cargonet Diagnostics" rows={diagRows} />
          </div>
        );
      }

      return <div data-panel-id={node.id}>{headerRow}<p>{emptyCopy("default", status === "pending" ? "pending" : "running_empty")}</p></div>;
    }

    case "emit_sandbox_evidence": {
      // FR-F-CARGONET.3: artifact ref + CopyButton; artifact_written events
      const artifactRef = runState.sandbox_evidence_artifact_ref;
      const artifactEvents = events.filter(e => e.type === "artifact_written");

      if (!artifactRef && artifactEvents.length === 0) {
        return (
          <div data-panel-id={node.id}>
            {headerRow}
            <EmptyState text="no evidence emitted" />
          </div>
        );
      }

      return (
        <div data-panel-id={node.id}>
          {headerRow}
          {artifactRef && (
            <div style={{ marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
              <strong>Evidence artifact:</strong> <code>{artifactRef}</code>
              <CopyButton value={artifactRef} />
            </div>
          )}
          {artifactEvents.length > 0 && (
            <div style={{ marginBottom: "12px" }}>
              <strong style={{ display: "block", marginBottom: "4px" }}>Artifacts Written</strong>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Hash</th>
                    <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Size</th>
                    <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>MIME</th>
                    <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Provenance</th>
                  </tr>
                </thead>
                <tbody>
                  {artifactEvents.map((e, i) => (
                    <tr key={i}>
                      <td style={{ padding: "4px 8px", fontFamily: "monospace", fontSize: "11px" }}>{e.hash || ""}</td>
                      <td style={{ padding: "4px 8px" }}>{e.size || ""}</td>
                      <td style={{ padding: "4px 8px" }}>{e.mime || e.content_type || ""}</td>
                      <td style={{ padding: "4px 8px" }}>{e.provenance || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      );
    }

    case "cargonet_writeback": {
      // FR-F-CARGONET.4: bool pill, lab ref as target, error banner, pending state
      const done = runState.cargonet_writeback_done;
      const error = runState.last_cargonet_error;

      return (
        <div data-panel-id={node.id}>
          {headerRow}
          <div style={{ marginBottom: "12px", display: "flex", alignItems: "center", gap: "8px" }}>
            <strong>Writeback:</strong>
            <span style={{
              display: "inline-block", width: "12px", height: "12px", borderRadius: "50%",
              background: done === true ? "#22c55e" : "#94a3b8",
            }} />
            <span>{done === true ? "complete" : "pending"}</span>
          </div>
          {runState.cargonet_lab_ref && (
            <div style={{ marginBottom: "12px" }}>
              <strong>Target lab ref:</strong> <code>{runState.cargonet_lab_ref}</code>
            </div>
          )}
          {error && (
            <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
              {error}
            </div>
          )}
          {done !== true && !error && (
            <p style={{ color: "#64748b", fontStyle: "italic" }}>writeback pending</p>
          )}
        </div>
      );
    }

    default:
      return <div data-panel-id={node.id}>{headerRow}<p>Unknown cargonet node: {node.id}</p></div>;
  }
}

// ─── GateFamilyPanel (FR-F1) ──────────────────────────────────────────────

function GateFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("gate", lifecycle)}</p></div>;
  }

  // FR-F1.1: verdict per node id
  let passed = false;
  let reason = "";
  if (node.id === "halt_new_gate") {
    passed = runState.halt_new_active === false;
    reason = runState.halt_reason || "";
  } else if (node.id === "plan_quarantine_gate") {
    passed = runState.plan_quarantined === false;
    reason = runState.plan_quarantine_reason || "";
  } else if (node.id === "divergence_quarantine") {
    passed = !runState.gepa_divergence_record_id;
    reason = runState.gepa_divergence_record_id ? ("divergence record: " + runState.gepa_divergence_record_id) : "";
  }

  return (
    <div data-panel-id={node.id}>
      {/* FR-F1.1: verdict pill */}
      <div style={{ marginBottom: "8px" }}>
        <span style={{
          background: passed ? "#d1fae5" : "#fee2e2",
          color: passed ? "#065f46" : "#991b1b",
          borderRadius: "4px", padding: "2px 8px", fontSize: "13px",
        }}>{passed ? "passed" : "halted"}</span>
      </div>

      {/* FR-F1.2: reason */}
      {reason && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Reason:</strong> {reason}
        </div>
      )}

      {/* FR-F1.3: telemetry fields */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
        <tbody>
          {runState.halt_new_active != null && (
            <tr><td style={{ padding: "4px 8px", fontWeight: "500" }}>halt_new_active</td><td style={{ padding: "4px 8px" }}>{String(runState.halt_new_active)}</td></tr>
          )}
          {runState.plan_quarantined != null && (
            <tr><td style={{ padding: "4px 8px", fontWeight: "500" }}>plan_quarantined</td><td style={{ padding: "4px 8px" }}>{String(runState.plan_quarantined)}</td></tr>
          )}
          {runState.gepa_divergence_record_id != null && (
            <tr><td style={{ padding: "4px 8px", fontWeight: "500" }}>gepa_divergence_record_id</td><td style={{ padding: "4px 8px" }}>{runState.gepa_divergence_record_id}</td></tr>
          )}
        </tbody>
      </table>

      {/* Error banner */}
      {status === "failed" && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginTop: "8px" }}>
          {emptyCopy("gate", "failed")}
        </div>
      )}
    </div>
  );
}

// ─── DecisionFamilyPanel (FR-F3) ─────────────────────────────────────────

function DecisionFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("decision", lifecycle)}</p></div>;
  }

  // FR-F3.1: decision from transition event
  const transitionEvent = events.find(e => e.type === "transition");
  const decision = transitionEvent ? (transitionEvent.to_node || transitionEvent.decision || "transition recorded") : null;

  // FR-F3.2: per-node inputs
  const DECISION_INPUTS = {
    source_trust_gate: [
      { key: "source_trust", label: "Source Trust" },
      { key: "source_class", label: "Source Class" },
    ],
    ssvc_evaluate: [
      { key: "ssvc_tier", label: "SSVC Tier" },
    ],
    sandbox_dispatch: [
      { key: "sandbox_runtime", label: "Sandbox Runtime" },
    ],
    suppress_not_applicable: [
      { key: "disposition", label: "Disposition" },
    ],
  };

  const inputs = DECISION_INPUTS[node.id] || [];

  return (
    <div data-panel-id={node.id}>
      {/* Decision pill */}
      {decision && (
        <div style={{ marginBottom: "8px" }}>
          <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{decision}</span>
        </div>
      )}

      {/* Input fields */}
      {inputs.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", marginBottom: "8px" }}>
          <tbody>
            {inputs.filter(f => runState[f.key] != null).map(f => (
              <tr key={f.key}>
                <td style={{ padding: "4px 8px", fontWeight: "500", width: "40%" }}>{f.label}</td>
                <td style={{ padding: "4px 8px" }}>{String(runState[f.key])}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ─── TransformFamilyPanel (FR-F4) ────────────────────────────────────────

function TransformFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  // FR-F4.1: before/after from delta keys with truncated values
  const deltaFields = delta && delta.fields ? Object.keys(delta.fields).filter(k => delta.fields[k] != null) : [];

  // FR-F4.2: canonicalize_* specific
  const isCanonicalize = node.id.startsWith("canonicalize_");
  // FR-F4.3: enrich_* specific
  const isEnrich = node.id.startsWith("enrich_");

  return (
    <div data-panel-id={node.id}>
      {/* FR-F4.1: delta fields */}
      {deltaFields.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Changed Fields</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <tbody>
              {deltaFields.map(k => {
                const val = delta.fields[k];
                const strVal = typeof val === "string" ? val : JSON.stringify(val);
                const truncated = strVal.length > 120 ? strVal.slice(0, 120) + "..." : strVal;
                return (
                  <tr key={k}>
                    <td style={{ padding: "4px 8px", fontWeight: "500", width: "30%" }}>{k}</td>
                    <td style={{ padding: "4px 8px" }}>{truncated}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-F4.2: canonicalize specific */}
      {isCanonicalize && (
        <div style={{ marginBottom: "8px" }}>
          {runState.canonical_body != null && (
            <div><strong>Canonical body length:</strong> {typeof runState.canonical_body === "string" ? runState.canonical_body.length : JSON.stringify(runState.canonical_body).length}</div>
          )}
          {runState.injection_class != null && (
            <div><strong>Injection class:</strong> {runState.injection_class}</div>
          )}
        </div>
      )}

      {/* FR-F4.3: enrich specific */}
      {isEnrich && runState.extract != null && (
        <div style={{ marginBottom: "8px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Extract Summary</strong>
          <pre style={{ fontSize: "11px", whiteSpace: "pre-wrap", background: "#f8fafc", padding: "8px", borderRadius: "4px" }}>
            {typeof runState.extract === "string" ? runState.extract : JSON.stringify(runState.extract, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

// ─── LlmFamilyPanel (FR-F5) ─────────────────────────────────────────────

// FR-F5.1: streaming token block — monospace pre-wrap auto-scroll
function StreamingTokenBlock({ model, text }) {
  const React = window.React;
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  });
  return (
    <div style={{ marginBottom: "12px" }}>
      {model && <div style={{ fontSize: "12px", color: "#64748b", marginBottom: "4px" }}>model: {model}</div>}
      <pre ref={ref} style={{
        fontFamily: "monospace", fontSize: "12px", whiteSpace: "pre-wrap",
        background: "#f8fafc", padding: "8px", borderRadius: "4px",
        maxHeight: "400px", overflow: "auto",
      }}>{text}</pre>
    </div>
  );
}

function LlmFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("llm", lifecycle)}</p></div>;
  }

  // FR-F5.5: mutual exclusion — token streaming early return
  const tokenEvents = events.filter(e => e.type === "token");
  if (tokenEvents.length > 0) {
    const text = tokenEvents.sort((a, b) => a.index - b.index).map(e => e.token).join("");
    const model = tokenEvents[0].model;
    return (
      <div data-panel-id={node.id}>
        <StreamingTokenBlock model={model} text={text} />
      </div>
    );
  }

  // FR-F5.2: static fallback per node
  return (
    <div data-panel-id={node.id}>
      {/* code_writer: FR-F5.3, FR-F5.4 */}
      {node.id === "code_writer" && (
        <div>
          {runState.plan_rationale && (
            <div style={{ marginBottom: "8px" }}>
              <strong>Plan Rationale:</strong>
              <pre style={{ fontSize: "11px", whiteSpace: "pre-wrap", background: "#f8fafc", padding: "8px", borderRadius: "4px", marginTop: "4px" }}>{runState.plan_rationale}</pre>
            </div>
          )}
          {runState.plan_spec && (
            <div style={{ marginBottom: "8px" }}>
              <strong>Plan Spec:</strong>
              <pre style={{ fontSize: "11px", whiteSpace: "pre-wrap", background: "#f8fafc", padding: "8px", borderRadius: "4px", marginTop: "4px" }}>
                {typeof runState.plan_spec === "string" ? runState.plan_spec : JSON.stringify(runState.plan_spec, null, 2)}
              </pre>
            </div>
          )}
          {runState.code_runtime && <div style={{ marginBottom: "4px" }}><strong>Runtime:</strong> {runState.code_runtime}</div>}
          {runState.apply_bundle_ref && <div style={{ marginBottom: "4px" }}><strong>Apply bundle:</strong> <code>{runState.apply_bundle_ref}</code></div>}
          {runState.rollback_bundle_ref && <div style={{ marginBottom: "4px" }}><strong>Rollback bundle:</strong> <code>{runState.rollback_bundle_ref}</code></div>}
          {runState.verify_probe_ref && <div style={{ marginBottom: "4px" }}><strong>Verify probe:</strong> <code>{runState.verify_probe_ref}</code></div>}
        </div>
      )}

      {/* critic */}
      {node.id === "critic" && (
        <div>
          {runState.critic_verdict && <div style={{ marginBottom: "4px" }}><strong>Verdict:</strong> <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.critic_verdict}</span></div>}
          {runState.critic_history && Array.isArray(runState.critic_history) && runState.critic_history.length > 0 && (
            <div style={{ marginBottom: "8px" }}>
              <strong>Latest critique:</strong>
              <pre style={{ fontSize: "11px", whiteSpace: "pre-wrap", background: "#f8fafc", padding: "8px", borderRadius: "4px", marginTop: "4px" }}>
                {typeof runState.critic_history[runState.critic_history.length - 1] === "string"
                  ? runState.critic_history[runState.critic_history.length - 1]
                  : JSON.stringify(runState.critic_history[runState.critic_history.length - 1], null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}

      {/* judge_safety */}
      {node.id === "judge_safety" && runState.judge_safety_verdict && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Safety Verdict:</strong> <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.judge_safety_verdict}</span>
        </div>
      )}

      {/* extract_* */}
      {node.id.startsWith("extract_") && runState.extract != null && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Extract:</strong>
          <pre style={{ fontSize: "11px", whiteSpace: "pre-wrap", background: "#f8fafc", padding: "8px", borderRadius: "4px", marginTop: "4px" }}>
            {typeof runState.extract === "string" ? runState.extract : JSON.stringify(runState.extract, null, 2)}
          </pre>
        </div>
      )}

      {/* injection_classify */}
      {node.id === "injection_classify" && runState.injection_class != null && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Injection Class:</strong> <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState.injection_class}</span>
        </div>
      )}

      {/* done_empty fallback */}
      {status === "done" && !delta && <p>{emptyCopy("llm", "done_empty")}</p>}
    </div>
  );
}

// ─── AuditFamilyPanel (FR-F6) ────────────────────────────────────────────

function AuditFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  return (
    <div data-panel-id={node.id}>
      {/* FR-F6.1: audit written bool */}
      <div style={{ marginBottom: "8px", display: "flex", alignItems: "center", gap: "8px" }}>
        <strong>Audit written:</strong>
        <span style={{
          display: "inline-block", width: "12px", height: "12px", borderRadius: "50%",
          background: runState.source_audit_written === true ? "#22c55e" : "#94a3b8",
        }} />
        <span>{runState.source_audit_written === true ? "yes" : "no"}</span>
      </div>

      {/* FR-F6.2: context fields */}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", marginBottom: "8px" }}>
        <tbody>
          {runState.source_trust != null && (
            <tr><td style={{ padding: "4px 8px", fontWeight: "500" }}>Source Trust</td><td style={{ padding: "4px 8px" }}>{String(runState.source_trust)}</td></tr>
          )}
          {runState.source_class != null && (
            <tr><td style={{ padding: "4px 8px", fontWeight: "500" }}>Source Class</td><td style={{ padding: "4px 8px" }}>{String(runState.source_class)}</td></tr>
          )}
          {runState.source_classifier_ran != null && (
            <tr><td style={{ padding: "4px 8px", fontWeight: "500" }}>Classifier Ran</td><td style={{ padding: "4px 8px" }}>{String(runState.source_classifier_ran)}</td></tr>
          )}
          {runState.source_trust_violation != null && (
            <tr><td style={{ padding: "4px 8px", fontWeight: "500" }}>Trust Violation</td><td style={{ padding: "4px 8px" }}>{String(runState.source_trust_violation)}</td></tr>
          )}
        </tbody>
      </table>

      {/* FR-F6.3: error banner */}
      {runState.last_source_audit_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_source_audit_error}
        </div>
      )}
    </div>
  );
}

// ─── ArtifactFamilyPanel (FR-F7) ─────────────────────────────────────────

const ARTIFACT_REF_FIELDS = {
  emit_quarantine_artifact: "quarantine_artifact_ref",
  emit_remediation_bundle: "remediation_bundle_artifact_ref",
  emit_evidence_bundle: "evidence_bundle_artifact_ref",
  emit_retro_payload: "retro_payload_artifact_ref",
  emit_docx_archive: "docx_artifact_ref",
  emit_proof_report: "proof_report_artifact_ref",
};

function ArtifactFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("artifact", lifecycle)}</p></div>;
  }

  // FR-F7.1: per-node ref field lookup
  const refField = ARTIFACT_REF_FIELDS[node.id];
  const refValue = refField ? runState[refField] : null;

  // FR-F7.2: artifact_written events
  const artifactEvents = events.filter(e => e.type === "artifact_written");

  if (!refValue && artifactEvents.length === 0) {
    return <div data-panel-id={node.id}><p>{emptyCopy("artifact", "done_empty")}</p></div>;
  }

  return (
    <div data-panel-id={node.id}>
      {refValue && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Artifact ref:</strong> <code>{refValue}</code>
        </div>
      )}

      {artifactEvents.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Artifacts Written</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Hash</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Size</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>MIME</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Provenance</th>
              </tr>
            </thead>
            <tbody>
              {artifactEvents.map((e, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace", fontSize: "11px" }}>{e.hash || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{e.size || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{e.mime || e.content_type || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{e.provenance || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── HitlFamilyPanel (FR-F8) ─────────────────────────────────────────────

function HitlFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed" && lifecycle !== "running_empty") {
    return <div data-panel-id={node.id}><p>{emptyCopy("hitl", lifecycle)}</p></div>;
  }

  // FR-F8.1: waiting_for_input event
  const waitingEvent = events.find(e => e.type === "waiting_for_input");
  const gateName = waitingEvent ? (waitingEvent.gate || waitingEvent.name || node.id) : node.id;
  const prompt = waitingEvent && waitingEvent.payload ? waitingEvent.payload.prompt : null;
  const requestedCapability = waitingEvent ? (waitingEvent.requested_capability || (waitingEvent.payload && waitingEvent.payload.requested_capability)) : null;

  // FR-F8.3: decision from response
  const response = runState.response || {};
  const responseDecision = response.decision;
  const responseActor = response.actor;
  const responseAt = response.at;

  return (
    <div data-panel-id={node.id}>
      {/* Gate name + waiting status */}
      <div style={{ marginBottom: "8px" }}>
        <strong>Gate:</strong> {gateName}
        {waitingEvent && !responseDecision && (
          <span style={{ marginLeft: "8px", background: "#fef3c7", borderRadius: "4px", padding: "2px 8px", fontSize: "12px", color: "#92400e" }}>waiting for input</span>
        )}
      </div>

      {/* FR-F8.2: prompt text */}
      {prompt && (
        <div style={{ marginBottom: "8px", padding: "8px", background: "#f8fafc", borderRadius: "4px" }}>
          <strong>Prompt:</strong> {prompt}
        </div>
      )}

      {/* Requested capability */}
      {requestedCapability && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Requested capability:</strong> {requestedCapability}
        </div>
      )}

      {/* FR-F8.3: decision */}
      {responseDecision && (
        <div style={{ marginBottom: "8px" }}>
          <span style={{ background: "#d1fae5", borderRadius: "4px", padding: "2px 8px", fontSize: "13px", color: "#065f46" }}>{responseDecision}</span>
          {responseActor && <span style={{ marginLeft: "8px", fontSize: "12px", color: "#64748b" }}>by {responseActor}</span>}
          {responseAt && <span style={{ marginLeft: "8px", fontSize: "12px", color: "#64748b" }}>at {responseAt}</span>}
        </div>
      )}

      {/* FR-F8.4: blocked_at */}
      {runState.hitl_blocked_at && (
        <div style={{ marginBottom: "8px", color: "#92400e" }}>
          <strong>Blocked at:</strong> {runState.hitl_blocked_at}
        </div>
      )}

      {/* HITL running_empty shows waiting copy */}
      {lifecycle === "running_empty" && !waitingEvent && (
        <p>{emptyCopy("hitl", "running_empty")}</p>
      )}
    </div>
  );
}

// ─── BranchFamilyPanel (FR-F9) ───────────────────────────────────────────

function BranchFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("branch", lifecycle)}</p></div>;
  }

  // FR-F9.1: route from transition event
  const transitionEvent = events.find(e => e.type === "transition");
  const toNode = transitionEvent ? transitionEvent.to_node : null;

  // FR-F9.2: routing input from NODE_PROFILE outputs
  const nodeProfile = window.NODE_PROFILE && window.NODE_PROFILE[node.id];
  const outputsDoc = nodeProfile && nodeProfile.outputs ? nodeProfile.outputs : null;

  if (!toNode && !delta) {
    return <div data-panel-id={node.id}><p>{emptyCopy("branch", "done_empty")}</p></div>;
  }

  return (
    <div data-panel-id={node.id}>
      {/* Route taken */}
      {toNode && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Route:</strong> <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{toNode}</span>
        </div>
      )}

      {/* Routing input doc */}
      {outputsDoc && (
        <div style={{ marginBottom: "8px", fontSize: "12px", color: "#64748b" }}>
          <strong>Routing outputs:</strong> {typeof outputsDoc === "string" ? outputsDoc : JSON.stringify(outputsDoc)}
        </div>
      )}

      {/* Delta fields if any */}
      {delta && delta.fields && Object.keys(delta.fields).length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
          <tbody>
            {Object.entries(delta.fields).filter(([, v]) => v != null).map(([k, v]) => (
              <tr key={k}>
                <td style={{ padding: "4px 8px", fontWeight: "500" }}>{k}</td>
                <td style={{ padding: "4px 8px" }}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ─── AgentFamilyPanel (FR-F10) ───────────────────────────────────────────

function AgentFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);
  const Collapsible = window.Collapsible;

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  if (node.id === "planner") {
    const agentTrace = runState.planner_agent_trace || [];
    const verifierFindings = runState.planner_verifier_findings || [];
    const ragSources = runState.planner_rag_sources || [];

    return (
      <div data-panel-id={node.id}>
        {/* FR-F10.1: agent trace */}
        {agentTrace.length > 0 && (
          <Collapsible title={"Agent Trace (" + agentTrace.length + " steps)"}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
              <tbody>
                {agentTrace.map((row, i) => (
                  <tr key={i}>
                    <td style={{ padding: "4px 8px", fontWeight: "500", width: "15%", verticalAlign: "top" }}>{row.role || ""}</td>
                    <td style={{ padding: "4px 8px" }}>{typeof row.content === "string" ? (row.content.length > 200 ? row.content.slice(0, 200) + "..." : row.content) : JSON.stringify(row.content)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Collapsible>
        )}

        {/* FR-F10.2: verifier findings + pass pill */}
        <div style={{ marginBottom: "8px", display: "flex", alignItems: "center", gap: "8px" }}>
          <strong>Verifier:</strong>
          <span style={{
            background: runState.planner_verifier_passed === true ? "#d1fae5" : runState.planner_verifier_passed === false ? "#fee2e2" : "#f1f5f9",
            color: runState.planner_verifier_passed === true ? "#065f46" : runState.planner_verifier_passed === false ? "#991b1b" : "#475569",
            borderRadius: "4px", padding: "2px 8px", fontSize: "13px",
          }}>{runState.planner_verifier_passed === true ? "passed" : runState.planner_verifier_passed === false ? "failed" : "pending"}</span>
        </div>
        {verifierFindings.length > 0 && (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", marginBottom: "8px" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Finding</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Severity</th>
              </tr>
            </thead>
            <tbody>
              {verifierFindings.map((f, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{f.finding || f.message || JSON.stringify(f)}</td>
                  <td style={{ padding: "4px 8px" }}>{f.severity || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {/* FR-F10.3: RAG sources */}
        {ragSources.length > 0 && (
          <div style={{ marginBottom: "8px" }}>
            <strong style={{ display: "block", marginBottom: "4px" }}>RAG Sources</strong>
            <ul style={{ margin: "0 0 0 16px", padding: 0, fontSize: "13px" }}>
              {ragSources.map((s, i) => <li key={i}>{typeof s === "string" ? s : JSON.stringify(s)}</li>)}
            </ul>
          </div>
        )}

        {/* FR-F10.4: quality metrics */}
        <div style={{ display: "flex", gap: "16px", marginBottom: "8px", flexWrap: "wrap", fontSize: "13px" }}>
          {runState.planner_latency_ms != null && <div><strong>Latency:</strong> {runState.planner_latency_ms}ms</div>}
          {runState.planner_schema_retries != null && <div><strong>Schema retries:</strong> {runState.planner_schema_retries}</div>}
          {runState.plan_quality_score_bp != null && <div><strong>Quality:</strong> {(runState.plan_quality_score_bp / 100).toFixed(0)}%</div>}
        </div>

        {/* FR-F10.7: error */}
        {runState.last_planner_error && (
          <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
            {runState.last_planner_error}
          </div>
        )}
      </div>
    );
  }

  if (node.id === "remediation_discovery") {
    const actions = runState.recommended_actions || [];
    const provenance = runState.recommendation_provenance || {};

    return (
      <div data-panel-id={node.id}>
        {/* FR-F10.5: recommended actions table */}
        {actions.length > 0 && (
          <div style={{ marginBottom: "12px" }}>
            <strong style={{ display: "block", marginBottom: "4px" }}>Recommended Actions</strong>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
              <thead>
                <tr>
                  <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Action</th>
                  <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Type</th>
                  <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Priority</th>
                </tr>
              </thead>
              <tbody>
                {actions.map((a, i) => (
                  <tr key={i}>
                    <td style={{ padding: "4px 8px" }}>{a.action || a.description || JSON.stringify(a)}</td>
                    <td style={{ padding: "4px 8px" }}>{a.type || ""}</td>
                    <td style={{ padding: "4px 8px" }}>{a.priority || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* FR-F10.6: provenance */}
        {Object.keys(provenance).length > 0 && (
          <div style={{ marginBottom: "8px" }}>
            <strong style={{ display: "block", marginBottom: "4px" }}>Provenance</strong>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
              <tbody>
                {Object.entries(provenance).filter(([k]) => k !== "last_error").map(([k, v]) => (
                  <tr key={k}>
                    <td style={{ padding: "4px 8px", fontWeight: "500" }}>{k}</td>
                    <td style={{ padding: "4px 8px" }}>{typeof v === "object" ? JSON.stringify(v) : String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* FR-F10.7: error */}
        {provenance.last_error && (
          <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
            {provenance.last_error}
          </div>
        )}
      </div>
    );
  }

  // Fallback for unknown agent nodes
  return <div data-panel-id={node.id}><p>{emptyCopy("default", "done_empty")}</p></div>;
}

// ─── KgFamilyPanel (FR-F12) ──────────────────────────────────────────────

function KgFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("kg", lifecycle)}</p></div>;
  }

  // FR-F12.1: per-node retrieval status
  const statusFields = {
    vec_search_retros: { status: "prior_retro_retrieval_status", mode: "prior_retro_retrieval_mode" },
    graph_prior_remediations: { status: "graph_prior_retrieval_status" },
    framework_mapping: { status: "framework_mapping_status" },
    plan_template_lookup: { hit: "template_lookup_hit", miss: "template_lookup_miss_reason" },
  };
  const sf = statusFields[node.id];

  return (
    <div data-panel-id={node.id}>
      {/* Retrieval status pill */}
      {sf && (
        <div style={{ marginBottom: "8px" }}>
          {sf.status && runState[sf.status] != null && (
            <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px", marginRight: "8px" }}>{runState[sf.status]}</span>
          )}
          {sf.mode && runState[sf.mode] != null && (
            <span style={{ fontSize: "12px", color: "#64748b" }}>mode: {runState[sf.mode]}</span>
          )}
          {sf.hit != null && runState[sf.hit] != null && (
            <span style={{ background: runState[sf.hit] ? "#d1fae5" : "#fee2e2", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{runState[sf.hit] ? "hit" : "miss"}</span>
          )}
          {sf.miss && runState[sf.miss] && (
            <span style={{ marginLeft: "8px", fontSize: "12px", color: "#64748b" }}>{runState[sf.miss]}</span>
          )}
        </div>
      )}

      {/* FR-F12.2: vec_search_retros → prior_retro_suggestions table */}
      {node.id === "vec_search_retros" && runState.prior_retro_suggestions && runState.prior_retro_suggestions.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Prior Retro Suggestions</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <tbody>
              {runState.prior_retro_suggestions.map((s, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{typeof s === "string" ? s : JSON.stringify(s)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-F12.3: graph_prior_remediations → graph_prior_actions table */}
      {node.id === "graph_prior_remediations" && runState.graph_prior_actions && runState.graph_prior_actions.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Prior Remediation Actions</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <tbody>
              {runState.graph_prior_actions.map((a, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{typeof a === "string" ? a : JSON.stringify(a)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-F12.4: graph_blast_radius */}
      {node.id === "graph_blast_radius" && runState.correlated && runState.correlated.blast_radius_node_count != null && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Blast radius node count:</strong> {runState.correlated.blast_radius_node_count}
        </div>
      )}

      {/* FR-F12.5: framework_mapping → framework_controls + attack_patterns */}
      {node.id === "framework_mapping" && (
        <div>
          {runState.framework_controls && runState.framework_controls.length > 0 && (
            <div style={{ marginBottom: "12px" }}>
              <strong style={{ display: "block", marginBottom: "4px" }}>Framework Controls</strong>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <tbody>
                  {runState.framework_controls.map((c, i) => (
                    <tr key={i}>
                      <td style={{ padding: "4px 8px" }}>{typeof c === "string" ? c : (c.id || "")} {c.name || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {runState.attack_patterns && runState.attack_patterns.length > 0 && (
            <div style={{ marginBottom: "12px" }}>
              <strong style={{ display: "block", marginBottom: "4px" }}>Attack Patterns</strong>
              <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                <tbody>
                  {runState.attack_patterns.map((p, i) => (
                    <tr key={i}>
                      <td style={{ padding: "4px 8px" }}>{typeof p === "string" ? p : (p.id || "")} {p.name || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* FR-F12.6: kg_run_writeback */}
      {node.id === "kg_run_writeback" && (
        <div style={{ marginBottom: "8px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
            <strong>KG writeback:</strong>
            <span style={{
              display: "inline-block", width: "12px", height: "12px", borderRadius: "50%",
              background: runState.kg_run_written === true ? "#22c55e" : "#94a3b8",
            }} />
          </div>
          {runState.kg_run_nodes_written != null && <div style={{ fontSize: "13px" }}>Nodes written: {runState.kg_run_nodes_written}</div>}
          {runState.kg_run_edges_written != null && <div style={{ fontSize: "13px" }}>Edges written: {runState.kg_run_edges_written}</div>}
        </div>
      )}

      {/* FR-F12.7: plan_kg_writeback */}
      {node.id === "plan_kg_writeback" && (
        <div style={{ marginBottom: "8px", display: "flex", alignItems: "center", gap: "8px" }}>
          <strong>Plan KG writeback:</strong>
          <span style={{
            display: "inline-block", width: "12px", height: "12px", borderRadius: "50%",
            background: runState.plan_kg_writeback_done === true ? "#22c55e" : "#94a3b8",
          }} />
          <span>{runState.plan_kg_writeback_done === true ? "done" : "pending"}</span>
        </div>
      )}

      {/* FR-F12.8: error banners */}
      {runState.last_graph_prior_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_graph_prior_error}
        </div>
      )}
      {runState.last_framework_mapping_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_framework_mapping_error}
        </div>
      )}
      {runState.last_kg_run_error && (
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "8px" }}>
          {runState.last_kg_run_error}
        </div>
      )}
    </div>
  );
}

// ─── ToolFamilyPanel (FR-F13) ────────────────────────────────────────────

// FR-F13.3: per-node state field lookup table (DRY)
const TOOL_NODE_FIELDS = {
  attach_all_artifacts: [
    { key: "attachment_sys_ids", label: "Attachment Sys IDs" },
    { key: "attachment_count", label: "Attachment Count" },
    { key: "attachment_manifest", label: "Attachment Manifest" },
    { key: "last_attachment_error", label: "Error", danger: true },
  ],
  progressive_execute: [
    { key: "canary_passed", label: "Canary Passed" },
    { key: "stage_passed", label: "Stage Passed" },
    { key: "fleet_passed", label: "Fleet Passed" },
    { key: "per_host_apply_results", label: "Per-Host Apply Results" },
    { key: "execution_ledger", label: "Execution Ledger" },
  ],
  verify_immediate: [
    { key: "verify_outcome", label: "Verify Outcome" },
    { key: "per_host_verify_results", label: "Per-Host Verify Results" },
    { key: "verify_probe_method", label: "Probe Method" },
  ],
  partial_apply_rollback: [
    { key: "rollback_triggered", label: "Rollback Triggered" },
  ],
  judge_lint: [
    { key: "judge_lint_verdict", label: "Lint Verdict" },
  ],
  publish_docplus: [
    { key: "docplus_published", label: "DocPlus Published" },
    { key: "doc_sys_id", label: "Doc Sys ID" },
    { key: "attachment_sys_id", label: "Attachment Sys ID" },
    { key: "last_docplus_table_error", label: "Error", danger: true },
  ],
  run_outcome_persist: [
    { key: "run_outcome_written", label: "Outcome Written" },
    { key: "last_run_outcome_error", label: "Error", danger: true },
  ],
  cr_self_validate: [
    { key: "cr_self_validation_passed", label: "Validation Passed" },
    { key: "cr_self_validation_findings", label: "Findings" },
    { key: "observed_field_lengths", label: "Observed Field Lengths" },
    { key: "observed_attachment_count", label: "Observed Attachment Count" },
    { key: "observed_journal_count", label: "Observed Journal Count" },
  ],
  render_docx: [
    { key: "docx_artifact_ref", label: "DOCX Artifact Ref" },
    { key: "last_docx_emit_error", label: "Error", danger: true },
  ],
};

function ToolFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  // FR-F13.1: tool-call events
  const toolCallEvents = events.filter(e => e.type === "tool_call");
  // FR-F13.2: tool-result events
  const toolResultEvents = events.filter(e => e.type === "tool_result");

  // FR-F13.3: per-node state fields from lookup table
  const nodeFields = TOOL_NODE_FIELDS[node.id] || [];

  return (
    <div data-panel-id={node.id}>
      {/* FR-F13.1: tool calls */}
      {toolCallEvents.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Tool Calls</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Tool</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Namespace</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Args</th>
              </tr>
            </thead>
            <tbody>
              {toolCallEvents.map((e, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px" }}>{e.tool || e.name || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{e.namespace || ""}</td>
                  <td style={{ padding: "4px 8px", fontSize: "11px" }}>{e.args ? (typeof e.args === "string" ? e.args.slice(0, 80) : JSON.stringify(e.args).slice(0, 80)) : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-F13.2: tool results */}
      {toolResultEvents.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>Tool Results</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <thead>
              <tr>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Call ID</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>OK</th>
                <th style={{ textAlign: "left", padding: "4px 8px", borderBottom: "1px solid #e2e8f0" }}>Result</th>
              </tr>
            </thead>
            <tbody>
              {toolResultEvents.map((e, i) => (
                <tr key={i}>
                  <td style={{ padding: "4px 8px", fontFamily: "monospace", fontSize: "11px" }}>{e.call_id || ""}</td>
                  <td style={{ padding: "4px 8px" }}>{e.ok != null ? (e.ok ? "yes" : "no") : ""}</td>
                  <td style={{ padding: "4px 8px", fontSize: "11px" }}>{e.error || (e.result ? (typeof e.result === "string" ? e.result.slice(0, 80) : JSON.stringify(e.result).slice(0, 80)) : "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* FR-F13.3: per-node state fields */}
      {nodeFields.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>State</strong>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
            <tbody>
              {nodeFields.filter(f => runState[f.key] != null).map(f => {
                const val = runState[f.key];
                const strVal = typeof val === "object" ? JSON.stringify(val) : String(val);
                return (
                  <tr key={f.key}>
                    <td style={{ padding: "4px 8px", fontWeight: "500", width: "40%" }}>{f.label}</td>
                    <td style={{ padding: "4px 8px", color: f.danger ? "#991b1b" : undefined }}>{strVal.length > 120 ? strVal.slice(0, 120) + "..." : strVal}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ─── SandboxFamilyPanel (FR-F14) ─────────────────────────────────────────

function SandboxFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("sandbox", lifecycle)}</p></div>;
  }

  const sandbox = runState.sandbox || {};

  return (
    <div data-panel-id={node.id}>
      {runState.skip_sandbox != null && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Skip sandbox:</strong> <span style={{ background: runState.skip_sandbox ? "#fef3c7" : "#d1fae5", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{String(runState.skip_sandbox)}</span>
        </div>
      )}
      {sandbox.skip_reason && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Skip reason:</strong> {sandbox.skip_reason}
        </div>
      )}
      {sandbox.force_hitl != null && (
        <div style={{ marginBottom: "8px" }}>
          <strong>Force HITL:</strong> {String(sandbox.force_hitl)}
        </div>
      )}
    </div>
  );
}

// ─── JoinFamilyPanel (FR-F15) ────────────────────────────────────────────

function JoinFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : (status === "done" && !delta) ? "done_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("default", lifecycle)}</p></div>;
  }

  if (node.id === "validate_plan_join") {
    return (
      <div data-panel-id={node.id}>
        {runState.validation_passed != null && (
          <div style={{ marginBottom: "8px", display: "flex", alignItems: "center", gap: "8px" }}>
            <strong>Validation:</strong>
            <span style={{
              background: runState.validation_passed ? "#d1fae5" : "#fee2e2",
              color: runState.validation_passed ? "#065f46" : "#991b1b",
              borderRadius: "4px", padding: "2px 8px", fontSize: "13px",
            }}>{runState.validation_passed ? "passed" : "failed"}</span>
          </div>
        )}
        {runState.judge_safety_verdict && (
          <div style={{ marginBottom: "4px" }}><strong>Safety verdict:</strong> {runState.judge_safety_verdict}</div>
        )}
        {runState.judge_lint_verdict && (
          <div style={{ marginBottom: "4px" }}><strong>Lint verdict:</strong> {runState.judge_lint_verdict}</div>
        )}
      </div>
    );
  }

  if (node.id === "retro_join") {
    const transitionEvent = events.find(e => e.type === "transition");
    return (
      <div data-panel-id={node.id}>
        {transitionEvent ? (
          <div style={{ marginBottom: "8px" }}>
            <strong>Downstream:</strong> <span style={{ background: "#e0e7ff", borderRadius: "4px", padding: "2px 8px", fontSize: "13px" }}>{transitionEvent.to_node || "transition recorded"}</span>
          </div>
        ) : (
          <p style={{ color: "#64748b", fontStyle: "italic" }}>awaiting upstream completion</p>
        )}
      </div>
    );
  }

  return <div data-panel-id={node.id}><p>{emptyCopy("default", "done_empty")}</p></div>;
}

// ─── TerminalFamilyPanel (FR-F16) ────────────────────────────────────────

function TerminalFamilyPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
  usePanelMountMark(node);

  const lifecycle = status === "pending" ? "pending"
    : (status === "running" && !delta) ? "running_empty"
    : status === "failed" ? "failed" : null;
  if (lifecycle && lifecycle !== "failed") {
    return <div data-panel-id={node.id}><p>{emptyCopy("terminal", lifecycle)}</p></div>;
  }

  // FR-F16.1: per-id static text
  const TERMINAL_TEXT = {
    tier_terminal_track: "track (re-evaluate at +7d)",
    tier_terminal_defer: "deferred",
    action_done: "run complete",
  };

  const text = TERMINAL_TEXT[node.id] || "terminal";

  // FR-F16.2: run_duration_ms from result event for action_done
  const resultEvent = events.find(e => e.type === "result");
  const durationMs = resultEvent ? resultEvent.run_duration_ms : null;

  return (
    <div data-panel-id={node.id}>
      <div style={{ marginBottom: "8px" }}>
        <span style={{ background: "#d1fae5", borderRadius: "4px", padding: "2px 8px", fontSize: "13px", color: "#065f46" }}>{text}</span>
      </div>
      {node.id === "action_done" && durationMs != null && (
        <div style={{ fontSize: "13px", color: "#64748b" }}>
          <strong>Run duration:</strong> {durationMs}ms
        </div>
      )}
    </div>
  );
}

// ─── Panel registries (stubs — filled by later tasks) ─────────────────────

const PRIORITY_PANEL = {};
const FAMILY_PANEL = {};

// ─── Phase / copy skeletons ───────────────────────────────────────────────

// Populated by usePhaseMap in a later task.
const PHASE_ORDER = [];
const PHASE_LABEL = {};

// single grep-target for all 4×~10 empty-state cells per D3
const EMPTY_COPY = {
  default: {
    pending: "pending",
    running_empty: "running — no checkpoint yet",
    done_empty: "no state changes",
    failed: "FAILED",
  },
  gate: { done_empty: "gate passed (no halt)" },
  decision: { done_empty: "decision made — no state change" },
  branch: { done_empty: "branch not yet evaluated" },
  artifact: { done_empty: "no artifact emitted" },
  hitl: { running_empty: "waiting for human input" },
  llm: { running_empty: "running — awaiting token stream" },
  kg: { done_empty: "no retrieval results" },
  sandbox: { done_empty: "probe not yet run" },
  terminal: { done_empty: "run complete" },
};

/** Return lifecycle copy for a given family, falling back to default. */
function emptyCopy(family, lifecycle) {
  const override = EMPTY_COPY[family];
  if (override && override[lifecycle] !== undefined) {
    return override[lifecycle];
  }
  return EMPTY_COPY.default[lifecycle];
}

// Diagnostic fields for cargonet panels per D12.
const CARGONET_DIAGNOSTIC_FIELDS = [
  "cargonet_lab_ref",
  "cargonet_proxy_ref",
  "cargonet_node_count",
  "cargonet_correlation_map",
  "last_cargonet_error",
  "cargonet_writeback_done",
];

// ─── Helpers ──────────────────────────────────────────────────────────────

/** Return the data-panel-id attribute value for a given node. */
function panelDataNodeId(node) {
  return node.id;
}

// ─── Dispatcher ───────────────────────────────────────────────────────────

/**
 * panelForNode(node) — 3-tier panel dispatcher per D1.
 *
 * Precedence (D1): priority-id → cargonet-id → family → OutcomePanel fallback.
 *
 *   1. PRIORITY_PANEL[node.id]  — bespoke panels for high-value nodes
 *   2. CARGONET_IDS.has(node.id) → CargonetFamilyPanel (when wired)
 *   3. FAMILY_PANEL[profile.family] — family-shaped fallback
 *   4. window.OutcomePanel — generic last-resort
 *
 * Returns a component reference (function), NOT JSX.
 * The caller renders: <Panel {...props} />.
 */
function panelForNode(node) {
  // 1. Priority lookup
  if (PRIORITY_IDS.has(node.id) && PRIORITY_PANEL[node.id]) {
    return PRIORITY_PANEL[node.id];
  }

  // Dev guard: warn on unmapped priority ids (D2 assertion)
  if (process?.env?.NODE_ENV !== "production" && PRIORITY_IDS.has(node.id) && !PRIORITY_PANEL[node.id]) {
    console.warn(`[node-panels] priority id "${node.id}" has no mapped panel in PRIORITY_PANEL`);
  }

  // 2. Cargonet lookup
  if (CARGONET_IDS.has(node.id)) {
    return window.CargonetFamilyPanel || UnimplementedPanel;
  }

  // 3. Family lookup via profile
  const profile = window.NODE_PROFILE && window.NODE_PROFILE[node.id];
  if (profile && profile.family && FAMILY_PANEL[profile.family]) {
    return FAMILY_PANEL[profile.family];
  }

  // 4. Fallback
  return window.OutcomePanel || UnimplementedPanel;
}

// ─── Priority panel registration ─────────────────────────────────────────

PRIORITY_PANEL.intake_fetch = IntakeFetchPanel;
PRIORITY_PANEL.correlate_assets = CorrelateAssetsPanel;
PRIORITY_PANEL.sandbox_run = SandboxRunPanel;
PRIORITY_PANEL.create_change_request = CreateChangeRequestPanel;
PRIORITY_PANEL.write_retrospective = WriteRetrospectivePanel;
PRIORITY_PANEL.krakntrust_attest = KrakntrustAttestPanel;
PRIORITY_PANEL.drift_watch_spawn = DriftWatchSpawnPanel;

// ─── Family panel registration ──────────────────────────────────────────

FAMILY_PANEL.gate = GateFamilyPanel;
FAMILY_PANEL.decision = DecisionFamilyPanel;
FAMILY_PANEL.transform = TransformFamilyPanel;
FAMILY_PANEL.llm = LlmFamilyPanel;
FAMILY_PANEL.audit = AuditFamilyPanel;
FAMILY_PANEL.artifact = ArtifactFamilyPanel;
FAMILY_PANEL.hitl = HitlFamilyPanel;
FAMILY_PANEL.branch = BranchFamilyPanel;
FAMILY_PANEL.agent = AgentFamilyPanel;
FAMILY_PANEL.kg = KgFamilyPanel;
FAMILY_PANEL.tool = ToolFamilyPanel;
FAMILY_PANEL.sandbox = SandboxFamilyPanel;
FAMILY_PANEL.join = JoinFamilyPanel;
FAMILY_PANEL.terminal = TerminalFamilyPanel;

// ─── Event filtering helper ──────────────────────────────────────────────

// reuses existing nodeEvents map dedupe; NFR-10
function eventsForNode(allEvents, id, delta) {
  return allEvents.filter(
    e => e.from_node === id || e.to_node === id || (delta && e.step === delta.step)
  );
}

// ─── Window exports ───────────────────────────────────────────────────────

window.panelForNode = panelForNode;
window.FAMILY_PANEL = FAMILY_PANEL;
window.PRIORITY_PANEL = PRIORITY_PANEL;
window.IntakeFetchPanel = IntakeFetchPanel;
window.CorrelateAssetsPanel = CorrelateAssetsPanel;
window.SandboxRunPanel = SandboxRunPanel;
window.CreateChangeRequestPanel = CreateChangeRequestPanel;
window.WriteRetrospectivePanel = WriteRetrospectivePanel;
window.KrakntrustAttestPanel = KrakntrustAttestPanel;
window.DriftWatchSpawnPanel = DriftWatchSpawnPanel;
window.CargonetFamilyPanel = CargonetFamilyPanel;
window.CARGONET_DIAGNOSTIC_FIELDS_FULL = CARGONET_DIAGNOSTIC_FIELDS_FULL;
window.GateFamilyPanel = GateFamilyPanel;
window.DecisionFamilyPanel = DecisionFamilyPanel;
window.TransformFamilyPanel = TransformFamilyPanel;
window.LlmFamilyPanel = LlmFamilyPanel;
window.StreamingTokenBlock = StreamingTokenBlock;
window.AuditFamilyPanel = AuditFamilyPanel;
window.ArtifactFamilyPanel = ArtifactFamilyPanel;
window.HitlFamilyPanel = HitlFamilyPanel;
window.BranchFamilyPanel = BranchFamilyPanel;
window.AgentFamilyPanel = AgentFamilyPanel;
window.KgFamilyPanel = KgFamilyPanel;
window.ToolFamilyPanel = ToolFamilyPanel;
window.TOOL_NODE_FIELDS = TOOL_NODE_FIELDS;
window.SandboxFamilyPanel = SandboxFamilyPanel;
window.JoinFamilyPanel = JoinFamilyPanel;
window.TerminalFamilyPanel = TerminalFamilyPanel;
window.ARTIFACT_REF_FIELDS = ARTIFACT_REF_FIELDS;
window.PRIORITY_IDS = PRIORITY_IDS;
window.CARGONET_IDS = CARGONET_IDS;
window.UnimplementedPanel = UnimplementedPanel;
window.PHASE_ORDER = PHASE_ORDER;
window.PHASE_LABEL = PHASE_LABEL;
window.EMPTY_COPY = EMPTY_COPY;
window.emptyCopy = emptyCopy;
window.CARGONET_DIAGNOSTIC_FIELDS = CARGONET_DIAGNOSTIC_FIELDS;
window.panelDataNodeId = panelDataNodeId;
window.eventsForNode = eventsForNode;
window.usePanelMountMark = usePanelMountMark;
