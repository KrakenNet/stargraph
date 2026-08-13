# SPDX-License-Identifier: Apache-2.0
"""High-level authoring façade for durable, cyclic StarGraph pipelines.

The raw engine is powerful but ceremony-heavy: a working durable-HITL graph
hand-wires a synthetic ``graph`` package, CLIPS ``defrule`` routing, an
``IRDocument`` with parallel ``NodeSpec``/``RuleSpec`` declarations, an
event-bus drain race, and a cross-process resume cursor. None of that is the
*pipeline* — it is plumbing every author re-derives.

:class:`DurableGraph` hides all of it. You bring:

* a plain Pydantic ``state`` model (no ``Mirror`` annotations);
* ``nodes``: ``{name: fn}`` where ``fn(state) -> dict`` returns a state patch
  (sync or async);
* ``edges``: ``{name: target}`` where ``target`` is another node name,
  :data:`END`, or a callable ``fn(state) -> name`` (a conditional edge);
* ``interrupts``: ``{name: prompt}`` declaring human-in-the-loop pause nodes.

The façade compiles that into a real :class:`~stargraph.graph.Graph` driven by
a live Fathom/CLIPS engine and a :class:`~stargraph.checkpoint.sqlite.SQLiteCheckpointer`,
and gives you three things the raw engine makes you build by hand:

* **run-until-boundary** — :meth:`DurableGraph.run` drives to the next terminal
  / pause / suspend and returns a :class:`StepResult` with the final state;
* **durable cross-process resume** — ``run(..., resume=True)`` reloads the latest
  checkpoint (no cursor bookkeeping); the entry node re-routes to the saved
  position without re-running finished work;
* **side-effect-once suspend** — ``suspend_after="node"`` halts *after* the named
  node ran exactly once; the next resume continues from its successor.

Routing is a pure function of state (the engine gotos the mirrored ``sg_route``
field), which is what makes durable resume correct: a fresh process reloads
state and the engine jumps straight to the saved node.
"""

from __future__ import annotations

import asyncio
import re as _re
import sys
import types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, cast

import fathom
from pydantic import BaseModel, create_model
from pydantic import ValidationError as PydanticValidationError

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.errors import CheckpointError, IRValidationError
from stargraph.fathom import FathomAdapter
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument, NodeSpec, RuleSpec, SGLangServer
from stargraph.ir._mirror import Mirror
from stargraph.ir._models import GotoAction, HaltAction
from stargraph.ir._when import compile_when as _compile_when
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.nodes.interrupt.interrupt_node import InterruptNode, InterruptNodeConfig
from stargraph.runtime.events import ResultEvent, WaitingForInputEvent

__all__ = [
    "DONE",
    "END",
    "DurableGraph",
    "StepResult",
    "authoring_clips",
    "compile_authoring",
    "is_authoring_format",
]

# Sentinel edge target: routing to END halts the run.
END = "__end__"

# Reserved routing/bookkeeping fields the façade injects into the user's state.
# ``sg_route`` is the single mirrored field the CLIPS engine gotos; the rest
# carry the durable-suspend cursor across processes.
_ROUTE = "sg_route"
_HALT = "sg_halt"
_SUSPEND_HALT = "sg_suspend_halt"
_SUSPENDED = "sg_suspended"
_RESUME_ROUTE = "sg_resume_route"
_SUSPEND_AFTER = "sg_suspend_after"
# Entry node id (``nodes[0]``). Must be a valid IR slug ([a-z0-9] start) and not
# collide with a user node name; never itself a goto target.
_ENTRY_ID = "sg-entry"

# fn(state) -> patch (sync or async)
NodeFn = Callable[[Any], "dict[str, Any] | Awaitable[dict[str, Any]]"]
EdgeTarget = str | Callable[[Any], str]  # node name, END, or router


@dataclass
class StepResult:
    """Outcome of one :meth:`DurableGraph.run` invocation.

    ``status`` is one of:

    * ``"done"`` — the run reached a terminal node (edge to :data:`END`);
    * ``"paused"`` — the run hit a HITL interrupt and checkpointed; answer it
      with ``run(resume=True, resume_to=<post-gate node>, patch={...})``;
    * ``"suspended"`` — the run honoured ``suspend_after`` and halted after that
      node; continue with a plain ``run(resume=True)``.
    """

    status: str
    state: BaseModel
    pause_node: str | None = None
    suspend_after: str | None = None


# --------------------------------------------------------------------------- #
# Internal: per-node wrapper                                                   #
# --------------------------------------------------------------------------- #


class _FacadeNodeBase(NodeBase):
    """Wraps a user ``fn(state) -> patch`` and computes the next route.

    The concrete subclass per node carries ``_graph_key`` / ``_node_name`` so it
    can reach the owning :class:`DurableGraph` (registered in ``_REGISTRY``) for
    the user fn + edge. After running the user fn it resolves the edge (static
    target, :data:`END`, or router callable) and writes the result into the
    mirrored ``sg_route`` field. Side-effect-once suspend is applied here: if
    this node is the not-yet-honoured ``suspend_after`` target, it halts *after*
    its side effects, stashing the real successor for resume.
    """

    _graph_key: str = ""
    _node_name: str = ""

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        owner = _REGISTRY[self._graph_key]
        result = owner.node_fn(self._node_name)(state)
        if isinstance(result, Awaitable):
            result = await result
        patch: dict[str, Any] = dict(result or {})
        nxt = owner.resolve_edge(self._node_name, state)
        # Side-effect-once durable suspend: the named node ran exactly once; halt
        # before routing onward and stash the real successor for the next resume.
        if getattr(state, _SUSPEND_AFTER, "") == self._node_name and not getattr(
            state, _SUSPENDED, False
        ):
            patch[_SUSPENDED] = True
            patch[_RESUME_ROUTE] = nxt
            patch[_ROUTE] = _SUSPEND_HALT
        else:
            patch[_ROUTE] = nxt
        return patch


class _RouteNode(NodeBase):
    """Entry node (``nodes[0]``); no side effects.

    Hit once per process — fresh start or cold resume — it re-mirrors ``sg_route``
    so the engine gotos straight to the saved position. On resume past a durable
    suspend it transparently restores the stashed successor.
    """

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx
        if getattr(state, _ROUTE, "") == _SUSPEND_HALT:
            return {_ROUTE: getattr(state, _RESUME_ROUTE, _HALT)}
        return {}


# --------------------------------------------------------------------------- #
# Internal: live Fathom routing                                               #
# --------------------------------------------------------------------------- #


class _RoutingFathom(FathomAdapter):
    """Retract the stale mirrored route fact before each tick's re-assert."""

    def mirror_state(self, state: BaseModel, annotations: dict[str, Any]) -> list[Any]:
        self.engine.retract(_ROUTE)
        return super().mirror_state(state, annotations)


_MIRROR_SLOTS = """
      - {name: value, type: string}
      - {name: _origin, type: string}
      - {name: _source, type: string}
      - {name: _run_id, type: string}
      - {name: _step, type: string}
      - {name: _confidence, type: string}
      - {name: _timestamp, type: string}
"""
_TEMPLATES_YAML = f"""
templates:
  - name: {_ROUTE}
    slots:{_MIRROR_SLOTS}
  - name: stargraph_action
    slots:
      - {{name: kind, type: symbol}}
      - {{name: target, type: string}}
      - {{name: reason, type: string}}
"""


def _build_fathom() -> _RoutingFathom:
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="sg_facade_"))
    (workdir / "templates.yaml").write_text(_TEMPLATES_YAML, encoding="utf-8")
    engine = fathom.Engine(default_decision="allow")
    engine.load_templates(str(workdir / "templates.yaml"))
    # One generic goto rule: route to whatever node name sits in ``sg_route``,
    # except the two terminal sentinels which halt instead.
    engine.load_clips_function(
        f'(defrule sg-goto ({_ROUTE} (value ?t&~"{_HALT}"&~"{_SUSPEND_HALT}")) '
        f"=> (assert (stargraph_action (kind goto) (target ?t))))"
    )
    for sentinel, reason in ((_HALT, "done"), (_SUSPEND_HALT, "suspended")):
        engine.load_clips_function(
            f'(defrule sg-halt-{sentinel} ({_ROUTE} (value "{sentinel}")) '
            f'=> (assert (stargraph_action (kind halt) (reason "{reason}"))))'
        )
    return _RoutingFathom(engine)


# --------------------------------------------------------------------------- #
# Façade                                                                       #
# --------------------------------------------------------------------------- #

# DurableGraph instances register here so their dynamically-built node classes
# (which the synthetic ``kind`` package must reference) can reach them at run
# time without threading a closure through ``NodeSpec``.
_REGISTRY: dict[str, DurableGraph] = {}


class DurableGraph:
    """A durable, cyclic pipeline authored from plain functions + an edge map.

    See the module docstring for the full contract. Construction compiles the
    graph (synthetic package, Fathom rules, IR) once; :meth:`run` drives it.
    """

    def __init__(
        self,
        *,
        state: type[BaseModel],
        nodes: dict[str, NodeFn],
        edges: dict[str, EdgeTarget],
        entry: str,
        interrupts: dict[str, str] | None = None,
    ) -> None:
        self._user_state = state
        self._nodes = dict(nodes)
        self._edges = dict(edges)
        self._entry = entry
        self._interrupts = dict(interrupts or {})
        self._key = f"g{len(_REGISTRY)}_{id(self)}"
        _REGISTRY[self._key] = self

        self._state_cls = self._build_state_cls()
        self._modname = self._expose_module()
        self._graph = self._build_graph()

    # ----- compile-time wiring ------------------------------------------- #

    def _build_state_cls(self) -> type[BaseModel]:
        """Subclass the user's state, adding the mirrored route + suspend cursor."""
        return create_model(
            "_FacadeState",
            __base__=self._user_state,
            sg_route=(Annotated[str, Mirror(template=_ROUTE)], self._entry),
            sg_suspended=(bool, False),
            sg_resume_route=(str, ""),
            sg_suspend_after=(str, ""),
        )

    def _node_classes(self) -> dict[str, type[NodeBase]]:
        """One concrete wrapper class per work node (distinct ``kind`` identity)."""
        out: dict[str, type[NodeBase]] = {}
        for name in self._nodes:
            out[name] = type(
                f"Node_{name}",
                (_FacadeNodeBase,),
                {"_graph_key": self._key, "_node_name": name},
            )
        return out

    def _expose_module(self) -> str:
        """Build the synthetic ``kind``-target package the IR references.

        The raw engine resolves ``NodeSpec.kind`` ("module:Class") for graph
        identity, so the classes must live in an importable module. The façade
        owns this module entirely — the author never sees it.
        """
        modname = f"_sg_facade_{self._key}"
        mod = types.ModuleType(modname)
        mod._RouteNode = _RouteNode  # type: ignore[attr-defined]
        mod.InterruptNode = InterruptNode  # type: ignore[attr-defined]
        mod._State = self._state_cls  # type: ignore[attr-defined]
        self._node_cls = self._node_classes()
        for cls in self._node_cls.values():
            setattr(mod, cls.__name__, cls)
        sys.modules[modname] = mod
        return modname

    def _build_graph(self) -> Graph:
        m = self._modname
        nodes = [NodeSpec(id=_ENTRY_ID, kind=f"{m}:_RouteNode")]
        nodes += [
            NodeSpec(id=name, kind=f"{m}:{self._node_cls[name].__name__}") for name in self._nodes
        ]
        nodes += [NodeSpec(id=name, kind=f"{m}:InterruptNode") for name in self._interrupts]
        # Goto rules only for real targets (user nodes + interrupts); the entry
        # node is nodes[0] and is never reached by a goto.
        route_targets = [*self._nodes, *self._interrupts]
        rules = [
            RuleSpec(id=f"r-{t}", when=f'({_ROUTE} (value "{t}"))', then=[GotoAction(target=t)])
            for t in route_targets
        ]
        rules.append(
            RuleSpec(
                id="r-halt", when=f'({_ROUTE} (value "{_HALT}"))', then=[HaltAction(reason="done")]
            )
        )
        ir = IRDocument(
            ir_version="1.0.0",
            id="graph:durable-facade",
            state_class=f"{self._modname}:_State",
            nodes=nodes,
            rules=rules,
        )
        return Graph(ir)

    def _registry(self) -> dict[str, NodeBase]:
        reg: dict[str, NodeBase] = {_ENTRY_ID: _RouteNode()}
        for name, cls in self._node_cls.items():
            reg[name] = cls()
        for name, prompt in self._interrupts.items():
            reg[name] = InterruptNode(
                config=InterruptNodeConfig(prompt=prompt, interrupt_payload={"node": name})
            )
        return reg

    # ----- routing ------------------------------------------------------- #

    def node_fn(self, name: str) -> NodeFn:
        """Internal: the user fn for ``name`` (called by the façade node wrappers)."""
        return self._nodes[name]

    def resolve_edge(self, name: str, state: BaseModel) -> str:
        """Internal: resolve ``name``'s edge (static, router callable, or END)."""
        target = self._edges.get(name, END)
        if callable(target):
            target = target(state)
        return _HALT if target == END else target

    # ----- run / resume -------------------------------------------------- #

    def run(
        self,
        *,
        run_id: str,
        db_path: str | Path,
        start_state: BaseModel | None = None,
        resume: bool = False,
        resume_to: str | None = None,
        patch: dict[str, Any] | None = None,
        suspend_after: str | None = None,
    ) -> StepResult:
        """Drive the graph to the next boundary (terminal / pause / suspend).

        Fresh start: pass ``start_state`` (a user-state instance). Resume: pass
        ``resume=True`` to reload the latest checkpoint; ``resume_to`` overrides
        the re-entry node (e.g. the post-gate router after answering a HITL
        pause) and ``patch`` merges fields into the reloaded state (e.g. the
        human decision). ``suspend_after`` halts after the named node runs once.
        """
        return asyncio.run(
            self._run_async(
                run_id=run_id,
                db_path=Path(db_path),
                start_state=start_state,
                resume=resume,
                resume_to=resume_to,
                patch=patch or {},
                suspend_after=suspend_after,
            )
        )

    async def _run_async(
        self,
        *,
        run_id: str,
        db_path: Path,
        start_state: BaseModel | None,
        resume: bool,
        resume_to: str | None,
        patch: dict[str, Any],
        suspend_after: str | None,
    ) -> StepResult:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        cp = SQLiteCheckpointer(db_path)
        await cp.bootstrap()
        try:
            if not resume:
                fields = start_state.model_dump() if start_state is not None else {}
                st = self._state_cls(**fields)
                setattr(st, _ROUTE, self._entry)
                setattr(st, _SUSPEND_AFTER, suspend_after or "")
            else:
                loaded = await GraphRun.resume(cp, run_id, graph=self._graph)
                init = loaded.initial_state
                if init is None:
                    raise CheckpointError(f"resume: run {run_id!r} has no checkpointed state")
                raw = init.model_dump()
                st = self._state_cls(
                    **{k: raw[k] for k in self._state_cls.model_fields if k in raw}
                )
                if suspend_after is not None:
                    setattr(st, _SUSPEND_AFTER, suspend_after)
                for k, v in patch.items():
                    setattr(st, k, v)
                if resume_to is not None:
                    setattr(st, _ROUTE, resume_to)

            run = GraphRun(
                run_id=run_id,
                graph=self._graph,
                initial_state=st,
                node_registry=self._registry(),
                checkpointer=cp,
                fathom=_build_fathom(),
            )
            status, final_state = await self._drive(run)
            final = self._state_cls(
                **{k: final_state[k] for k in self._state_cls.model_fields if k in final_state}
            )
            if status == "paused":
                pause_node = final_state.get("__pause_node__")
                return StepResult(
                    "paused", final, pause_node=pause_node if isinstance(pause_node, str) else None
                )
            if getattr(final, _ROUTE, "") == _SUSPEND_HALT:
                return StepResult(
                    "suspended", final, suspend_after=getattr(final, _SUSPEND_AFTER, "")
                )
            return StepResult("done", final)
        finally:
            await cp.close()

    async def _drive(self, run: GraphRun) -> tuple[str, dict[str, Any]]:
        """Race the run against an event-bus consumer to the first boundary."""
        final: dict[str, Any] = {}
        paused = False
        pause_node: str | None = None

        async def _consume() -> None:
            nonlocal paused, pause_node
            while True:
                ev = await run.bus.receive()
                if isinstance(ev, WaitingForInputEvent):
                    paused = True
                    node = ev.interrupt_payload.get("node")
                    pause_node = node if isinstance(node, str) else None
                    return
                if isinstance(ev, ResultEvent):
                    final.update(ev.final_state)
                    return

        await asyncio.gather(run.start(), _consume())
        if paused:
            # On a pause the engine exits before emitting ResultEvent; reconstruct
            # the boundary state from the run's last checkpoint-bearing state.
            final = run.initial_state.model_dump() if run.initial_state is not None else final
            final["__pause_node__"] = pause_node
            return "paused", final
        return "done", final


# --------------------------------------------------------------------------- #
# YAML authoring compiler (P4)                                                #
# --------------------------------------------------------------------------- #
#
# The authoring format is the ~12-line front door: ``state`` (field-name ->
# type, ``route: true`` marks a Mirror-routed field), ``nodes`` (name ->
# ``kind`` + config), ``routes`` (name -> target, or name -> {verdict-value:
# target}; ``done`` halts). :func:`compile_authoring` lowers it to a strict
# :class:`IRDocument` -- a synthesized state module (Mirror annotations dict
# ``state_schema`` cannot express), ``when`` mapping-sugar rules, and plain
# NodeSpecs -- so every downstream surface (run, serve, replay, simulate)
# sees ordinary IR. ``stargraph run`` detects the format by the absence of
# ``ir_version`` and compiles transparently; ``stargraph compile`` prints
# the lowering for learning/debug.

#: Sentinel route target: halt the run.
DONE = "done"

#: Authoring ``type:`` names -> (python type, zero default).
_AUTHORING_TYPES: dict[str, tuple[type, Any]] = {
    "str": (str, ""),
    "int": (int, 0),
    "float": (float, 0.0),
    "bool": (bool, False),
    "list": (list, []),
    "dict": (dict, {}),
}

_AUTHORING_KEYS = frozenset({"id", "state", "nodes", "routes", "lm"})
_NAME_RE = _re.compile(r"^[a-z0-9][a-z0-9_.\-]*$")


def is_authoring_format(doc: Any) -> bool:
    """True when ``doc`` is an authoring-format mapping (no ``ir_version``)."""
    return isinstance(doc, dict) and "ir_version" not in doc and "nodes" in doc


def _authoring_error(message: str, *, hint: str | None = None) -> IRValidationError:
    return IRValidationError(f"authoring: {message}", hint=hint)


def _compile_state_field(name: str, raw: Any) -> tuple[Any, Any, bool]:
    """One ``state`` entry -> (annotation, default, routed?)."""
    if isinstance(raw, str):
        type_name, routed, default = raw, False, None
    elif isinstance(raw, dict):
        raw_map = cast("dict[str, Any]", raw)
        extra = set(raw_map) - {"type", "route", "default"}
        if extra:
            raise _authoring_error(
                f"state field {name!r} has unknown keys {sorted(extra)}",
                hint="allowed: type, route, default",
            )
        type_name = str(raw_map.get("type", "str"))
        routed = bool(raw_map.get("route", False))
        default = raw_map.get("default")
    else:
        raise _authoring_error(
            f"state field {name!r} must be a type name or a mapping "
            f"(type/route/default), got {type(raw).__name__}"
        )
    if type_name not in _AUTHORING_TYPES:
        raise _authoring_error(
            f"state field {name!r} has unsupported type {type_name!r}",
            hint=f"one of: {', '.join(sorted(_AUTHORING_TYPES))}",
        )
    py_type, zero = _AUTHORING_TYPES[type_name]
    annotation: Any = Annotated[py_type, Mirror(lifecycle="step")] if routed else py_type
    return annotation, (default if default is not None else zero), routed


def _synthesize_state_module(graph_id: str, state: dict[str, Any]) -> tuple[str, set[str]]:
    """Build + register the state module; return (``state_class`` ref, routed fields).

    The module name derives deterministically from the graph id, so
    re-compiling the same document (fresh process, replay) lands on the
    same ``state_class`` reference and the graph hash stays stable.
    """
    fields: dict[str, Any] = {}
    routed: set[str] = set()
    for name, raw in state.items():
        if not name.isidentifier():
            raise _authoring_error(f"state field {name!r} is not a valid identifier")
        annotation, default, is_routed = _compile_state_field(name, raw)
        fields[name] = (annotation, default)
        if is_routed:
            routed.add(name)
    modname = "_sg_authored_" + _re.sub(r"[^0-9A-Za-z_]", "_", graph_id)
    state_cls: type[BaseModel] = create_model("State", **fields)
    mod = types.ModuleType(modname)
    mod.State = state_cls  # type: ignore[attr-defined]
    sys.modules[modname] = mod
    return f"{modname}:State", routed


def _default_tool_version(tool_id: str) -> str:
    """Bare tool ids default to version 1 (``std.web_search`` -> ``@1``)."""
    return tool_id if "@" in tool_id else f"{tool_id}@1"


def _compile_node(name: str, raw: Any) -> NodeSpec:
    if not isinstance(raw, dict):
        raise _authoring_error(
            f"node {name!r} must be a mapping with a `kind` key, got {type(raw).__name__}"
        )
    if not _NAME_RE.match(name):
        raise _authoring_error(f"node name {name!r} must match {_NAME_RE.pattern}")
    config: dict[str, Any] = dict(cast("dict[str, Any]", raw))
    kind = config.pop("kind", None)
    if not isinstance(kind, str) or not kind:
        raise _authoring_error(f"node {name!r} is missing `kind`")
    spec_path = config.pop("spec", None)
    if spec_path is not None and kind != "subgraph":
        raise _authoring_error(f"node {name!r}: `spec` is only valid on kind: subgraph")
    tools: Any = config.get("tools")
    if isinstance(tools, list):
        config["tools"] = [_default_tool_version(str(t)) for t in cast("list[Any]", tools)]
    if isinstance(config.get("tool"), str):
        config["tool"] = _default_tool_version(str(config["tool"]))
    return NodeSpec(
        id=name,
        kind=kind,
        spec=str(spec_path) if spec_path is not None else None,
        config=config,
    )


def _compile_lm(raw: Any) -> SGLangServer | None:
    """Lower the optional top-level ``lm:`` block to an IR endpoint spec."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _authoring_error(
            f"`lm` must be a mapping, got {type(raw).__name__}",
            hint="keys: provider (sglang), model, host, port, args, startup_timeout_s",
        )
    try:
        return SGLangServer.model_validate(raw)
    except PydanticValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc']) or 'lm'}: {err['msg']}"
            for err in exc.errors()
        )
        raise _authoring_error(
            f"`lm` block is invalid -- {problems}",
            hint="keys: provider (sglang), model, host, port, args, startup_timeout_s",
        ) from exc


def _compile_routes(
    routes: dict[str, Any],
    node_names: list[str],
    routed_fields: set[str],
) -> list[RuleSpec]:
    known = set(node_names)
    rules: list[RuleSpec] = []

    def _then(source: str, target: str) -> list[Any]:
        if target == DONE:
            return [HaltAction(reason=f"{source} complete")]
        if target not in known:
            raise _authoring_error(
                f"route from {source!r} targets unknown node {target!r}",
                hint=f"nodes: {', '.join(node_names)}; or `done` to halt",
            )
        return [GotoAction(target=target)]

    for source, target in routes.items():
        if source not in known:
            raise _authoring_error(f"route source {source!r} is not a declared node")
        if isinstance(target, str):
            rules.append(
                RuleSpec(id=f"r-{source}", when={"node": source}, then=_then(source, target))
            )
        elif isinstance(target, dict):
            if "verdict" not in routed_fields:
                raise _authoring_error(
                    f"route {source!r} branches on verdict values, but the "
                    "state does not declare a routed `verdict` field",
                    hint="add `verdict: {type: str, route: true}` to state",
                )
            for value, tgt in cast("dict[Any, Any]", target).items():
                rules.append(
                    RuleSpec(
                        id=f"r-{source}-{value}",
                        when={"node": source, "verdict": str(value)},
                        then=_then(source, str(tgt)),
                    )
                )
        else:
            raise _authoring_error(
                f"route {source!r} must be a target name or a "
                f"{{verdict-value: target}} mapping, got {type(target).__name__}"
            )
    return rules


def compile_authoring(doc: dict[str, Any], *, default_id: str = "authored") -> IRDocument:
    """Lower an authoring-format mapping to a strict :class:`IRDocument`.

    Raises :class:`~stargraph.errors.IRValidationError` on any shape
    problem; the message names the offending key and the fix.
    """
    extra = set(doc) - _AUTHORING_KEYS
    if extra:
        raise _authoring_error(
            f"unknown top-level keys {sorted(extra)}",
            hint=f"allowed: {', '.join(sorted(_AUTHORING_KEYS))} "
            "(full IR documents must declare ir_version)",
        )
    graph_id = doc.get("id", default_id)
    if not isinstance(graph_id, str) or not _NAME_RE.match(graph_id):
        raise _authoring_error(f"id must match {_NAME_RE.pattern}, got {graph_id!r}")

    nodes_raw = doc.get("nodes")
    if not isinstance(nodes_raw, dict) or not nodes_raw:
        raise _authoring_error("`nodes` must be a non-empty mapping of name -> {kind, ...}")
    nodes_map = cast("dict[str, Any]", nodes_raw)

    state_raw: Any = doc.get("state") or {}
    if not isinstance(state_raw, dict):
        raise _authoring_error("`state` must be a mapping of field -> type")
    state_ref, routed_fields = _synthesize_state_module(graph_id, cast("dict[str, Any]", state_raw))

    routes_raw: Any = doc.get("routes") or {}
    if not isinstance(routes_raw, dict):
        raise _authoring_error("`routes` must be a mapping of node -> target")

    lm = _compile_lm(doc.get("lm"))

    node_names = [str(name) for name in nodes_map]
    nodes = [_compile_node(str(name), raw) for name, raw in nodes_map.items()]
    rules = _compile_routes(cast("dict[str, Any]", routes_raw), node_names, routed_fields)

    return IRDocument(
        ir_version="1.0.0",
        id=f"graph:{graph_id}",
        state_class=state_ref,
        nodes=nodes,
        rules=rules,
        lm=lm,
    )


def authoring_clips(ir: IRDocument) -> list[str]:
    """Human-readable ``rule-id: <CLIPS LHS> => <actions>`` lines for --show-clips."""
    lines: list[str] = []
    for rule in ir.rules:
        actions = ", ".join(
            f"goto {a.target}" if isinstance(a, GotoAction) else f"halt ({a.reason})"
            for a in rule.then
            if isinstance(a, (GotoAction, HaltAction))
        )
        lines.append(f"{rule.id}: {_compile_when(rule.when)} => {actions}")
    return lines
