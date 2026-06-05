// Detail panel: shows full info for the selected node.
// ID, kind, rules (when/then), connected edges, upstream/downstream.

const { h } = preact;
const { useMemo } = preactHooks;

function Section({ title, children, defaultOpen }) {
  const [open, setOpen] = preactHooks.useState(defaultOpen !== false);
  return h('div', { className: 'detail-section' }, [
    h('div', {
      className: 'detail-section-header',
      onClick: function() { setOpen(!open); },
    }, [
      h('span', { className: 'section-chevron' }, open ? '▾' : '▸'),
      h('span', null, title),
    ]),
    open ? h('div', { className: 'detail-section-body' }, children) : null,
  ]);
}

function KV({ label, value, mono }) {
  return h('div', { className: 'detail-kv' }, [
    h('span', { className: 'detail-kv-label' }, label),
    h('span', { className: mono ? 'detail-kv-value mono' : 'detail-kv-value' }, value || '—'),
  ]);
}

function RuleBlock({ rule }) {
  return h('div', { className: 'rule-block' }, [
    h('div', { className: 'rule-id' }, rule.id),
    h('div', { className: 'rule-when mono' }, rule.when || '(always)'),
    h('div', { className: 'rule-actions' },
      (rule.actions || []).map(function(a, i) {
        var desc = a.kind;
        if (a.kind === 'goto') desc = '→ ' + a.target;
        if (a.kind === 'halt') desc = '⛔ halt: ' + (a.reason || '');
        if (a.kind === 'parallel') desc = '⑂ parallel → [' + (a.targets || []).join(', ') + '] join=' + (a.join || '');
        if (a.kind === 'interrupt') desc = '✋ interrupt: ' + (a.prompt || '');
        if (a.kind === 'retry') desc = '↻ retry → ' + a.target;
        if (a.kind === 'assert') desc = '✓ assert ' + a.fact;
        return h('div', { key: i, className: 'rule-action' }, desc);
      })
    ),
  ]);
}

window.DetailPanel = function DetailPanel({ topology, selectedNodeId, onSelectNode }) {
  if (!topology || !selectedNodeId) {
    return h('div', { className: 'detail-panel empty' }, [
      h('div', { className: 'detail-empty-text' }, 'Select a node to inspect'),
    ]);
  }

  var node = topology.nodes.find(function(n) { return n.id === selectedNodeId; });
  if (!node) {
    return h('div', { className: 'detail-panel empty' }, [
      h('div', { className: 'detail-empty-text' }, 'Node not found: ' + selectedNodeId),
    ]);
  }

  var kindInfo = window.getNodeKindInfo(node.kind);
  var customParsed = window.parseCustomKind(node.kind);

  var inbound = useMemo(function() {
    return topology.edges.filter(function(e) { return e.target === node.id; });
  }, [topology, node.id]);

  var outbound = useMemo(function() {
    return topology.edges.filter(function(e) { return e.source === node.id; });
  }, [topology, node.id]);

  var upstream = useMemo(function() {
    return [...new Set(inbound.map(function(e) { return e.source; }))];
  }, [inbound]);

  var downstream = useMemo(function() {
    return [...new Set(outbound.map(function(e) { return e.target; }))]
      .filter(function(id) { return id !== '__halt__' && id !== '__interrupt__'; });
  }, [outbound]);

  var phase = useMemo(function() {
    var phases = window.fallbackPhases(topology);
    for (var p of phases) {
      if (p.node_ids.indexOf(node.id) >= 0) return p.label;
    }
    return null;
  }, [topology, node.id]);

  var halts = outbound.filter(function(e) { return e.target === '__halt__'; });
  var interrupts = outbound.filter(function(e) { return e.target === '__interrupt__'; });

  return h('div', { className: 'detail-panel' }, [
    h('div', { className: 'detail-header' }, [
      h('div', { className: 'detail-kind-badge', style: { backgroundColor: kindInfo.color + '22', borderColor: kindInfo.color, color: kindInfo.color } }, [
        h('span', null, kindInfo.icon || '⚙️'),
        h('span', null, ' ' + (customParsed ? customParsed.className : kindInfo.label)),
      ]),
      h('h2', { className: 'detail-title' }, node.id),
      node.description ? h('div', { className: 'detail-description' }, node.description) : null,
      phase ? h('div', { className: 'detail-phase' }, phase) : null,
    ]),

    h(Section, { title: 'Identity', defaultOpen: true }, [
      h(KV, { label: 'ID', value: node.id, mono: true }),
      h(KV, { label: 'Kind', value: node.kind, mono: true }),
      customParsed ? h(KV, { label: 'Module', value: customParsed.module, mono: true }) : null,
      customParsed ? h(KV, { label: 'Class', value: customParsed.className, mono: true }) : null,
      node.spec ? h(KV, { label: 'Spec', value: node.spec, mono: true }) : null,
    ]),

    (node.rules && node.rules.length > 0) ? h(Section, { title: 'Routing Rules (' + node.rules.length + ')', defaultOpen: true },
      node.rules.map(function(r, i) {
        return h(RuleBlock, { key: i, rule: r });
      })
    ) : null,

    halts.length > 0 ? h(Section, { title: 'Halt Paths (' + halts.length + ')' },
      halts.map(function(e, i) {
        return h('div', { key: i, className: 'halt-entry' }, [
          h('span', { className: 'halt-icon' }, '⛔'),
          h('span', { className: 'mono' }, e.reason || e.when || '(unconditional)'),
        ]);
      })
    ) : null,

    interrupts.length > 0 ? h(Section, { title: 'HITL Interrupts (' + interrupts.length + ')' },
      interrupts.map(function(e, i) {
        return h('div', { key: i, className: 'interrupt-entry' }, [
          h('span', { className: 'interrupt-icon' }, '✋'),
          h('span', { className: 'mono' }, e.prompt || e.when || '(interrupt)'),
        ]);
      })
    ) : null,

    h(Section, { title: 'Connections' }, [
      h('div', { className: 'connections-group' }, [
        h('div', { className: 'connections-label' }, 'Upstream (' + upstream.length + ')'),
        upstream.length > 0
          ? h('div', { className: 'connections-list' },
              upstream.map(function(id) {
                return h('button', {
                  key: id,
                  className: 'connection-chip',
                  onClick: function() { onSelectNode(id); },
                }, id);
              })
            )
          : h('div', { className: 'connections-none' }, '(entry point)'),
      ]),
      h('div', { className: 'connections-group' }, [
        h('div', { className: 'connections-label' }, 'Downstream (' + downstream.length + ')'),
        downstream.length > 0
          ? h('div', { className: 'connections-list' },
              downstream.map(function(id) {
                return h('button', {
                  key: id,
                  className: 'connection-chip',
                  onClick: function() { onSelectNode(id); },
                }, id);
              })
            )
          : h('div', { className: 'connections-none' }, '(terminal)'),
      ]),
    ]),

    (node.config && Object.keys(node.config).length > 0) ? h(Section, { title: 'Config', defaultOpen: false }, [
      h('pre', { className: 'config-pre' }, JSON.stringify(node.config, null, 2)),
    ]) : null,

    h(Section, { title: 'Raw Edges', defaultOpen: false }, [
      h('div', { className: 'raw-edges' }, [
        h('div', { className: 'raw-edges-label' }, 'Inbound:'),
        inbound.map(function(e, i) {
          return h('div', { key: 'in-' + i, className: 'raw-edge mono' },
            e.source + ' → (this) via ' + e.via_rule + ' [' + e.kind + ']'
          );
        }),
        h('div', { className: 'raw-edges-label', style: { marginTop: 8 } }, 'Outbound:'),
        outbound.map(function(e, i) {
          return h('div', { key: 'out-' + i, className: 'raw-edge mono' },
            '(this) → ' + e.target + ' via ' + e.via_rule + ' [' + e.kind + ']'
          );
        }),
      ]),
    ]),
  ]);
};
