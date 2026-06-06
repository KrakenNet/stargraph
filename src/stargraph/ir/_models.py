# SPDX-License-Identifier: Apache-2.0
"""IR Pydantic models -- portable-subset base class and core IR document.

This module hosts the :class:`IRBase` parent for every IR Pydantic type
(``IRDocument``, ``ToolSpec``, ``SkillSpec``, ``PluginManifest`` and their
sub-models). The base sets ``extra='forbid'`` (FR-6, AC-9.1) and is the single
place to wire any future portable-subset config knob.

Task 1.24 extends this module with the IR :data:`Action` discriminated union
(six variants -- FR-11) and the minimal :class:`IRDocument` skeleton that
later tasks (validators, JSON Schema export, structural-hash, mirror folding)
build on. Variant fields parallel the dataclass shapes in
``stargraph.fathom._action`` (POC adapter); the IR-side Pydantic models become
the source of truth in Phase 2.
"""

from __future__ import annotations

from datetime import timedelta  # noqa: TC003 -- pydantic resolves at runtime
from decimal import Decimal  # noqa: TC003 -- pydantic resolves at runtime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from stargraph.tools.spec import ReplayPolicy, SideEffects

__all__ = [
    "Action",
    "AssertAction",
    "CheckpointBlock",
    "FactTemplate",
    "GotoAction",
    "HaltAction",
    "IRBase",
    "IRDocument",
    "InterruptAction",
    "MigrateBlock",
    "NodeSpec",
    "PackMount",
    "PackRequires",
    "ParallelAction",
    "ParallelBlock",
    "PluginManifest",
    "RetractAction",
    "RetryAction",
    "RuleSpec",
    "SkillRef",
    "SkillSpec",
    "SlotDef",
    "StoreRef",
    "StoreSpec",
    "ToolRef",
    "ToolSpec",
]


class IRBase(BaseModel):
    """Parent class for every Stargraph IR Pydantic model.

    Enforces the portable-subset contract: unknown keys are rejected
    (``extra='forbid'``) so JSON Schema round-trip and forward-compat
    upgrades are explicit, never silent (FR-6, AC-9.1).
    """

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Action variants (FR-11) -- discriminated union, top-level only (no nesting).
# ---------------------------------------------------------------------------


class GotoAction(IRBase):
    """Transition to ``target`` rule/state."""

    kind: Literal["goto"] = "goto"
    target: str


class HaltAction(IRBase):
    """Stop execution; ``reason`` is an operator-facing message."""

    kind: Literal["halt"] = "halt"
    reason: str = ""


class ParallelAction(IRBase):
    """Fan out to ``targets`` and join under ``strategy`` (all/any/race/quorum)."""

    kind: Literal["parallel"] = "parallel"
    targets: list[str]
    join: str = ""
    strategy: str = "all"


class RetryAction(IRBase):
    """Re-attempt ``target`` after ``backoff_ms`` milliseconds."""

    kind: Literal["retry"] = "retry"
    target: str
    backoff_ms: int = 0


class AssertAction(IRBase):
    """Assert ``fact`` with ``slots`` (JSON-encoded slot dict in POC)."""

    kind: Literal["assert"] = "assert"
    fact: str
    slots: str = ""


class RetractAction(IRBase):
    """Retract facts matching ``pattern``."""

    kind: Literal["retract"] = "retract"
    pattern: str


class InterruptAction(IRBase):
    """Pause the run and request input (HITL primitive, FR-81, AC-14.1).

    Surfaces a human-in-the-loop checkpoint: the engine emits a
    ``WaitingForInputEvent`` carrying ``prompt`` + ``interrupt_payload``,
    persists a checkpoint, and exits cleanly. Resume happens via
    ``POST /runs/{id}/respond`` (or ``GraphRun.respond()``) gated on
    ``requested_capability``. ``timeout`` bounds the wait; ``on_timeout``
    selects ``"halt"`` (terminal) or ``"goto:<node_id>"`` (resume target).

    Per design §17 Decision #1, dispatch happens on
    ``Action.kind == "interrupt"`` BEFORE ``translate_actions`` -- this
    variant is a control-flow primitive, not a routing decision.
    """

    kind: Literal["interrupt"] = "interrupt"
    prompt: str
    interrupt_payload: dict[str, Any] = Field(default_factory=dict[str, Any])
    requested_capability: str | None = None
    timeout: timedelta | None = None
    on_timeout: Literal["halt"] | str = "halt"  # "halt" or "goto:<node_id>"


Action = Annotated[
    GotoAction
    | HaltAction
    | ParallelAction
    | RetryAction
    | AssertAction
    | RetractAction
    | InterruptAction,
    Field(discriminator="kind"),
]
"""Top-level discriminated union over the six Stargraph verbs (FR-11).

Per FR-11, ``Action`` appears only at the top level of a rule's ``then``
list -- nested actions inside variant fields are deliberately disallowed
to keep rule semantics inspectable without recursion.
"""


# ---------------------------------------------------------------------------
# Minimal POC shells (subclassing IRBase). Later tasks extend these.
# ---------------------------------------------------------------------------


class SlotDef(IRBase):
    """Single slot in a ``FactTemplate``: typed name with optional default."""

    name: str
    type: str
    default: str | None = None


class FactTemplate(IRBase):
    """CLIPS deftemplate descriptor: name plus typed slots."""

    name: str
    slots: list[SlotDef] = Field(default_factory=list[SlotDef])


class ToolRef(IRBase):
    """Reference to a tool by namespaced id (POC: bare string id + optional version)."""

    id: str
    version: str | None = None


class SkillRef(IRBase):
    """Reference to a skill by namespaced id."""

    id: str
    version: str | None = None


class StoreRef(IRBase):
    """Lightweight reference to a store binding (POC: name + provider id).

    :class:`StoreSpec` is the canonical IR record for store registration
    (FR-19/FR-20, design §3.16); ``StoreRef`` remains the trimmed shape used
    inside :class:`IRDocument` graphs. The :meth:`to_capabilities` helper
    yields the same ``[db.{name}:read, db.{name}:write]`` capability list
    derived by ``StoreSpec`` so policy checks can resolve from either form.
    """

    name: str
    provider: str

    def to_capabilities(self) -> list[str]:
        """Return derived capability strings for this store reference."""
        return [f"db.{self.name}:read", f"db.{self.name}:write"]


class PackRequires(IRBase):
    """Pack version-compat requirements (FR-39, design §3.2, §7.4).

    Optional sub-record on :class:`PackMount` declaring the stargraph-facts
    schema version + plugin api_version a Bosun rule pack was authored
    against. Both fields default to ``None`` (no requirement); when set,
    :func:`stargraph.ir._versioning.check_pack_compat` enforces them at
    pack-load time, raising :class:`stargraph.errors.PackCompatError` on
    mismatch (load-fail, never silent runtime drift).
    """

    stargraph_facts_version: str | None = None
    api_version: str | None = None


class PackMount(IRBase):
    """Bosun rule pack mount entry (POC: id + optional version + version-compat).

    ``requires`` (added in task 2.22 for FR-39) carries a
    :class:`PackRequires` block declaring the stargraph-facts and plugin
    api_versions the pack was authored against. ``None`` keeps full
    backwards compatibility -- existing two-field mounts (``id`` +
    ``version``) round-trip and structural-hash byte-identically.
    """

    id: str
    version: str | None = None
    requires: PackRequires | None = None


class NodeSpec(IRBase):
    """Graph node descriptor.

    Attributes:
        id: Node identifier, unique within the IR.
        kind: Either a short kind registered in
            :data:`stargraph.cli.run._NODE_FACTORIES`
            (``echo``/``halt``/``passthrough``/``tool``/``broker``/
            ``write_artifact``/``interrupt``/``ml``/``subgraph``/``dspy``)
            or a ``module.path:ClassName`` reference imported via
            :mod:`importlib`.
        spec: Optional subgraph file path (relative to the IR YAML)
            for ``kind: subgraph`` nodes. Resolved by
            :class:`stargraph.nodes.subgraph.SubGraphNode` at construction.
            ``None`` for non-subgraph nodes.
        config: Optional per-node config dict. Forwarded as kwargs into
            the node factory by
            :func:`stargraph.cli.run._build_node_registry` so concrete node
            classes (``BrokerNode``/``MLNode``/``WriteArtifactNode``)
            can read their own config without sub-classing per call site.
    """

    id: str
    kind: str
    spec: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class RuleSpec(IRBase):
    """Single rule (POC: id + ``when`` pattern + ``then`` action list).

    ``then`` is a list of :data:`Action` -- the discriminated union; the
    FR-11 "no nesting" constraint is enforced by the type itself (variants
    cannot themselves contain :data:`Action` fields).

    Attributes:
        id: Rule identifier, unique within the IR.
        when: CLIPS-style ``when`` pattern (free text; engine parses).
        then: Discriminated-union list of routing/control actions.
        node_id: Owning node id. Introduced in IR ``1.1.0`` for UI /
            topology consumers (so a rule's source node is explicit
            rather than inferred from a naming convention). Optional for
            backward compatibility -- documents declaring
            ``ir_version: "1.0.0"`` may omit it; loaders MAY fill it via
            the ``rule_id`` prefix heuristic so downstream tools
            (``stargraph inspect``, StarGraph topology endpoint) can assume
            non-null. New graphs at ``1.1.0`` SHOULD declare ``node_id``
            explicitly.
    """

    id: str
    when: str = ""
    then: list[Action] = Field(default_factory=list[Action])
    node_id: str | None = None


class ParallelBlock(IRBase):
    """Top-level parallel/join declaration (POC: targets + join + strategy)."""

    targets: list[str]
    join: str = ""
    strategy: str = "all"


class MigrateBlock(IRBase):
    """Migration descriptor for graph-hash mismatch on resume (POC: from/to ids)."""

    from_hash: str
    to_hash: str


class CheckpointBlock(IRBase):
    """Checkpoint policy declaration (design §3.1.2 step 7).

    Top-level IR field that declares the checkpoint cadence and
    backing store. The CLI / serve lifespan reads this block and
    constructs the :class:`stargraph.checkpoint.Checkpointer`. When
    omitted, the run loop uses CLI defaults
    (``./.stargraph/run.sqlite``, ``every: node-exit``).

    Attributes:
        every: Cadence — currently only ``"node-exit"`` is supported;
            ``"never"`` and ``"step-N"`` are reserved for future
            phases.
        store: DSN-style store binding. ``"sqlite:./path.sqlite"``
            (file) or ``"postgres:dsn"`` (Phase 3+).
    """

    every: Literal["node-exit", "never"] = "node-exit"
    store: str = "sqlite:./.stargraph/run.sqlite"


# ---------------------------------------------------------------------------
# IRDocument -- top-level IR shell. Required: ir_version, id, nodes.
# ---------------------------------------------------------------------------


class IRDocument(IRBase):
    """Top-level IR document (POC skeleton).

    Required fields: ``ir_version``, ``id``, ``nodes``. All other top-level
    sections are optional with sensible empty defaults so a minimal document
    (``IRDocument(ir_version="1.0.0", id="run:test", nodes=[])``) constructs
    without further plumbing. Stable-ID validation lands in task 1.28; for
    now ``id`` is a free-form ``str``.
    """

    ir_version: str
    id: str
    nodes: list[NodeSpec]
    rules: list[RuleSpec] = Field(default_factory=list[RuleSpec])
    tools: list[ToolRef] = Field(default_factory=list[ToolRef])
    skills: list[SkillRef] = Field(default_factory=list[SkillRef])
    stores: list[StoreRef] = Field(default_factory=list[StoreRef])
    state_schema: dict[str, str] = Field(default_factory=dict[str, str])
    # Optional escape hatch: declare an existing Pydantic BaseModel subclass via
    # ``module.path:ClassName`` instead of the flat primitive map. Mutually
    # exclusive with a non-empty ``state_schema``. Resolved at Graph construction
    # (not in IR validation) so the IR stays import-free.
    state_class: str | None = None
    parallel: list[ParallelBlock] = Field(default_factory=list[ParallelBlock])
    governance: list[PackMount] = Field(default_factory=list[PackMount])
    migrate: list[MigrateBlock] = Field(default_factory=list[MigrateBlock])
    checkpoints: CheckpointBlock | None = None


# ---------------------------------------------------------------------------
# ToolSpec / SkillSpec / PluginManifest -- portable subset (AC-9.4/9.5/9.6).
# ---------------------------------------------------------------------------


class ToolSpec(IRBase):
    """Tool descriptor (AC-9.4): name, schemas, side-effects, cost, etc.

    ``cost_estimate`` is ``Decimal | None`` per FR-9 (no float for monetary
    fields). All optional list/dict fields default to empty so a minimal
    spec only needs name/namespace/version/description/schemas/side_effects.
    """

    name: str
    namespace: str
    version: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    side_effects: SideEffects
    replay_policy: ReplayPolicy = ReplayPolicy.must_stub
    permissions: list[str] = Field(default_factory=list[str])
    idempotency_key: str | None = None
    cost_estimate: Decimal | None = None
    examples: list[dict[str, object]] = Field(
        default_factory=list[dict[str, object]],
    )
    tags: list[str] = Field(default_factory=list[str])
    deprecated: bool = False


class SkillSpec(IRBase):
    """Skill descriptor (AC-9.6): named bundle of agent/workflow/utility logic.

    Optional ``subgraph`` references a graph fragment id; ``system_prompt``
    is the instruction template. ``tools`` lists tool ids the skill may call.
    """

    name: str
    namespace: str
    version: str
    description: str
    kind: Literal["agent", "workflow", "utility"]
    tools: list[str] = Field(default_factory=list[str])
    examples: list[dict[str, object]] = Field(
        default_factory=list[dict[str, object]],
    )
    subgraph: str | None = None
    system_prompt: str | None = None


class PluginManifest(IRBase):
    """Plugin manifest (AC-9.5): identity + namespaces + provided entity kinds.

    ``order`` (D1 design decision) controls plugin discovery/load priority,
    bounded ``[0, 10000]`` with default ``5000``. ``api_version`` is pinned
    to ``"1"`` -- bumps gate forward-compat upgrades explicitly (FR-6).
    """

    name: str
    version: str
    api_version: Literal["1"]
    namespaces: list[str]
    provides: list[Literal["tool", "skill", "store", "pack", "trigger", "mcp_adapter"]]
    order: Annotated[int, Field(default=5000, ge=0, le=10000)]


class StoreSpec(IRBase):
    """Canonical store registration record (design §3.16, FR-19/FR-20).

    A ``StoreSpec`` describes a named store binding: its provider, the
    portable-subset ``protocol`` (one of ``vector``, ``graph``, ``doc``,
    ``memory``, ``fact``), an opaque ``config_schema`` (JSON Schema for the
    provider's config payload), and the ``capabilities`` it grants.

    Per FR-7 / AC-13.1, IR records may not carry Pydantic
    ``computed_field`` / ``model_validator`` decorators (cross-language
    portability constraint). Auto-derivation of the AC-8.1 default
    ``[f"db.{name}:read", f"db.{name}:write"]`` is therefore exposed via
    :meth:`effective_capabilities`; callers populate ``capabilities``
    explicitly (e.g. ``StoreSpec(..., capabilities=spec.effective_capabilities())``)
    when they want the canonical default baked into the serialized record.
    """

    name: str
    provider: str
    protocol: Literal["vector", "graph", "doc", "memory", "fact"]
    config_schema: dict[str, object]
    capabilities: list[str] = Field(default_factory=list[str])

    def effective_capabilities(self) -> list[str]:
        """Return ``capabilities`` if non-empty, else the AC-8.1 default pair."""
        if self.capabilities:
            return list(self.capabilities)
        return [f"db.{self.name}:read", f"db.{self.name}:write"]
