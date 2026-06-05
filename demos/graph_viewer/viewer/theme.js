// Color palette and styling constants for the graph viewer.
// Node kinds → colors, edge kinds → styles.

window.THEME = {
  bg0: '#0a0b0d',
  bg1: '#101216',
  bg2: '#15181d',
  bg3: '#1b1f25',
  bg3h: '#22272f',
  bg4: '#2a3038',
  fg0: '#f1f3f7',
  fg1: '#c8cdd5',
  fg2: '#8a92a0',
  fg3: '#5c6371',

  nodeKinds: {
    dspy:            { color: '#5b8def', label: 'DSPy Module',       icon: '🧠' },
    broker:          { color: '#a78bfa', label: 'Broker Call',        icon: '🔗' },
    tool:            { color: '#f59e42', label: 'Tool Call',          icon: '🔧' },
    passthrough:     { color: '#6b7280', label: 'Passthrough',       icon: '→'  },
    interrupt:       { color: '#ef6a6a', label: 'HITL Gate',         icon: '✋' },
    ml:              { color: '#22d3ee', label: 'ML Inference',      icon: '📊' },
    write_artifact:  { color: '#5fcf90', label: 'Write Artifact',   icon: '💾' },
    subgraph:        { color: '#14b8a6', label: 'Subgraph',         icon: '📦' },
    echo:            { color: '#facc15', label: 'Echo',              icon: '📢' },
    retrieval:       { color: '#818cf8', label: 'Retrieval',         icon: '🔍' },
    memory_write:    { color: '#f472b6', label: 'Memory Write',     icon: '📝' },
    _custom:         { color: '#60a5fa', label: 'Custom Node',      icon: '⚙️'  },
    __halt__:        { color: '#ef4444', label: 'Halt',             icon: '⛔' },
    __interrupt__:   { color: '#f59e42', label: 'Interrupt',        icon: '⏸️'  },
  },

  edgeKinds: {
    goto:           { color: 'rgba(255,255,255,0.25)', style: 'solid',  width: 1.5 },
    parallel:       { color: '#a78bfa',                style: 'dashed', width: 2   },
    parallel_join:  { color: '#a78bfa',                style: 'dotted', width: 1.5 },
    halt:           { color: '#ef4444',                style: 'solid',  width: 2   },
    interrupt:      { color: '#f59e42',                style: 'dashed', width: 2   },
    retry:          { color: '#facc15',                style: 'dashed', width: 1.5 },
  },

  phaseColors: [
    '#3ddc97', '#5b8def', '#a78bfa', '#f59e42',
    '#22d3ee', '#f472b6', '#facc15', '#ef6a6a',
  ],
};

// Infer semantic kind from module:ClassName paths.
// Handles stub classes (PassthroughStub → passthrough) and
// real node class names via keyword matching.
var _STUB_MAP = {
  'PassthroughStub': 'passthrough',
  'ToolStub': 'tool',
  'BrokerStub': 'broker',
  'WriteArtifactStub': 'write_artifact',
  'InterruptStub': 'interrupt',
  'MLStub': 'ml',
  'DSPyStub': 'dspy',
  'SubgraphStub': 'subgraph',
  'EchoStub': 'echo',
  'RetrievalStub': 'retrieval',
  'MemoryWriteStub': 'memory_write',
};

var _CLASS_PATTERNS = [
  [/Hitl|Interrupt|HumanInLoop/i, 'interrupt'],
  [/Broker|Nautilus|Nautobot|CargoNet|Publish|DocPlus/i, 'broker'],
  [/Emit|WriteArtifact|RenderDoc|Archive/i, 'write_artifact'],
  [/Planner|CodeWriter|Extract|Critic|Classify|Canonicalize|Injection|Critique|Discovery|Retrospective/i, 'dspy'],
  [/Sandbox|SubGraph|Progressive/i, 'subgraph'],
  [/Ssvc|MlInfer|Predict/i, 'ml'],
  [/VecSearch|GraphPrior|GraphBlast|Framework|Retriev/i, 'retrieval'],
  [/KgWrite|PlanKg|Writeback|Persist|Outcome|Fetch|Lookup|Judge|Safety|Lint|CreateChange|Request/i, 'tool'],
  [/Gate|Branch|Dispatch|Join|Terminal|Suppress|Defer|Track|Done|Quarantine|Divergence|Rollback|Verify|Drift|Attach|Validate|Attest|SelfValid|Proof|Audit|Trust/i, 'passthrough'],
];

window.inferKindKey = function(kind) {
  if (!kind) return '_custom';
  if (window.THEME.nodeKinds[kind]) return kind;

  var parsed = window.parseCustomKind(kind);
  if (!parsed) return '_custom';

  var cls = parsed.className;
  if (_STUB_MAP[cls]) return _STUB_MAP[cls];

  for (var i = 0; i < _CLASS_PATTERNS.length; i++) {
    if (_CLASS_PATTERNS[i][0].test(cls)) return _CLASS_PATTERNS[i][1];
  }

  return '_custom';
};

window.getNodeKindInfo = function(kind) {
  if (!kind) return window.THEME.nodeKinds._custom;
  var key = window.inferKindKey(kind);
  return window.THEME.nodeKinds[key] || window.THEME.nodeKinds._custom;
};

window.getNodeKindKey = function(kind) {
  return window.inferKindKey(kind);
};

window.parseCustomKind = function(kind) {
  if (!kind) return null;
  if (window.THEME.nodeKinds[kind]) return null;
  var parts = kind.split(':');
  if (parts.length === 2) {
    return { module: parts[0], className: parts[1] };
  }
  return { module: kind, className: kind };
};
