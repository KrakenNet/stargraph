// Root component: ties together sidebar, graph canvas, and detail panel.
// Manages topology state, data loading (serve API or file upload), and selection.

const { h, render } = preact;
const { useState, useCallback, useEffect, useRef, useMemo } = preactHooks;

function DataSourceBar({ onLoadTopology, loading, error, graphList }) {
  const [mode, setMode] = useState('none');
  const [serverUrl, setServerUrl] = useState('');
  const fileRef = useRef(null);

  function handleFileUpload(e) {
    var file = e.target.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(ev) {
      try {
        var topology = window.parseStargraphYaml(ev.target.result);
        onLoadTopology(topology, 'file');
      } catch (err) {
        onLoadTopology(null, 'file', err.message);
      }
    };
    reader.readAsText(file);
  }

  function handleConnect() {
    if (!serverUrl) return;
    var url = serverUrl.replace(/\/$/, '');
    onLoadTopology(null, 'connecting');
    // Try /api/graph (graph-viewer native), then /watch/api/graph (run-watcher).
    var paths = ['/api/graph', '/watch/api/graph'];
    var attempt = 0;
    function tryNext() {
      if (attempt >= paths.length) {
        onLoadTopology(null, 'serve',
          'no topology endpoint found at ' + url + ' (tried ' + paths.join(', ') + ')');
        return;
      }
      fetch(url + paths[attempt++])
        .then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function(data) { onLoadTopology(data, 'serve'); })
        .catch(function(err) {
          if (attempt < paths.length) tryNext();
          else onLoadTopology(null, 'serve', err.message);
        });
    }
    tryNext();
  }

  function handleFileDrop(e) {
    e.preventDefault();
    e.stopPropagation();
    var file = e.dataTransfer.files[0];
    if (!file) return;
    var reader = new FileReader();
    reader.onload = function(ev) {
      try {
        var topology = window.parseStargraphYaml(ev.target.result);
        onLoadTopology(topology, 'file');
      } catch (err) {
        onLoadTopology(null, 'file', err.message);
      }
    };
    reader.readAsText(file);
  }

  function handleDragOver(e) {
    e.preventDefault();
    e.stopPropagation();
  }

  return h('div', { className: 'data-source-bar' }, [
    h('div', { className: 'source-actions' }, [
      h('button', {
        className: 'source-btn' + (mode === 'file' ? ' active' : ''),
        onClick: function() { setMode(mode === 'file' ? 'none' : 'file'); },
      }, '📁 Upload YAML'),
      h('button', {
        className: 'source-btn' + (mode === 'serve' ? ' active' : ''),
        onClick: function() { setMode(mode === 'serve' ? 'none' : 'serve'); },
      }, '🔗 Connect to Server'),
      graphList && graphList.length > 1 ? h('select', {
        className: 'graph-select',
        onChange: function(e) {
          if (e.target.value) {
            var url = serverUrl.replace(/\/$/, '');
            fetch(url + '/api/graph?graph_id=' + encodeURIComponent(e.target.value))
              .then(function(r) { return r.json(); })
              .then(function(data) { onLoadTopology(data, 'serve'); });
          }
        },
      }, graphList.map(function(g) {
        return h('option', { key: g.graph_id, value: g.graph_id }, g.graph_id + ' (' + g.node_count + ' nodes)');
      })) : null,
    ]),

    mode === 'file' ? h('div', {
      className: 'file-drop-zone',
      onDrop: handleFileDrop,
      onDragOver: handleDragOver,
    }, [
      h('input', {
        ref: fileRef,
        type: 'file',
        accept: '.yaml,.yml',
        onChange: handleFileUpload,
        style: { display: 'none' },
      }),
      h('div', { className: 'drop-text', onClick: function() { fileRef.current.click(); } },
        'Drop stargraph.yaml here or click to browse'),
    ]) : null,

    mode === 'serve' ? h('div', { className: 'serve-connect' }, [
      h('input', {
        type: 'text',
        className: 'serve-url-input',
        placeholder: 'http://localhost:9100',
        value: serverUrl,
        onInput: function(e) { setServerUrl(e.target.value); },
        onKeyDown: function(e) { if (e.key === 'Enter') handleConnect(); },
      }),
      h('button', {
        className: 'connect-btn',
        onClick: handleConnect,
        disabled: loading,
      }, loading ? 'Connecting…' : 'Connect'),
    ]) : null,

    error ? h('div', { className: 'source-error' }, '⚠ ' + error) : null,
  ]);
}

function App() {
  const [topology, setTopology] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);
  const [highlightNodes, setHighlightNodes] = useState(null);
  const [showPhases, setShowPhases] = useState(true);
  const [filterKind, setFilterKind] = useState(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [graphList, setGraphList] = useState(null);
  const [sourceUrl, setSourceUrl] = useState('');
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [runPath, setRunPath] = useState(null);
  const [runStatus, setRunStatus] = useState(null);
  const runSubRef = useRef(null);

  var handleLoadTopology = useCallback(function(data, source, err) {
    if (err) {
      setError(err);
      setLoading(false);
      return;
    }
    if (source === 'connecting') {
      setLoading(true);
      setError(null);
      return;
    }
    setTopology(data);
    setSelectedNode(null);
    setHighlightNodes(null);
    setLoading(false);
    setError(null);
  }, []);

  useEffect(function() {
    var params = new URLSearchParams(window.location.search);
    // Default to current host so graph-viewer's own /api/graph (pre-loaded
    // via --graph flag) auto-loads. ?serve=<url> overrides for cross-host use.
    var serveUrl = params.get('serve') || window.location.origin;
    setSourceUrl(serveUrl);

    var base = serveUrl.replace(/\/$/, '');
    fetch(base + '/api/graphs')
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(data) { if (data) setGraphList(data.graphs); })
      .catch(function() {});

    // Try /api/graph (graph-viewer native), then /watch/api/graph (run-watcher).
    var paths = ['/api/graph', '/watch/api/graph'];
    var attempt = 0;
    function tryNext() {
      if (attempt >= paths.length) {
        setError('Auto-connect failed: no topology endpoint at ' + base);
        return;
      }
      fetch(base + paths[attempt++])
        .then(function(r) {
          if (!r.ok) throw new Error('HTTP ' + r.status);
          return r.json();
        })
        .then(function(data) { setTopology(data); })
        .catch(function(err) {
          if (attempt < paths.length) tryNext();
          else setError('Auto-connect failed: ' + err.message);
        });
    }
    tryNext();
  }, []);

  // Load run path when selectedRunId changes; subscribe via WS if live.
  useEffect(function() {
    if (runSubRef.current) {
      runSubRef.current.stop();
      runSubRef.current = null;
    }
    if (!selectedRunId || !sourceUrl) {
      setRunPath(null);
      setRunStatus(null);
      return;
    }

    var cancelled = false;
    var base = sourceUrl;

    fetch(base.replace(/\/$/, '') + '/api/runs/' + encodeURIComponent(selectedRunId))
      .then(function(r) { return r.ok ? r.json() : null; })
      .then(function(runMeta) {
        if (cancelled) return;
        var status = runMeta ? (runMeta.status || 'unknown') : 'unknown';
        setRunStatus(status);

        // Prefer checkpoints (every step writes one). Fall back to events.
        var initialPathPromise = window.fetchRunCheckpoints(base, selectedRunId)
          .then(function(data) {
            var cps = data.checkpoints || [];
            if (cps.length > 0) {
              return { kind: 'checkpoints', rows: cps };
            }
            return null;
          })
          .catch(function() { return null; })
          .then(function(cpResult) {
            if (cpResult) return cpResult;
            return window.fetchRunEvents(base, selectedRunId)
              .then(function(data) { return { kind: 'events', rows: data.events || [] }; })
              .catch(function() { return { kind: 'events', rows: [] }; });
          });

        return initialPathPromise.then(function(result) {
          if (cancelled) return;
          var path = result.kind === 'checkpoints'
            ? window.computeRunPathFromCheckpoints(result.rows, status)
            : window.computeRunPath(result.rows, status);
          setRunPath(path);

          if (status === 'running' || status === 'paused') {
            // Live: subscribe via WS for incremental events on top of the
            // initial snapshot. Convert events into transitions for path growth.
            var events = result.kind === 'events' ? result.rows.slice() : [];
            var sub = new window.RunSubscription(base, selectedRunId,
              function(evt) {
                events.push(evt);
                // Recompute by merging the live events on top of the snapshot.
                var basePath = result.kind === 'checkpoints'
                  ? window.computeRunPathFromCheckpoints(result.rows, status)
                  : { nodeOrder: [], nodeSteps: {}, current: null, status: status };
                var evPath = window.computeRunPath(events, status);
                // Merge: append new nodes from evPath onto basePath.
                var merged = { nodeOrder: basePath.nodeOrder.slice(),
                               nodeSteps: Object.assign({}, basePath.nodeSteps),
                               current: null,
                               status: status };
                var seen = new Set(merged.nodeOrder);
                for (var i = 0; i < evPath.nodeOrder.length; i++) {
                  var nid = evPath.nodeOrder[i];
                  if (!seen.has(nid)) {
                    seen.add(nid);
                    merged.nodeSteps[nid] = merged.nodeOrder.length;
                    merged.nodeOrder.push(nid);
                  }
                }
                merged.current = merged.nodeOrder.length > 0
                  ? merged.nodeOrder[merged.nodeOrder.length - 1] : null;
                setRunPath(merged);
              },
              function(connStatus) {
                if (connStatus === 'closed') {
                  fetch(base.replace(/\/$/, '') + '/api/runs/' + encodeURIComponent(selectedRunId))
                    .then(function(r) { return r.ok ? r.json() : null; })
                    .then(function(meta) {
                      if (cancelled || !meta) return;
                      setRunStatus(meta.status);
                      // Reload checkpoints on close — they're the authoritative source.
                      window.fetchRunCheckpoints(base, selectedRunId)
                        .then(function(d) {
                          if (cancelled) return;
                          var finalPath = window.computeRunPathFromCheckpoints(
                            d.checkpoints || [], meta.status);
                          setRunPath(finalPath);
                        })
                        .catch(function() {});
                    });
                }
              }
            );
            sub.start();
            runSubRef.current = sub;
          }
        });
      })
      .catch(function(err) {
        if (!cancelled) console.error('run load error', err);
      });

    return function() {
      cancelled = true;
      if (runSubRef.current) {
        runSubRef.current.stop();
        runSubRef.current = null;
      }
    };
  }, [selectedRunId, sourceUrl]);

  var handleSelectNode = useCallback(function(nodeId) {
    setSelectedNode(nodeId);
    if (nodeId) setHighlightNodes(null);
  }, []);

  var handleHighlightNodes = useCallback(function(nodeIds) {
    setHighlightNodes(nodeIds);
    setSelectedNode(null);
  }, []);

  var filteredTopology = topology;
  if (topology && (filterKind || searchQuery)) {
    var matchingIds = new Set();
    for (const n of topology.nodes) {
      var kindKey = window.getNodeKindKey(n.kind);
      var kindMatch = !filterKind || kindKey === filterKind;
      var searchMatch = !searchQuery || n.id.toLowerCase().indexOf(searchQuery.toLowerCase()) >= 0
        || n.kind.toLowerCase().indexOf(searchQuery.toLowerCase()) >= 0;
      if (kindMatch && searchMatch) matchingIds.add(n.id);
    }
    if (matchingIds.size < topology.nodes.length && matchingIds.size > 0) {
      setHighlightNodes(Array.from(matchingIds));
    } else if (matchingIds.size === topology.nodes.length) {
      if (highlightNodes) setHighlightNodes(null);
    }
    filteredTopology = topology;
  }

  return h('div', { className: 'app-root' }, [
    h(DataSourceBar, {
      onLoadTopology: handleLoadTopology,
      loading: loading,
      error: error,
      graphList: graphList,
    }),
    h('div', { className: 'app-body' }, [
      h(window.Sidebar, {
        topology: topology,
        onHighlightNodes: handleHighlightNodes,
        showPhases: showPhases,
        onTogglePhases: setShowPhases,
        filterKind: filterKind,
        onFilterKind: setFilterKind,
        searchQuery: searchQuery,
        onSearchQuery: setSearchQuery,
        baseUrl: sourceUrl,
        selectedRunId: selectedRunId,
        onSelectRun: setSelectedRunId,
        runPath: runPath,
      }),
      h('div', { className: 'graph-area' }, [
        topology
          ? h(window.GraphCanvas, {
              topology: topology,
              selectedNode: selectedNode,
              onSelectNode: handleSelectNode,
              highlightNodes: highlightNodes,
              showPhases: showPhases,
              runPath: runPath,
              runStatus: runStatus,
            })
          : h('div', { className: 'graph-empty' }, [
              h('div', { className: 'graph-empty-icon' }, '🔍'),
              h('div', { className: 'graph-empty-text' }, 'Load a stargraph.yaml to explore'),
              h('div', { className: 'graph-empty-hint' }, 'Upload a file or connect to a running stargraph serve instance'),
            ]),
      ]),
      h(window.DetailPanel, {
        topology: topology,
        selectedNodeId: selectedNode,
        onSelectNode: handleSelectNode,
      }),
    ]),
  ]);
}

render(h(App), document.getElementById('app'));
