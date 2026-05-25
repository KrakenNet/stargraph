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

// ─── Window exports ───────────────────────────────────────────────────────

window.panelForNode = panelForNode;
window.FAMILY_PANEL = FAMILY_PANEL;
window.PRIORITY_PANEL = PRIORITY_PANEL;
window.PRIORITY_IDS = PRIORITY_IDS;
window.CARGONET_IDS = CARGONET_IDS;
window.UnimplementedPanel = UnimplementedPanel;
window.PHASE_ORDER = PHASE_ORDER;
window.PHASE_LABEL = PHASE_LABEL;
window.EMPTY_COPY = EMPTY_COPY;
window.CARGONET_DIAGNOSTIC_FIELDS = CARGONET_DIAGNOSTIC_FIELDS;
window.panelDataNodeId = panelDataNodeId;
