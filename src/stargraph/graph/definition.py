# SPDX-License-Identifier: Apache-2.0
"""stargraph.Graph -- static, hashable, IR-validated graph definition (FR-1, FR-2, FR-4).

Per design §3.1.1, ``Graph`` is the sync construction half of the Temporal-style
Graph/GraphRun split: validate IR, compile the state schema, and pin both the
JCS structural hash (FR-4) and the ``(python_version, stargraph_version)`` runtime
hash at definition time. ``await graph.start(...)`` returns a :class:`GraphRun`
(execution loop lands in task 1.11); ``await graph.simulate(...)`` runs offline
fixtures (full implementation in task 3.43). Repeated ``start()`` calls return
fresh runs bound to the same ``graph_hash`` -- counterfactuals derive a new
hash via JCS domain separation in :mod:`stargraph.replay.counterfactual`.

The constructor is the single entry point that wires the four FR-4 components
into :func:`stargraph.graph.hash.structural_hash`:

(a) topology, (b) per-node JSON schema, (c) compiled state schema, and
(d) rule-pack ``(id, sha256, version)`` triples. Phase 1 has no rule packs
mounted on graphs (governance lands later), so component (d) is the empty
list -- documented inline so the omission is auditable.

State-schema compilation: the IR's ``state_schema`` is a ``dict[str, str]``
mapping (e.g. ``{"message": "str"}`` from ``sample-graph.yaml``). This module
turns it into a real :class:`pydantic.BaseModel` subclass via
:func:`pydantic.create_model`, so the engine can typecheck state at the
boundaries while keeping the IR portable across runtimes. The mapping accepts
the small set of POC-grade type names listed in :data:`_TYPE_MAP`; unknown
type names raise :class:`stargraph.errors.ValidationError` rather than silently
falling back to ``Any`` (FR-6 force-loud).
"""

from __future__ import annotations

import importlib
import sys
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, create_model

import stargraph
from stargraph.errors import IRValidationError, SimulationError, ValidationError
from stargraph.graph.hash import runtime_hash, structural_hash
from stargraph.graph.run import GraphRun
from stargraph.ir._validate import validate as _validate_ir
from stargraph.tools.spec import SideEffects

if TYPE_CHECKING:
    from stargraph.ir._models import IRDocument, ParallelBlock

__all__ = ["Graph", "RuleFiring", "SimulationResult"]


# Phase 1 POC type-name → Python type map for IR ``state_schema`` compilation.
# Kept deliberately small; expansion happens alongside the typed-state work in
# Phase 2 once the IR ``state_schema`` field grows past ``dict[str, str]``.
_TYPE_MAP: dict[str, type[Any]] = {
    "str": str,
    "int": int,
    "bool": bool,
    "bytes": bytes,
}


def _resolve_state_class(state_class_ref: str) -> type[BaseModel]:
    """Import a Pydantic BaseModel subclass declared as ``module.path:ClassName``."""
    if ":" not in state_class_ref:
        raise ValidationError(
            "Invalid state_class format",
            path="/state_class",
            expected="'module.path:ClassName'",
            actual=state_class_ref,
            hint="separate module path and class name with a colon",
            see="docs/tutorials/first-graph.md",
        )
    module_path, _, class_name = state_class_ref.partition(":")
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise ValidationError(
            "Cannot import state_class module",
            path="/state_class",
            expected=f"importable module {module_path!r}",
            actual=str(e),
            hint="check the module path; may need to install the package",
        ) from e
    raw: object = getattr(module, class_name, None)
    if raw is None:
        raise ValidationError(
            "state_class symbol not found",
            path="/state_class",
            expected=f"{class_name!r} attribute on {module_path!r}",
            actual="missing",
            hint="verify the class is defined and exported",
        )
    if not isinstance(raw, type):
        raise ValidationError(
            "state_class is not a Pydantic BaseModel",
            path="/state_class",
            expected="subclass of pydantic.BaseModel",
            actual=type(raw).__name__,
            hint="state_class must point at a class inheriting from BaseModel",
        )
    if not issubclass(raw, BaseModel):
        raise ValidationError(
            "state_class is not a Pydantic BaseModel",
            path="/state_class",
            expected="subclass of pydantic.BaseModel",
            actual=raw.__name__,
            hint="state_class must point at a class inheriting from BaseModel",
        )
    return raw


def _compile_state_schema(
    state_schema: dict[str, str],
    *,
    graph_id: str,
) -> type[BaseModel]:
    """Build a Pydantic ``BaseModel`` subclass from the IR ``state_schema`` dict.

    Each ``{name: type_name}`` entry becomes a required field on the resulting
    model. Field names are taken verbatim; type names are resolved through
    :data:`_TYPE_MAP`. Unknown type names raise
    :class:`stargraph.errors.ValidationError` (FR-6 force-loud -- never silently
    coerce to ``Any``). An empty mapping yields a model with no fields, which
    is the legal "stateless graph" case.

    The model class name is derived from ``graph_id`` so multiple graphs in
    the same process produce distinct schema classes (matters for
    :func:`stargraph.graph.hash.structural_hash` rule (c), which keys the schema
    component on ``model_json_schema(mode='serialization')``).
    """
    fields: dict[str, Any] = {}
    for field_name, type_name in state_schema.items():
        py_type = _TYPE_MAP.get(type_name)
        if py_type is None:
            raise ValidationError(
                "Unsupported state_schema type",
                path=f"/state_schema/{field_name}",
                expected=f"one of {sorted(_TYPE_MAP)}",
                actual=type_name,
                hint="Phase 1 POC accepts a small primitive set; extend _TYPE_MAP if needed.",
                see="docs/tutorials/first-graph.md",
            )
        fields[field_name] = (py_type, ...)

    safe_id = "".join(ch if ch.isalnum() else "_" for ch in graph_id) or "graph"
    model_name = f"StargraphState_{safe_id}"
    return create_model(model_name, __base__=BaseModel, **fields)


def _check_parallel_writes_have_reducers(
    parallel_blocks: list[ParallelBlock],
    state_schema: dict[str, str],
) -> None:
    """Compile-time guard against un-merged parallel writes (FR-11, design §3.6.3).

    Per design §3.6.3, ``Graph.__init__`` must refuse graphs where two or
    more parallel branches write the same state field with no reducer
    declared (the LangGraph ``InvalidUpdateError`` analogue). The
    canonical reducer registry lives in :mod:`stargraph.runtime.merge`.

    The current Phase-1 IR (``ParallelBlock.targets: list[str]``,
    ``state_schema: dict[str, str]``) does not yet encode per-target
    writes nor per-field reducer declarations -- those land alongside
    the ``Mirror[T]`` reducer-attribute extension in a later foundation
    revision. Until then, this function is the structural hook design
    §3.6.3 requires: it walks ``parallel_blocks`` and raises
    :class:`stargraph.errors.IRValidationError` with
    ``violation="parallel-write-no-reducer"`` only when the IR provides
    enough information to detect the violation. With the current IR
    that information is never present, so the function always returns
    cleanly; the hook exists so the expansion to reducer-aware IR is a
    surgical body change, not an api change.
    """
    # Future-extension placeholder. The hook is wired in `Graph.__init__`
    # so reducer-aware IR plugs in here without touching the constructor.
    del parallel_blocks, state_schema
    # When IR grows reducer declarations, build a {field -> reducer?} map
    # and raise as below if any parallel block has 2+ targets writing the
    # same field with no reducer declared:
    #
    #     raise IRValidationError(
    #         "parallel branches write the same field with no reducer",
    #         node_ids=[...],
    #         violation="parallel-write-no-reducer",
    #     )
    return None


# Per FR-28 amendment-6: ``set`` / ``frozenset`` types in IR ``state_schema`` are
# forbidden because Python set iteration order is hash-randomized across
# processes (PEP 456), which would break replay determinism. Callers must use
# ``list[str]`` (with declared sort) or ``dict[str, bool]`` keyed by members.
_FORBIDDEN_STATE_SCHEMA_TYPES: frozenset[str] = frozenset({"set", "frozenset"})


def _check_state_schema_no_set_fields(state_schema: dict[str, str]) -> None:
    """Compile-time guard: ``set``/``frozenset`` field types forbidden (FR-28, design §3.8.5).

    Per FR-28 amendment-6, set-typed state fields break replay determinism
    because Python set iteration is hash-randomized across processes (PEP 456).
    Callers must use ``list[str]`` with a declared sort or
    ``dict[str, bool]`` keyed by would-be members.

    Raises:
        IRValidationError: with ``violation="set-field-forbidden"`` and
            ``field``/``type_name`` context when a state schema field declares
            a forbidden set-type.
    """
    for field_name, type_name in state_schema.items():
        if type_name in _FORBIDDEN_STATE_SCHEMA_TYPES:
            raise IRValidationError(
                (
                    f"state_schema field {field_name!r} uses forbidden "
                    f"type {type_name!r}; sets break replay determinism "
                    "(PEP 456 hash randomization). Use list[str] with a "
                    "declared sort or frozenset with declared sort instead "
                    "(FR-28, design §3.8.5)."
                ),
                field=field_name,
                type_name=type_name,
                violation="set-field-forbidden",
            )


_UNSAFE_CANCEL_SIDE_EFFECTS: frozenset[SideEffects] = frozenset(
    {SideEffects.write, SideEffects.external},
)


def _check_race_side_effects(
    parallel_blocks: list[ParallelBlock],
    *,
    side_effects_by_node: dict[str, SideEffects],
    allow_unsafe_cancel_nodes: frozenset[str],
) -> None:
    """Compile-time guard against unsafe ``race``/``any`` cancellation (FR-12, design §3.6.1).

    Per FR-12, a ``race`` or ``any`` parallel block must refuse to compile
    when any branch contains a tool whose ``side_effects`` is ``write``
    or ``external`` -- cancelling a losing branch mid-write risks
    half-committed I/O. The escape hatch is per-branch
    ``allow_unsafe_cancel: true`` (operator opt-in, design §3.6.1).

    Phase-1 IR (:class:`stargraph.ir._models.ParallelBlock`) carries
    ``targets: list[str]`` and ``strategy: str`` but does not yet expose
    a per-branch tool-side-effect map or the ``allow_unsafe_cancel``
    flag. This helper accepts both as explicit parameters so the
    violation semantics are pinned now; ``Graph.__init__`` wires it with
    empty inputs until the IR extension lands, at which point the
    constructor populates the parameters from the IR and the contract
    here remains identical (no api change).

    Raises:
        IRValidationError: with ``violation="unsafe-cancel"`` and
            structured ``node_ids``/``strategy`` context when a
            non-opted-in ``race``/``any`` branch hits a write/external
            tool.
    """
    offenders: list[str] = []
    triggering_strategy: str | None = None
    for block in parallel_blocks:
        if block.strategy not in ("race", "any"):
            continue
        for target in block.targets:
            if target in allow_unsafe_cancel_nodes:
                continue
            effect = side_effects_by_node.get(target)
            if effect is None:
                continue
            if effect in _UNSAFE_CANCEL_SIDE_EFFECTS:
                offenders.append(target)
                triggering_strategy = block.strategy

    if offenders:
        raise IRValidationError(
            (
                f"{triggering_strategy!r} parallel block has branch(es) with "
                "write/external side effects; cancellation would risk "
                "half-committed I/O. Set allow_unsafe_cancel=true on the "
                "branch to opt in (design §3.6.1)."
            ),
            node_ids=offenders,
            strategy=triggering_strategy,
            violation="unsafe-cancel",
        )


class Graph:
    """Static, hashable, IR-validated graph definition (design §3.1.1, FR-1/2/4).

    Construction is synchronous and side-effect free: it validates the IR
    against the foundation validators, compiles the state schema, and pins
    ``graph_hash`` (JCS over the four FR-4 components) plus ``runtime_hash``
    (sha256 of ``python_version + stargraph_version``). Both hashes are stable
    across processes given identical inputs (FR-4 amendment 3).

    Repeated ``start()`` calls return fresh :class:`GraphRun` handles bound to
    the same ``graph_hash``; counterfactuals derive a new ``graph_hash`` via
    domain separation (see :mod:`stargraph.replay.counterfactual`, Phase 3).

    Attributes:
        ir: The validated :class:`stargraph.ir._models.IRDocument` source.
        graph_hash: Hex sha256 of the JCS-canonical four-component dict.
        runtime_hash: Hex sha256 of ``python_version + NUL + stargraph_version``.
        state_schema: The compiled Pydantic ``BaseModel`` subclass.
        plugin_loader: Optional pluggy-based plugin manager (typed ``Any``
            for Phase 1; a typed Protocol lands when 1.13 lands).
        registry: Optional in-memory tool/skill registry (typed ``Any`` for
            Phase 1; the typed surface lands when 1.13 lands).
    """

    ir: IRDocument
    graph_hash: str
    runtime_hash: str
    state_schema: type[BaseModel]
    plugin_loader: Any
    registry: Any

    def __init__(
        self,
        ir: IRDocument,
        *,
        plugin_loader: Any = None,
        registry: Any = None,
    ) -> None:
        # Step 1: validate the IR through the foundation entry point. The
        # validator accepts a dict/JSON-string; round-trip through model_dump
        # so an already-typed IRDocument re-runs the structured checks
        # (stable-ID slugs, version major, extra='forbid'). Empty list ⇒ ok.
        errors = _validate_ir(ir.model_dump(mode="json"))
        if errors:
            # Surface the first error; full multi-error rendering belongs to
            # the CLI / loader layer (it accepts a list, this constructor does
            # not). FR-6 force-loud: never silently accept invalid IR.
            raise errors[0]

        # Step 1b: FR-28 compile-time check -- refuse ``set``/``frozenset``
        # state-schema field types (design §3.8.5, amendment-6). Runs before
        # ``_compile_state_schema`` so the targeted IRValidationError surfaces
        # rather than the generic "unsupported type" ValidationError fallback.
        _check_state_schema_no_set_fields(ir.state_schema)

        # Step 2: compile the IR's ``state_schema`` placeholder into a real
        # Pydantic model. See module docstring for the type-name policy.
        # When ``ir.state_class`` is set, import that BaseModel subclass instead;
        # the two are mutually exclusive.
        if ir.state_class is not None:
            if ir.state_schema:
                raise ValidationError(
                    "state_class and state_schema are mutually exclusive",
                    path="/state_class",
                    expected="exactly one of state_class or non-empty state_schema",
                    actual="both set",
                    hint="remove state_schema entries when using state_class",
                )
            self.state_schema = _resolve_state_class(ir.state_class)
        else:
            self.state_schema = _compile_state_schema(ir.state_schema, graph_id=ir.id)

        # Step 2b: FR-11 compile-time check -- refuse graphs with un-merged
        # parallel writes (design §3.6.3, LangGraph InvalidUpdateError analogue).
        # Hook is currently a no-op pending reducer-aware IR; see helper.
        _check_parallel_writes_have_reducers(ir.parallel, ir.state_schema)

        # Step 2c: FR-12 compile-time check -- refuse ``race``/``any`` parallel
        # blocks whose branches contain write/external side-effect tools (design
        # §3.6.1). Phase-1 IR does not yet expose the per-branch tool side-effect
        # map nor the ``allow_unsafe_cancel`` flag, so the inputs are empty here;
        # the helper exercises its full violation/opt-in semantics through the
        # unit-test surface and becomes load-bearing once the IR extension lands.
        _check_race_side_effects(
            ir.parallel,
            side_effects_by_node={},
            allow_unsafe_cancel_nodes=frozenset(),
        )

        # Step 3: pin the structural hash. Phase 1 has no rule packs mounted
        # on graphs, so component (d) of FR-4 amendment 3 is the empty list.
        # When governance/pack mounts go live, derive the triples here.
        rule_pack_versions: list[tuple[str, str, str]] = []
        # Use the compiled BaseModel (not the raw dict) for hash component (c)
        # so structural_hash sees the same JSON schema the runtime will enforce.
        ir_for_hash = ir.model_copy(update={"state_schema": self.state_schema})  # pyright: ignore[reportArgumentType]
        self.graph_hash = structural_hash(
            ir_for_hash,
            rule_pack_versions=rule_pack_versions,
        )

        # Step 4: pin the runtime hash. ``sys.version`` includes build/compiler
        # banners we don't want in the hash; use ``sys.version_info`` digits
        # plus stargraph's distribution version for stability across re-installs.
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        self.runtime_hash = runtime_hash(py_ver, stargraph.__version__)

        # Step 5: store the rest of the construction inputs for the run loop
        # (task 1.11) to consume. Stored last so a hash failure can't leave a
        # half-built Graph hanging around with mismatched fields.
        self.ir = ir
        self.plugin_loader = plugin_loader
        self.registry = registry

    async def start(
        self,
        state: BaseModel | None = None,
        *,
        checkpointer: Any,
        capabilities: Any = None,
        audit_sink: Any = None,
        run_id: str | None = None,
    ) -> GraphRun:
        """Start a fresh execution run bound to this graph's ``graph_hash``.

        Returns a :class:`GraphRun` -- the live, single-use execution handle
        defined in design §3.1.1. The full run-loop wiring (TaskGroup,
        anyio-bounded event bus, action-vocabulary translation, mirror
        scheduler) lands in task 1.16; this skeleton raises so callers don't
        silently get a no-op run.
        """
        if run_id is None:
            run_id = uuid.uuid4().hex
        return GraphRun(
            run_id=run_id,
            graph=self,
            initial_state=state,
            checkpointer=checkpointer,
            capabilities=capabilities,
            audit_sink=audit_sink,
        )

    async def simulate(self, fixtures: dict[str, Any]) -> SimulationResult:
        """Run this graph against offline fixtures and return the rule-firing trace (FR-9).

        Per FR-9, ``simulate`` exists so operators can validate rule logic
        before deploying: it pins synthetic outputs at each node boundary
        (the ``fixtures`` mapping ``node_id -> output``), walks the IR rule
        list in declaration order, and records every rule whose ``when``
        clause references a node present in ``fixtures``. No tool is
        invoked, no LLM is called, no checkpoint is written -- the
        method touches only in-memory IR state and the caller-supplied
        fixtures dict.

        Phase-1 rule semantics: the IR ``RuleSpec.when`` field is a free-form
        ``str`` (the full CLIPS pattern grammar lands later); for the POC
        simulator a rule "fires" when its ``when`` substring contains an
        IR node id that has a fixture entry. This is the minimum surface
        FR-9 needs to be falsifiable -- the trace shape (rule id, fired
        bool, action kinds) is the contract test downstream tooling will
        depend on, not the firing predicate itself, which gets tightened
        when the CLIPS adapter lands.

        Args:
            fixtures: Mapping of ``node_id -> synthetic output``. Every
                node declared in :attr:`ir.nodes` must have a key here;
                a missing key raises :class:`stargraph.errors.SimulationError`
                with ``violation="missing-fixture"`` (FR-6 force-loud --
                ``simulate`` does not fall through to live execution).

        Returns:
            :class:`SimulationResult` with the per-rule firing trace and a
            verbatim copy of the input ``fixtures`` as ``node_outputs``.

        Raises:
            SimulationError: when a node in the IR has no fixture entry.
        """
        # Force-loud fixture coverage check. Iterating ir.nodes (not fixtures)
        # so an extra fixture for a node that does not exist is silently
        # ignored -- only missing coverage is a violation. Sort by IR order
        # so the surfaced ``node_id`` is deterministic across runs.
        for node in self.ir.nodes:
            if node.id not in fixtures:
                raise SimulationError(
                    f"simulate() missing fixture for node {node.id!r}; "
                    "every IR node must have a synthetic output entry "
                    "(simulate does not invoke live tools or LLMs).",
                    node_id=node.id,
                    violation="missing-fixture",
                )

        # Build the rule-firing trace. ``when`` is a free-form POC string;
        # treat any IR node id appearing as a substring as a match. Iterate
        # rules in IR declaration order so the trace is replay-stable.
        firings: list[RuleFiring] = []
        node_ids = [n.id for n in self.ir.nodes]
        for rule in self.ir.rules:
            matched = [nid for nid in node_ids if nid and nid in rule.when]
            firings.append(
                RuleFiring(
                    rule_id=rule.id,
                    fired=bool(matched),
                    matched_nodes=tuple(matched),
                    action_kinds=tuple(action.kind for action in rule.then),
                ),
            )

        # ``node_outputs`` is a defensive shallow copy so callers cannot
        # mutate the result by mutating the fixtures dict afterwards.
        return SimulationResult(
            rule_firings=tuple(firings),
            node_outputs=dict(fixtures),
        )


@dataclass(frozen=True, slots=True)
class RuleFiring:
    """Single entry in the :class:`SimulationResult` rule-firing trace (FR-9).

    Attributes:
        rule_id: The IR rule's stable id.
        fired: Whether the rule's ``when`` matched any fixtured node.
        matched_nodes: IR node ids whose presence triggered the firing
            (empty tuple when ``fired`` is ``False``).
        action_kinds: The ``kind`` discriminator of every action in the
            rule's ``then`` list, in declaration order. Recorded even
            when ``fired`` is ``False`` so callers can render the
            "would-have-fired" branch shape without re-reading the IR.
    """

    rule_id: str
    fired: bool
    matched_nodes: tuple[str, ...]
    action_kinds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """Result of :meth:`Graph.simulate` -- rule-firing trace + node outputs (FR-9).

    The dataclass is frozen so callers cannot mutate the trace after the
    fact (replay-stability invariant). ``rule_firings`` is a tuple in IR
    declaration order; ``node_outputs`` is a shallow copy of the caller's
    ``fixtures`` dict.
    """

    rule_firings: tuple[RuleFiring, ...] = field(default_factory=tuple)
    node_outputs: dict[str, Any] = field(default_factory=dict[str, Any])
