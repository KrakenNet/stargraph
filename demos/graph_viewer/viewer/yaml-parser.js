// Client-side stargraph.yaml to topology JSON parser.
// Used when user uploads a file directly (no serve backend).
// Mirrors the server-side _topology_for logic.

window.parseStargraphYaml = function(rawText) {
  const doc = jsyaml.load(rawText);
  if (!doc || !doc.nodes) {
    throw new Error('Invalid stargraph.yaml: missing "nodes" section');
  }

  const nodes = (doc.nodes || []).map(function(n) {
    return {
      id: n.id,
      kind: n.kind || 'passthrough',
      spec: n.spec || null,
      config: n.config || {},
      rules: [],
    };
  });

  const nodeIdRe = /\(node-id\s*\(id\s+([A-Za-z0-9_\-:.]+)\s*\)\s*\)/;
  const edges = [];
  const rulesBySource = {};

  for (const rule of (doc.rules || [])) {
    const srcMatch = nodeIdRe.exec(rule.when || '');
    const source = rule.node_id || (srcMatch ? srcMatch[1] : null);

    if (source) {
      if (!rulesBySource[source]) rulesBySource[source] = [];
      rulesBySource[source].push({
        id: rule.id,
        when: rule.when || '',
        actions: rule.then || [],
      });
    }

    for (const action of (rule.then || [])) {
      const kind = action.kind;
      if (kind === 'goto' || kind === 'retry') {
        if (source && action.target) {
          edges.push({
            source: source, target: action.target,
            via_rule: rule.id, kind: kind, when: rule.when || '',
          });
        }
      } else if (kind === 'parallel') {
        for (const tgt of (action.targets || [])) {
          if (source) {
            edges.push({
              source: source, target: tgt,
              via_rule: rule.id, kind: 'parallel', when: rule.when || '',
            });
          }
        }
        if (action.join) {
          for (const tgt of (action.targets || [])) {
            edges.push({
              source: tgt, target: action.join,
              via_rule: rule.id, kind: 'parallel_join', when: rule.when || '',
            });
          }
        }
      } else if (kind === 'halt') {
        if (source) {
          edges.push({
            source: source, target: '__halt__',
            via_rule: rule.id, kind: 'halt', when: rule.when || '',
            reason: action.reason || '',
          });
        }
      } else if (kind === 'interrupt') {
        if (source) {
          edges.push({
            source: source, target: '__interrupt__',
            via_rule: rule.id, kind: 'interrupt', when: rule.when || '',
            prompt: action.prompt || '',
          });
        }
      }
    }
  }

  for (const n of nodes) {
    n.rules = rulesBySource[n.id] || [];
  }

  const phases = _detectPhases(rawText);

  return {
    graph_id: doc.id || 'unknown',
    ir_version: doc.ir_version || '1.0.0',
    graph_hash: '',
    state_class: doc.state_class || null,
    nodes: nodes,
    edges: edges,
    tools: (doc.tools || []).map(function(t) { return { id: t.id, version: t.version || null }; }),
    governance: (doc.governance || []).map(function(g) {
      return { id: g.id, version: g.version || null, requires: g.requires || null };
    }),
    stores: (doc.stores || []).map(function(s) { return { name: s.name, provider: s.provider }; }),
    skills: (doc.skills || []).map(function(s) { return { id: s.id, version: s.version || null }; }),
    phases: phases,
    parallel: (doc.parallel || []).map(function(p) {
      return { targets: p.targets, join: p.join || '', strategy: p.strategy || 'all' };
    }),
  };
};

function _detectPhases(rawText) {
  const phaseRe = /^#\s*-{3,}\s*(.+?)\s*-{3,}\s*$/;
  const idRe = /^-\s*id:\s*(\S+)/;
  const phases = [];
  var inNodes = false;
  var current = null;

  for (const line of rawText.split('\n')) {
    const trimmed = line.trim();
    if (trimmed === 'nodes:') { inNodes = true; continue; }
    if (inNodes && trimmed && !trimmed.startsWith('#') && !trimmed.startsWith('-')
        && !trimmed.startsWith(' ') && !trimmed.startsWith('id:')
        && !trimmed.startsWith('kind') && !trimmed.startsWith('spec')
        && !trimmed.startsWith('config')) {
      inNodes = false;
      continue;
    }
    if (!inNodes) continue;

    const pm = phaseRe.exec(trimmed);
    if (pm) {
      current = { label: pm[1].trim(), node_ids: [] };
      phases.push(current);
      continue;
    }

    const im = idRe.exec(trimmed);
    if (im && current) {
      current.node_ids.push(im[1]);
    }
  }

  return phases;
}

window.fallbackPhases = function(topology) {
  if (topology.phases && topology.phases.length > 0) return topology.phases;

  var nodeIds = new Set(topology.nodes.map(function(n) { return n.id; }));
  var hasIncoming = new Set();
  var outgoing = {};

  for (const e of topology.edges) {
    if (e.target !== '__halt__' && e.target !== '__interrupt__') {
      hasIncoming.add(e.target);
    }
    if (!outgoing[e.source]) outgoing[e.source] = [];
    outgoing[e.source].push(e.target);
  }

  var roots = topology.nodes.filter(function(n) { return !hasIncoming.has(n.id); });
  if (roots.length === 0) {
    return [{ label: 'All Nodes', node_ids: topology.nodes.map(function(n) { return n.id; }) }];
  }

  var layers = [];
  var visited = new Set();
  var frontier = roots.map(function(n) { return n.id; });

  while (frontier.length > 0) {
    var layerIds = frontier.filter(function(id) { return !visited.has(id); });
    if (layerIds.length === 0) break;
    layers.push({ label: 'Layer ' + (layers.length + 1), node_ids: layerIds });
    for (const id of layerIds) visited.add(id);
    var next = new Set();
    for (const id of layerIds) {
      for (const tgt of (outgoing[id] || [])) {
        if (!visited.has(tgt) && nodeIds.has(tgt)) next.add(tgt);
      }
    }
    frontier = Array.from(next);
  }

  var unvisited = topology.nodes.filter(function(n) { return !visited.has(n.id); })
    .map(function(n) { return n.id; });
  if (unvisited.length > 0) {
    layers.push({ label: 'Ungrouped', node_ids: unvisited });
  }

  return layers;
};
