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

// ─── IntakeFetchPanel (FR-P1) ─────────────────────────────────────────────

function IntakeFetchPanel({ node, profile, status, delta, runState, timing, events, runTerminal }) {
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
    return <div data-panel-id="intake_fetch">{headerRow}<p>{EMPTY_COPY.pending}</p></div>;
  }
  if (status === "running" && !delta) {
    return <div data-panel-id="intake_fetch">{headerRow}<p>{EMPTY_COPY.running_empty}</p></div>;
  }
  if (status === "failed") {
    return (
      <div data-panel-id="intake_fetch">
        {headerRow}
        <div style={{ background: "#fee2e2", border: "1px solid #fca5a5", borderRadius: "4px", padding: "8px", color: "#991b1b", marginBottom: "12px" }}>
          {runState.last_intake_error || EMPTY_COPY.failed}
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
              <tr key={f.key}>
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
      {runState.cpe_uris && runState.cpe_uris.length > 0 && (
        <div style={{ marginBottom: "12px" }}>
          <strong style={{ display: "block", marginBottom: "4px" }}>CPE URIs</strong>
          <pre style={{ fontFamily: "monospace", fontSize: "12px", background: "#f8fafc", padding: "8px", borderRadius: "4px", overflowX: "auto", margin: 0 }}>
            {runState.cpe_uris.join("\n")}
          </pre>
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

// ─── Panel registries (stubs — filled by later tasks) ─────────────────────

const PRIORITY_PANEL = {};
const FAMILY_PANEL = {};

// ─── Phase / copy skeletons ───────────────────────────────────────────────

// Populated by usePhaseMap in a later task.
const PHASE_ORDER = [];
const PHASE_LABEL = {};

// Clinical-voice empty-state copy per D3.
const EMPTY_COPY = {
  pending: "pending",
  running_empty: "running — no checkpoint yet",
  done_empty: "no state changes",
  failed: "FAILED",
};

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
 * Precedence:
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

// ─── Window exports ───────────────────────────────────────────────────────

window.panelForNode = panelForNode;
window.FAMILY_PANEL = FAMILY_PANEL;
window.PRIORITY_PANEL = PRIORITY_PANEL;
window.IntakeFetchPanel = IntakeFetchPanel;
window.PRIORITY_IDS = PRIORITY_IDS;
window.CARGONET_IDS = CARGONET_IDS;
window.UnimplementedPanel = UnimplementedPanel;
window.PHASE_ORDER = PHASE_ORDER;
window.PHASE_LABEL = PHASE_LABEL;
window.EMPTY_COPY = EMPTY_COPY;
window.CARGONET_DIAGNOSTIC_FIELDS = CARGONET_DIAGNOSTIC_FIELDS;
window.panelDataNodeId = panelDataNodeId;
