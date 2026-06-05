// Run tracker — lists runs from upstream harbor serve, loads paths,
// subscribes to live WebSocket streams.
//
// API surface (all proxied through graph-viewer's own host):
//   GET  /api/runs?status=&limit=&offset=         → run list
//   GET  /api/runs/{id}/events                     → event history (JSONL audit)
//   WS   /api/runs/{id}/stream                     → live event stream

const { h } = preact;
const { useState, useEffect, useCallback, useRef } = preactHooks;

window.fetchRunList = async function(baseUrl, opts) {
  opts = opts || {};
  var params = new URLSearchParams();
  params.set("limit", String(opts.limit || 50));
  if (opts.status) params.set("status", opts.status);
  var url = baseUrl.replace(/\/$/, "") + "/api/runs?" + params.toString();
  var r = await fetch(url);
  if (!r.ok) throw new Error("runs fetch HTTP " + r.status);
  return await r.json();
};

window.fetchRunEvents = async function(baseUrl, runId) {
  var url = baseUrl.replace(/\/$/, "") + "/api/runs/" + encodeURIComponent(runId) + "/events";
  var r = await fetch(url);
  if (!r.ok) throw new Error("events HTTP " + r.status);
  return await r.json();
};

window.fetchRunCheckpoints = async function(baseUrl, runId) {
  var url = baseUrl.replace(/\/$/, "") + "/api/runs/" + encodeURIComponent(runId) + "/checkpoints";
  var r = await fetch(url);
  if (!r.ok) throw new Error("checkpoints HTTP " + r.status);
  return await r.json();
};

// Push nodeId into ordered path; idempotent on dupes (preserves first-seen step).
function _appendIfNew(nodeOrder, nodeSteps, seen, nodeId) {
  if (!nodeId || seen.has(nodeId)) return;
  seen.add(nodeId);
  nodeSteps[nodeId] = nodeOrder.length;
  nodeOrder.push(nodeId);
}

// Compute the path traversal from a flat event list.
// Returns: { nodeOrder: [id...], nodeSteps: {id: stepIdx}, current: id|null, status }
window.computeRunPath = function(events, status) {
  var nodeOrder = [];
  var nodeSteps = {};
  var seen = new Set();

  for (const ev of events) {
    var t = ev.type || ev.event_type || ev.kind || "";
    var payload = ev.payload || {};
    // Transition events have from_node / to_node — use both, in order.
    if (t === "transition") {
      _appendIfNew(nodeOrder, nodeSteps, seen, ev.from_node || payload.from_node);
      _appendIfNew(nodeOrder, nodeSteps, seen, ev.to_node || payload.to_node);
      continue;
    }
    var nodeId = ev.node_id || ev.last_node || ev.next_node ||
                 payload.node_id || payload.last_node || null;
    if (t === "node_started" || t === "node_entered" || t === "step" || t === "node_exit") {
      _appendIfNew(nodeOrder, nodeSteps, seen, nodeId);
    }
  }

  var current = null;
  if (status === "running" || status === "paused") {
    current = nodeOrder.length > 0 ? nodeOrder[nodeOrder.length - 1] : null;
  }

  return { nodeOrder, nodeSteps, current, status };
};

// Compute path from checkpoint rows (ordered by step ascending, each has last_node).
// Checkpoints are more reliable than events because every step writes one.
window.computeRunPathFromCheckpoints = function(checkpoints, status) {
  var nodeOrder = [];
  var nodeSteps = {};
  var seen = new Set();
  for (const cp of checkpoints) {
    _appendIfNew(nodeOrder, nodeSteps, seen, cp.last_node);
  }
  var current = null;
  if (status === "running" || status === "paused") {
    current = nodeOrder.length > 0 ? nodeOrder[nodeOrder.length - 1] : null;
  }
  return { nodeOrder, nodeSteps, current, status };
};

window.RunSubscription = class {
  constructor(baseUrl, runId, onEvent, onStatus) {
    this.runId = runId;
    this.onEvent = onEvent;
    this.onStatus = onStatus;
    var proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    var host = baseUrl.replace(/^https?:\/\//, "").replace(/\/$/, "");
    this.url = proto + "//" + host + "/api/runs/" + encodeURIComponent(runId) + "/stream";
    this.ws = null;
    this.closed = false;
  }

  start() {
    var self = this;
    try {
      this.ws = new WebSocket(this.url);
    } catch (err) {
      if (this.onStatus) this.onStatus("error", err.message);
      return;
    }
    this.ws.onopen = function() {
      if (self.onStatus) self.onStatus("connected", null);
    };
    this.ws.onmessage = function(evt) {
      try {
        var data = JSON.parse(evt.data);
        if (self.onEvent) self.onEvent(data);
      } catch (err) {}
    };
    this.ws.onclose = function() {
      if (!self.closed && self.onStatus) self.onStatus("closed", null);
    };
    this.ws.onerror = function() {
      if (self.onStatus) self.onStatus("error", null);
    };
  }

  stop() {
    this.closed = true;
    if (this.ws) {
      try { this.ws.close(); } catch (err) {}
      this.ws = null;
    }
  }
};

window.RunTrackerPanel = function RunTrackerPanel({ baseUrl, selectedRunId, onSelectRun, runPath }) {
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const loadRuns = useCallback(function() {
    if (!baseUrl) return;
    setLoading(true);
    setError(null);
    window.fetchRunList(baseUrl, { limit: 100 })
      .then(function(data) {
        var items = data.items || data.runs || [];
        items.sort(function(a, b) {
          var ta = new Date(a.started_at || 0).getTime();
          var tb = new Date(b.started_at || 0).getTime();
          return tb - ta;
        });
        setRuns(items);
        setLoading(false);
      })
      .catch(function(err) {
        setError(err.message);
        setLoading(false);
      });
  }, [baseUrl]);

  useEffect(function() {
    loadRuns();
  }, [loadRuns]);

  if (!baseUrl) {
    return h('div', { className: 'runs-empty' },
      'Connect to harbor serve via "--upstream" to see runs');
  }

  return h('div', { className: 'runs-panel' }, [
    h('div', { className: 'runs-toolbar' }, [
      h('button', {
        className: 'runs-refresh-btn',
        onClick: loadRuns,
        disabled: loading,
      }, loading ? '⟳ Loading…' : '⟳ Refresh'),
      runs.length > 0 ? h('span', { className: 'runs-count' }, runs.length + ' runs') : null,
    ]),
    error ? h('div', { className: 'runs-error' }, '⚠ ' + error) : null,
    selectedRunId ? h('div', { className: 'runs-current' }, [
      h('div', { className: 'runs-current-label' }, 'Viewing:'),
      h('div', { className: 'runs-current-id mono' }, selectedRunId),
      runPath ? h('div', { className: 'runs-current-stats' },
        runPath.nodeOrder.length + ' nodes visited' +
        (runPath.current ? ' • current: ' + runPath.current : '')
      ) : null,
      h('button', {
        className: 'runs-clear-btn',
        onClick: function() { onSelectRun(null); },
      }, '✕ Clear path'),
    ]) : null,
    h('div', { className: 'runs-list' },
      runs.length === 0 && !loading
        ? h('div', { className: 'runs-empty' }, 'No runs found')
        : runs.map(function(r) {
            var isSelected = r.run_id === selectedRunId;
            var statusClass = 'status-' + (r.status || 'unknown');
            return h('button', {
              key: r.run_id,
              className: 'run-item' + (isSelected ? ' selected' : '') + ' ' + statusClass,
              onClick: function() { onSelectRun(r.run_id); },
            }, [
              h('div', { className: 'run-item-header' }, [
                h('span', { className: 'run-status-badge ' + statusClass }, r.status || '?'),
                h('span', { className: 'run-id mono' }, (r.run_id || '').substring(0, 20)),
              ]),
              h('div', { className: 'run-item-meta' }, [
                r.started_at ? h('span', null, _formatTime(r.started_at)) : null,
                r.duration_ms != null ? h('span', null, ' • ' + _formatDuration(r.duration_ms)) : null,
                r.trigger_source ? h('span', null, ' • ' + r.trigger_source) : null,
              ]),
            ]);
          })
    ),
  ]);
};

function _formatTime(ts) {
  try {
    var d = new Date(ts);
    var now = Date.now();
    var ago = (now - d.getTime()) / 1000;
    if (ago < 60) return Math.round(ago) + 's ago';
    if (ago < 3600) return Math.round(ago / 60) + 'm ago';
    if (ago < 86400) return Math.round(ago / 3600) + 'h ago';
    return d.toLocaleDateString();
  } catch (err) {
    return String(ts);
  }
}

function _formatDuration(ms) {
  if (ms < 1000) return ms + 'ms';
  if (ms < 60000) return (ms / 1000).toFixed(1) + 's';
  return Math.round(ms / 60000) + 'm';
}
