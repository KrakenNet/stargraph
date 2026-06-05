// Cytoscape.js graph renderer component.
// L→R DAG layout with kind-based coloring, hover tooltips,
// layout preset buttons, and edge styling by action kind.

const { h } = preact;
const { useEffect, useRef, useCallback, useState } = preactHooks;

var LAYOUT_PRESETS = {
  lr: { name: 'dagre', rankDir: 'LR', nodeSep: 60, rankSep: 100, edgeSep: 25, padding: 60, spacingFactor: 1.3, animate: true, animationDuration: 400, fit: false },
  tb: { name: 'dagre', rankDir: 'TB', nodeSep: 50, rankSep: 80, edgeSep: 20, padding: 60, spacingFactor: 1.2, animate: true, animationDuration: 400, fit: false },
  force: { name: 'cose', idealEdgeLength: 120, nodeOverlap: 30, padding: 60, animate: true, animationDuration: 600, fit: true, randomize: false, gravity: 0.3 },
  // ELK layered: Sugiyama-style with multi-pass layer-sweep crossing minimization.
  // Stronger than dagre for tangled graphs but slower.
  elk: {
    name: 'elk',
    fit: false,
    padding: 60,
    animate: true,
    animationDuration: 500,
    elk: {
      'algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.spacing.nodeNode': 55,
      'elk.layered.spacing.nodeNodeBetweenLayers': 100,
      'elk.layered.spacing.edgeNodeBetweenLayers': 25,
      'elk.layered.spacing.edgeEdgeBetweenLayers': 15,
      'elk.layered.crossingMinimization.strategy': 'LAYER_SWEEP',
      'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
      'elk.layered.cycleBreaking.strategy': 'GREEDY',
      'elk.layered.thoroughness': 30,
      'elk.layered.layering.strategy': 'NETWORK_SIMPLEX',
      'elk.edgeRouting': 'POLYLINE',
      'elk.layered.mergeEdges': false,
    },
  },
};

function LayoutButtons({ onLayout, activeLayout }) {
  return h('div', { className: 'layout-buttons' }, [
    h('button', {
      className: 'layout-btn' + (activeLayout === 'lr' ? ' active' : ''),
      onClick: function() { onLayout('lr'); },
      title: 'Left to Right DAG',
    }, '→ L-R'),
    h('button', {
      className: 'layout-btn' + (activeLayout === 'tb' ? ' active' : ''),
      onClick: function() { onLayout('tb'); },
      title: 'Top to Bottom DAG',
    }, '↓ T-B'),
    h('button', {
      className: 'layout-btn' + (activeLayout === 'force' ? ' active' : ''),
      onClick: function() { onLayout('force'); },
      title: 'Force-directed layout',
    }, '◎ Force'),
    h('button', {
      className: 'layout-btn' + (activeLayout === 'elk' ? ' active' : ''),
      onClick: function() { onLayout('elk'); },
      title: 'ELK layered — minimizes edge crossings (slower)',
    }, '✱ Min-X'),
    h('button', {
      className: 'layout-btn',
      onClick: function() { onLayout('fit'); },
      title: 'Zoom to fit all nodes',
    }, '⊞ Fit'),
  ]);
}

function buildTooltipDom(tipEl, data) {
  while (tipEl.firstChild) tipEl.removeChild(tipEl.firstChild);

  function addLine(cls, text) {
    var div = document.createElement('div');
    div.className = cls;
    div.textContent = text;
    tipEl.appendChild(div);
  }

  if (data.isEdge) {
    addLine('tip-rule', data.viaRule);
    if (data.when) addLine('tip-when', data.when);
    addLine('tip-kind', data.edgeKind);
  } else {
    addLine('tip-id', data.id);
    addLine('tip-kind', data.kindIcon + ' ' + data.kindLabel);
    if (data.className) addLine('tip-class', data.className);
    if (data.description) addLine('tip-desc', data.description);
    if (data.ruleCount > 0) addLine('tip-rules', data.ruleCount + ' routing rule' + (data.ruleCount > 1 ? 's' : ''));
  }
}

window.GraphCanvas = function GraphCanvas({ topology, selectedNode, onSelectNode, highlightNodes, showPhases, runPath, runStatus }) {
  const containerRef = useRef(null);
  const cyRef = useRef(null);
  const tooltipRef = useRef(null);
  const [layoutDone, setLayoutDone] = useState(false);
  const [activeLayout, setActiveLayout] = useState('lr');

  var buildElements = useCallback(function() {
    if (!topology) return [];

    var elements = [];
    var nodeIdSet = new Set(topology.nodes.map(function(n) { return n.id; }));

    for (const node of topology.nodes) {
      var kindKey = window.getNodeKindKey(node.kind);
      var kindInfo = window.getNodeKindInfo(node.kind);
      var customParsed = window.parseCustomKind(node.kind);

      var displayLabel = node.id;
      if (displayLabel.length > 20) displayLabel = displayLabel.substring(0, 18) + '…';

      elements.push({
        group: 'nodes',
        data: {
          id: node.id,
          label: displayLabel,
          kindKey: kindKey,
          kindLabel: customParsed ? customParsed.className : kindInfo.label,
          kindColor: kindInfo.color,
          kindIcon: kindInfo.icon || '',
          fullKind: node.kind,
          className: customParsed ? customParsed.className : '',
          description: node.description || '',
          ruleCount: (node.rules || []).length,
        },
        classes: kindKey,
      });
    }

    for (const edge of topology.edges) {
      if (edge.target === '__halt__') continue;
      if (edge.target === '__interrupt__') continue;
      if (!nodeIdSet.has(edge.source) || !nodeIdSet.has(edge.target)) continue;

      var edgeStyle = window.THEME.edgeKinds[edge.kind] || window.THEME.edgeKinds.goto;
      var edgeId = edge.source + '->' + edge.target + ':' + edge.via_rule;
      elements.push({
        group: 'edges',
        data: {
          id: edgeId,
          source: edge.source,
          target: edge.target,
          edgeKind: edge.kind,
          viaRule: edge.via_rule,
          when: edge.when || '',
          lineColor: edgeStyle.color,
          lineStyle: edgeStyle.style,
          lineWidth: edgeStyle.width,
        },
        classes: 'edge-' + edge.kind,
      });
    }

    return elements;
  }, [topology]);

  function showTooltip(evt) {
    var el = evt.target;
    var tip = tooltipRef.current;
    if (!tip) return;

    var pos = evt.renderedPosition || el.renderedPosition();
    var data;

    if (el.isEdge()) {
      data = { isEdge: true, viaRule: el.data('viaRule'), when: el.data('when'), edgeKind: el.data('edgeKind') };
    } else {
      data = {
        isEdge: false,
        id: el.data('id'),
        kindIcon: el.data('kindIcon'),
        kindLabel: el.data('kindLabel'),
        className: el.data('className'),
        description: el.data('description'),
        ruleCount: el.data('ruleCount'),
      };
    }

    buildTooltipDom(tip, data);
    tip.style.display = 'block';
    tip.style.left = (pos.x + 15) + 'px';
    tip.style.top = (pos.y - 10) + 'px';

    var rect = tip.getBoundingClientRect();
    var container = containerRef.current.getBoundingClientRect();
    if (rect.right > container.right - 10) {
      tip.style.left = (pos.x - rect.width - 15) + 'px';
    }
    if (rect.bottom > container.bottom - 10) {
      tip.style.top = (pos.y - rect.height - 10) + 'px';
    }
  }

  function hideTooltip() {
    var tip = tooltipRef.current;
    if (tip) tip.style.display = 'none';
  }

  function runLayout(preset) {
    var cy = cyRef.current;
    if (!cy) return;

    if (preset === 'fit') {
      cy.animate({ fit: { eles: cy.elements(), padding: 40 } }, { duration: 300 });
      return;
    }

    setActiveLayout(preset);
    var opts = Object.assign({}, LAYOUT_PRESETS[preset]);
    var layout = cy.elements().layout(opts);
    layout.run();

    if (!opts.fit) {
      setTimeout(function() {
        cy.fit(cy.elements(), 40);
        if (cy.zoom() < 0.3) {
          cy.zoom({ level: 0.3, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
          cy.center();
        }
      }, (opts.animationDuration || 400) + 50);
    }
  }

  useEffect(function() {
    if (!containerRef.current || !topology) return;

    var elements = buildElements();
    if (elements.length === 0) return;

    if (cyRef.current) {
      cyRef.current.destroy();
    }

    var cy = cytoscape({
      container: containerRef.current,
      elements: elements,
      style: [
        {
          selector: 'node',
          style: {
            'label': 'data(label)',
            'text-valign': 'bottom',
            'text-halign': 'center',
            'font-size': '16px',
            'font-family': '"JetBrains Mono", monospace',
            'color': window.THEME.fg0,
            'text-outline-color': window.THEME.bg1,
            'text-outline-width': 3,
            'background-color': 'data(kindColor)',
            'width': 44,
            'height': 44,
            'shape': 'roundrectangle',
            'border-width': 2,
            'border-color': 'data(kindColor)',
            'border-opacity': 0.7,
            'background-opacity': 0.25,
            'text-max-width': '180px',
            'text-wrap': 'ellipsis',
            'text-margin-y': 6,
          },
        },
        {
          selector: 'node:selected',
          style: {
            'border-width': 3,
            'border-color': '#3ddc97',
            'background-opacity': 0.5,
            'z-index': 999,
          },
        },
        {
          selector: 'node.highlighted',
          style: {
            'border-width': 3,
            'border-color': '#facc15',
            'background-opacity': 0.5,
          },
        },
        {
          selector: 'node.dimmed',
          style: { 'opacity': 0.15 },
        },
        {
          selector: 'node.interrupt',
          style: { 'shape': 'diamond', 'width': 50, 'height': 50 },
        },
        {
          selector: 'node.passthrough',
          style: { 'shape': 'ellipse', 'width': 32, 'height': 32, 'font-size': '13px' },
        },
        {
          selector: 'node.broker',
          style: { 'shape': 'hexagon' },
        },
        {
          selector: 'node.retrieval',
          style: { 'shape': 'barrel' },
        },
        {
          selector: 'edge',
          style: {
            'width': 'data(lineWidth)',
            'line-color': 'data(lineColor)',
            'line-style': 'data(lineStyle)',
            'target-arrow-color': 'data(lineColor)',
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'arrow-scale': 0.8,
            'opacity': 0.6,
          },
        },
        {
          selector: 'edge:selected',
          style: { 'width': 3, 'opacity': 1, 'z-index': 999 },
        },
        {
          selector: 'edge.dimmed',
          style: { 'opacity': 0.06 },
        },
        {
          selector: 'edge.highlighted',
          style: { 'opacity': 1, 'width': 3 },
        },
        {
          selector: 'node.run-visited',
          style: {
            'border-color': '#5fcf90',
            'border-width': 3,
            'background-color': '#5fcf90',
            'background-opacity': 0.35,
            'opacity': 1,
          },
        },
        {
          selector: 'node.run-current',
          style: {
            'border-color': '#3ddc97',
            'border-width': 4,
            'background-color': '#3ddc97',
            'background-opacity': 0.6,
            'shadow-blur': 30,
            'shadow-color': '#3ddc97',
            'shadow-opacity': 0.9,
            'shadow-offset-x': 0,
            'shadow-offset-y': 0,
            'z-index': 998,
          },
        },
        {
          selector: 'edge.run-traversed',
          style: {
            'line-color': '#5fcf90',
            'target-arrow-color': '#5fcf90',
            'width': 3,
            'opacity': 1,
            'line-style': 'solid',
          },
        },
      ],
      layout: { name: 'preset' },
      minZoom: 0.08,
      maxZoom: 5,
      wheelSensitivity: 0.25,
    });

    cy.on('tap', 'node', function(evt) {
      hideTooltip();
      onSelectNode(evt.target.data('id'));
    });

    cy.on('tap', function(evt) {
      if (evt.target === cy) {
        hideTooltip();
        onSelectNode(null);
      }
    });

    cy.on('mouseover', 'node, edge', showTooltip);
    cy.on('mouseout', 'node, edge', hideTooltip);

    cyRef.current = cy;
    window._cy = cy;  // debug hook
    window._cy = cy;

    var initLayout = cy.elements().layout(
      Object.assign({}, LAYOUT_PRESETS.lr, { animate: false })
    );
    initLayout.run();

    requestAnimationFrame(function() {
      cy.resize();
      cy.fit(cy.elements(), 40);
      if (cy.zoom() < 0.3) {
        cy.zoom({ level: 0.3, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
        cy.center();
      }
      setLayoutDone(true);
    });

    return function() {
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
    };
  }, [topology]);

  useEffect(function() {
    if (!cyRef.current || !layoutDone) return;
    var cy = cyRef.current;

    cy.batch(function() {
      cy.nodes().removeClass('run-visited run-current');
      cy.edges().removeClass('run-traversed');

      if (runPath && runPath.nodeOrder && runPath.nodeOrder.length > 0) {
        var visitedSet = new Set(runPath.nodeOrder);
        cy.nodes().forEach(function(n) {
          var id = n.data('id');
          if (visitedSet.has(id)) {
            n.addClass('run-visited');
            var step = runPath.nodeSteps[id];
            if (step != null) {
              var origLabel = n.data('label');
              if (!n.data('origLabel')) n.data('origLabel', origLabel);
              n.data('label', '[' + (step + 1) + '] ' + n.data('origLabel'));
            }
          } else {
            if (n.data('origLabel')) {
              n.data('label', n.data('origLabel'));
              n.data('origLabel', null);
            }
          }
        });
        if (runPath.current) {
          var curNode = cy.getElementById(runPath.current);
          if (curNode.length > 0) {
            curNode.removeClass('run-visited');
            curNode.addClass('run-current');
          }
        }
        // Mark edges traversed: any edge whose source and target are both in
        // visitedSet AND whose source step precedes target step.
        cy.edges().forEach(function(e) {
          var s = e.source().data('id');
          var t = e.target().data('id');
          if (visitedSet.has(s) && visitedSet.has(t)
              && runPath.nodeSteps[s] < runPath.nodeSteps[t]) {
            e.addClass('run-traversed');
          }
        });
      } else {
        // Clear all step badges if runPath cleared
        cy.nodes().forEach(function(n) {
          if (n.data('origLabel')) {
            n.data('label', n.data('origLabel'));
            n.data('origLabel', null);
          }
        });
      }
    });
  }, [runPath, layoutDone]);

  useEffect(function() {
    if (!cyRef.current || !layoutDone) return;
    var cy = cyRef.current;

    cy.batch(function() {
      cy.nodes().removeClass('highlighted dimmed');
      cy.edges().removeClass('highlighted dimmed');

      if (selectedNode) {
        var sel = cy.getElementById(selectedNode);
        if (sel.length > 0) {
          sel.select();
          var neighborhood = sel.neighborhood().add(sel);
          cy.elements().not(neighborhood).addClass('dimmed');
          neighborhood.addClass('highlighted');
        }
      } else {
        cy.nodes().unselect();
      }

      if (highlightNodes && highlightNodes.length > 0) {
        var hlSet = new Set(highlightNodes);
        cy.nodes().forEach(function(n) {
          if (hlSet.has(n.data('id'))) {
            n.addClass('highlighted');
            n.removeClass('dimmed');
          }
        });
      }
    });
  }, [selectedNode, highlightNodes, layoutDone]);

  return h('div', {
    style: { position: 'absolute', inset: 0 },
  }, [
    h('div', {
      ref: containerRef,
      style: {
        position: 'absolute',
        inset: 0,
        background: window.THEME.bg1,
      },
    }),
    h('div', {
      ref: tooltipRef,
      className: 'graph-tooltip',
      style: { display: 'none' },
    }),
    h(LayoutButtons, { onLayout: runLayout, activeLayout: activeLayout }),
  ]);
};
