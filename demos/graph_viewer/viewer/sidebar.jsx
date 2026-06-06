// Left sidebar: graph summary, tools, governance, stores, phase legend, filters.
// Click-to-highlight: clicking a tool/governance pack highlights nodes that use it.

const { h } = preact;
const { useState, useMemo } = preactHooks;

function SidebarSection({ title, count, children, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen !== false);
  return h('div', { className: 'sidebar-section' }, [
    h('div', {
      className: 'sidebar-section-header',
      onClick: function() { setOpen(!open); },
    }, [
      h('span', { className: 'section-chevron' }, open ? '▾' : '▸'),
      h('span', null, title),
      count != null ? h('span', { className: 'sidebar-count' }, count) : null,
    ]),
    open ? h('div', { className: 'sidebar-section-body' }, children) : null,
  ]);
}

function KindLegend({ topology }) {
  var kindCounts = useMemo(function() {
    var counts = {};
    for (const n of topology.nodes) {
      var key = window.getNodeKindKey(n.kind);
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  }, [topology]);

  var entries = Object.entries(kindCounts).sort(function(a, b) { return b[1] - a[1]; });

  return h('div', { className: 'kind-legend' },
    entries.map(function(entry) {
      var key = entry[0];
      var count = entry[1];
      var info = window.THEME.nodeKinds[key] || window.THEME.nodeKinds._custom;
      return h('div', { key: key, className: 'kind-legend-item' }, [
        h('span', { className: 'kind-dot', style: { backgroundColor: info.color } }),
        h('span', { className: 'kind-label' }, info.label),
        h('span', { className: 'kind-count' }, count),
      ]);
    })
  );
}

window.Sidebar = function Sidebar({ topology, onHighlightNodes, showPhases, onTogglePhases, filterKind, onFilterKind, searchQuery, onSearchQuery, baseUrl, selectedRunId, onSelectRun, runPath }) {
  if (!topology) {
    return h('div', { className: 'sidebar' }, [
      h('div', { className: 'sidebar-empty' }, 'No graph loaded'),
    ]);
  }

  var phases = useMemo(function() {
    return window.fallbackPhases(topology);
  }, [topology]);

  var toolNodeMap = useMemo(function() {
    var map = {};
    for (const n of topology.nodes) {
      var custom = window.parseCustomKind(n.kind);
      if (custom) {
        var parts = custom.module.split('.');
        for (const tool of topology.tools) {
          var toolNs = tool.id.split('.')[0];
          if (parts.indexOf(toolNs) >= 0) {
            if (!map[tool.id]) map[tool.id] = [];
            map[tool.id].push(n.id);
          }
        }
      }
      var kindKey = window.getNodeKindKey(n.kind);
      if (kindKey === 'broker') {
        var brokerTool = topology.tools.find(function(t) { return t.id.indexOf('broker') >= 0; });
        if (brokerTool) {
          if (!map[brokerTool.id]) map[brokerTool.id] = [];
          map[brokerTool.id].push(n.id);
        }
      }
      if (kindKey === 'tool') {
        for (const tool of topology.tools) {
          if (n.id.indexOf(tool.id.split('.').pop()) >= 0) {
            if (!map[tool.id]) map[tool.id] = [];
            map[tool.id].push(n.id);
          }
        }
      }
    }
    return map;
  }, [topology]);

  var edgeStats = useMemo(function() {
    var stats = { goto: 0, parallel: 0, parallel_join: 0, halt: 0, interrupt: 0, retry: 0 };
    for (const e of topology.edges) {
      stats[e.kind] = (stats[e.kind] || 0) + 1;
    }
    return stats;
  }, [topology]);

  return h('div', { className: 'sidebar' }, [
    h('div', { className: 'sidebar-header' }, [
      h('h1', { className: 'sidebar-title' }, 'Stargraph Graph'),
      h('div', { className: 'sidebar-graph-id mono' }, topology.graph_id),
    ]),

    h(SidebarSection, { title: 'Runs', defaultOpen: !!selectedRunId }, [
      h(window.RunTrackerPanel, {
        baseUrl: baseUrl,
        selectedRunId: selectedRunId,
        onSelectRun: onSelectRun,
        runPath: runPath,
      }),
    ]),

    h(SidebarSection, { title: 'Summary', defaultOpen: true }, [
      h('div', { className: 'summary-grid' }, [
        h('div', { className: 'summary-stat' }, [
          h('div', { className: 'stat-value' }, topology.nodes.length),
          h('div', { className: 'stat-label' }, 'Nodes'),
        ]),
        h('div', { className: 'summary-stat' }, [
          h('div', { className: 'stat-value' }, topology.edges.length),
          h('div', { className: 'stat-label' }, 'Edges'),
        ]),
        h('div', { className: 'summary-stat' }, [
          h('div', { className: 'stat-value' }, topology.tools.length),
          h('div', { className: 'stat-label' }, 'Tools'),
        ]),
        h('div', { className: 'summary-stat' }, [
          h('div', { className: 'stat-value' }, topology.governance.length),
          h('div', { className: 'stat-label' }, 'Packs'),
        ]),
      ]),
      topology.ir_version ? h('div', { className: 'detail-kv' }, [
        h('span', { className: 'detail-kv-label' }, 'IR Version'),
        h('span', { className: 'detail-kv-value mono' }, topology.ir_version),
      ]) : null,
      topology.state_class ? h('div', { className: 'detail-kv' }, [
        h('span', { className: 'detail-kv-label' }, 'State Class'),
        h('span', { className: 'detail-kv-value mono' }, topology.state_class),
      ]) : null,
      topology.graph_hash ? h('div', { className: 'detail-kv' }, [
        h('span', { className: 'detail-kv-label' }, 'Hash'),
        h('span', { className: 'detail-kv-value mono' }, topology.graph_hash.substring(0, 16) + '…'),
      ]) : null,
    ]),

    h(SidebarSection, { title: 'Search & Filter', defaultOpen: true }, [
      h('input', {
        type: 'text',
        className: 'search-input',
        placeholder: 'Search nodes…',
        value: searchQuery || '',
        onInput: function(e) { onSearchQuery(e.target.value); },
      }),
      h('div', { className: 'filter-row' }, [
        h('label', { className: 'filter-label' }, 'Kind:'),
        h('select', {
          className: 'filter-select',
          value: filterKind || '',
          onChange: function(e) { onFilterKind(e.target.value || null); },
        }, [
          h('option', { value: '' }, 'All'),
          Object.entries(window.THEME.nodeKinds)
            .filter(function(entry) { return entry[0] !== '__halt__' && entry[0] !== '__interrupt__'; })
            .map(function(entry) {
              return h('option', { key: entry[0], value: entry[0] }, entry[1].label);
            }),
        ]),
      ]),
      h('div', { className: 'filter-row' }, [
        h('label', { className: 'checkbox-label' }, [
          h('input', {
            type: 'checkbox',
            checked: showPhases,
            onChange: function() { onTogglePhases(!showPhases); },
          }),
          h('span', null, ' Show phase groups'),
        ]),
      ]),
    ]),

    h(SidebarSection, { title: 'Node Kinds', defaultOpen: true }, [
      h(KindLegend, { topology: topology }),
    ]),

    h(SidebarSection, { title: 'Edge Types' }, [
      h('div', { className: 'edge-stats' },
        Object.entries(edgeStats).filter(function(e) { return e[1] > 0; }).map(function(entry) {
          var kind = entry[0];
          var count = entry[1];
          var style = window.THEME.edgeKinds[kind] || {};
          return h('div', { key: kind, className: 'edge-stat-row' }, [
            h('span', { className: 'edge-stat-line', style: {
              backgroundColor: style.color || '#666',
              borderStyle: style.style === 'dashed' ? 'dashed' : style.style === 'dotted' ? 'dotted' : 'solid',
            } }),
            h('span', { className: 'edge-stat-label' }, kind.replace('_', ' ')),
            h('span', { className: 'edge-stat-count' }, count),
          ]);
        })
      ),
    ]),

    topology.tools.length > 0 ? h(SidebarSection, { title: 'Tools', count: topology.tools.length }, [
      h('div', { className: 'entity-list' },
        topology.tools.map(function(t) {
          var connectedNodes = toolNodeMap[t.id] || [];
          return h('button', {
            key: t.id,
            className: 'entity-item' + (connectedNodes.length > 0 ? ' clickable' : ''),
            onClick: function() {
              if (connectedNodes.length > 0) onHighlightNodes(connectedNodes);
            },
          }, [
            h('span', { className: 'entity-icon' }, '🔧'),
            h('span', { className: 'entity-id mono' }, t.id),
            t.version ? h('span', { className: 'entity-version' }, 'v' + t.version) : null,
            connectedNodes.length > 0
              ? h('span', { className: 'entity-refs' }, connectedNodes.length + ' nodes')
              : null,
          ]);
        })
      ),
    ]) : null,

    topology.governance.length > 0 ? h(SidebarSection, { title: 'Governance Packs', count: topology.governance.length }, [
      h('div', { className: 'entity-list' },
        topology.governance.map(function(g) {
          return h('div', { key: g.id, className: 'entity-item' }, [
            h('span', { className: 'entity-icon' }, '🛡️'),
            h('span', { className: 'entity-id mono' }, g.id),
            g.version ? h('span', { className: 'entity-version' }, 'v' + g.version) : null,
            g.requires ? h('div', { className: 'entity-meta' },
              'facts=' + (g.requires.stargraph_facts_version || '?') +
              ' api=' + (g.requires.api_version || '?')
            ) : null,
          ]);
        })
      ),
    ]) : null,

    topology.stores.length > 0 ? h(SidebarSection, { title: 'Stores', count: topology.stores.length }, [
      h('div', { className: 'entity-list' },
        topology.stores.map(function(s) {
          return h('div', { key: s.name, className: 'entity-item' }, [
            h('span', { className: 'entity-icon' }, '🗄️'),
            h('span', { className: 'entity-id mono' }, s.name),
            h('span', { className: 'entity-meta' }, s.provider),
          ]);
        })
      ),
    ]) : null,

    phases.length > 0 ? h(SidebarSection, { title: 'Phases', count: phases.length }, [
      h('div', { className: 'phase-list' },
        phases.map(function(p, i) {
          var color = window.THEME.phaseColors[i % window.THEME.phaseColors.length];
          return h('button', {
            key: i,
            className: 'phase-item',
            onClick: function() { onHighlightNodes(p.node_ids); },
          }, [
            h('span', { className: 'phase-dot', style: { backgroundColor: color } }),
            h('span', { className: 'phase-label' }, p.label),
            h('span', { className: 'phase-count' }, p.node_ids.length + ' nodes'),
          ]);
        })
      ),
    ]) : null,
  ]);
};
