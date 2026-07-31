# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.registry -- node-kind resolution for graph builds.

Maps every :class:`~stargraph.ir.NodeSpec` in an IR to a constructed
:class:`~stargraph.nodes.base.NodeBase` instance. Formerly private to
``stargraph.cli.run``; moved here because the CLI is one consumer among
several (``stargraph serve`` and the demo servers build node registries
too), and because plugins extend the kind table via the
``stargraph.nodes`` entry-point group (``register_nodes`` hookspec).

Resolution order for ``NodeSpec.kind``:

1. the short-kind table (:data:`node_kinds` lists it) -- built-ins plus
   any kinds installed by :func:`register_node_kind` /
   :func:`install_plugin_node_kinds`;
2. ``module.path:ClassName`` -- imported via :mod:`importlib`; the class
   is constructed from ``NodeSpec.config`` (``config_model`` class attr
   if declared, else ``**config`` kwargs, else zero-arg).

Failures raise :class:`~stargraph.errors.IRValidationError` (graph-build
contract violations) or :class:`~stargraph.errors.PluginLoadError`
(kind-table conflicts); CLI entry points translate these into user-facing
parameter errors.
"""

from __future__ import annotations

import contextvars
import importlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from stargraph.errors import IRValidationError, PluginLoadError
from stargraph.ir import IRDocument
from stargraph.nodes.base import EchoNode, ExecutionContext, NodeBase
from stargraph.runtime.events import ToolCallEvent, ToolResultEvent

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.ir._models import NodeSpec

__all__ = [
    "build_node_registry",
    "install_plugin_node_kinds",
    "node_kinds",
    "register_node_kind",
]


class _StubDSPyNode(NodeBase):
    """Stub DSPy node -- built ONLY via an explicit ``config: {stub: true}``.

    The Phase-4 sample graph (``tests/fixtures/sample-graph-phase4.yaml``)
    declares ``node_b`` with ``kind: dspy`` + ``stub: true`` to exercise the
    FR-14 tool-call audit contract end-to-end without standing up a live
    LLM. The paired cassette records zero HTTP interactions, so this node
    returns a fixed answer projection and emits ``tool_call`` /
    ``tool_result`` events on the run bus around the synthetic invocation.
    A ``kind: dspy`` node without the flag builds a real LM-backed
    :class:`~stargraph.nodes.dspy.DSPyNode` (or fails the build loudly).
    """

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        # ``ExecutionContext`` is a :class:`Protocol`; the live driver
        # passes the concrete :class:`GraphRun`, which carries the bus +
        # fathom handle FR-14 events need. Cast through ``Any`` so this
        # surface stays typed against the protocol while still reaching
        # the structural fields the runtime supplies.
        run: Any = ctx
        call_id = f"{run.run_id}-stub-dspy"
        await run.bus.send(
            ToolCallEvent(
                run_id=run.run_id,
                step=0,
                ts=datetime.now(UTC),
                tool_name="dspy.stub",
                namespace="stargraph.tests",
                args={"message": getattr(state, "message", "")},
                call_id=call_id,
            ),
            fathom=run.fathom,
        )
        outputs = {"answer": "stub-answer"}
        await run.bus.send(
            ToolResultEvent(
                run_id=run.run_id,
                step=0,
                ts=datetime.now(UTC),
                call_id=call_id,
                ok=True,
                result=outputs,
            ),
            fathom=run.fathom,
        )
        return outputs


# Short-kind builders. Each takes a NodeSpec and returns a constructed
# NodeBase instance, allowing per-node config (NodeSpec.config) to drive
# constructor kwargs without sub-classing per call site. ``module:ClassName``
# refs go through :func:`_resolve_class_kind` instead.
NodeBuilder = Any  # Callable[[NodeSpec], NodeBase] — typed loosely to avoid TC import cycles.


def _build_echo(_spec: NodeSpec) -> NodeBase:
    return EchoNode()


def _build_passthrough(_spec: NodeSpec) -> NodeBase:
    """``passthrough`` — no-op node mirroring :class:`EchoNode`'s contract.

    Distinct kind name preserved so IRs can document intent (dispatch
    helper vs sentinel) without forcing a separate class.
    """
    return EchoNode()


def _build_dspy(spec: NodeSpec) -> NodeBase:
    """``dspy`` short-kind builder -- real LM-backed node by default.

    ``config: {stub: true}`` short-circuits to :class:`_StubDSPyNode`
    (offline test fixtures, cassette runs) without importing DSPy. The
    signature-presence check lives here too so a bare ``kind: dspy``
    fails fast without the (slow) ``import dspy``; full config
    validation is :func:`stargraph.nodes.dspy.dspy_node_from_config`.
    """
    if spec.config.get("stub"):
        return _StubDSPyNode()
    if not spec.config.get("signature"):
        raise IRValidationError(
            f"dspy node {spec.id!r} requires config.signature "
            "(e.g. 'question -> answer'); set config.stub: true for the "
            "offline test stub"
        )
    from stargraph.nodes.dspy import dspy_node_from_config

    return dspy_node_from_config(spec)


def _build_broker(spec: NodeSpec) -> NodeBase:
    from stargraph.nodes.nautilus.broker_node import BrokerNode, BrokerNodeConfig

    return BrokerNode(config=BrokerNodeConfig.model_validate(spec.config))


def _build_write_artifact(spec: NodeSpec) -> NodeBase:
    from stargraph.nodes.artifacts.write_artifact_node import (
        WriteArtifactNode,
        WriteArtifactNodeConfig,
    )

    return WriteArtifactNode(config=WriteArtifactNodeConfig.model_validate(spec.config))


def _build_interrupt(spec: NodeSpec) -> NodeBase:
    from stargraph.nodes.interrupt.interrupt_node import InterruptNode, InterruptNodeConfig

    return InterruptNode(config=InterruptNodeConfig.model_validate(spec.config))


def _build_ml(spec: NodeSpec) -> NodeBase:
    from stargraph.nodes.ml import MLNode

    return MLNode(**spec.config)


def _build_subgraph(spec: NodeSpec) -> NodeBase:
    """``subgraph`` short-kind builder.

    Reads ``NodeSpec.spec`` as the path to the child IR YAML (relative
    paths resolve against the parent IR's directory, captured in the
    :data:`_IR_DIR_VAR` :class:`ContextVar` by :func:`build_node_registry`).
    The child IR is loaded, every child :class:`NodeSpec` is built via
    the same :func:`_resolve_node_factory` machinery (so nested
    sub-graphs work), and the resulting :class:`NodeBase` list is
    wrapped in a :class:`SubGraphNode` keyed on the parent
    ``NodeSpec.id``.

    Empty / missing ``spec`` falls back to :class:`EchoNode` so legacy
    IRs (no sub-IR yet) still validate and walk.
    """
    if not spec.spec:
        return EchoNode()
    ir_dir = _IR_DIR_VAR.get()
    sub_path = Path(spec.spec)
    if not sub_path.is_absolute():
        if ir_dir is None:
            raise IRValidationError(
                f"subgraph node {spec.id!r} has relative spec={spec.spec!r} "
                f"but no parent IR directory was set"
            )
        sub_path = (ir_dir / sub_path).resolve()
    if not sub_path.is_file():
        raise IRValidationError(f"subgraph node {spec.id!r}: child IR not found at {sub_path}")

    sub_ir_dict = yaml.safe_load(sub_path.read_text(encoding="utf-8"))
    sub_ir = IRDocument.model_validate(sub_ir_dict)
    # Recurse via build_node_registry so nested sub-graphs preserve
    # the parent-IR-dir context via the ContextVar.
    sub_registry = build_node_registry(sub_ir.nodes, ir_dir=sub_path.parent)
    children = [sub_registry[n.id] for n in sub_ir.nodes]

    from stargraph.nodes.subgraph import SubGraphNode

    return SubGraphNode(subgraph_id=spec.id, children=children)


def _build_tool(spec: NodeSpec) -> NodeBase:
    """``tool`` short-kind builder.

    A non-empty config builds :class:`~stargraph.nodes.tool_call.ToolCallNode`
    -- the first-class :func:`~stargraph.runtime.tool_exec.execute_tool` call
    site (capability gate, replay routing, provenance facts, sanitization).
    An empty config keeps the legacy :class:`EchoNode` placeholder so
    historic IRs (skills fixtures, enrichment demo) still validate and
    walk; those get real wiring as the built-in tool pack lands.
    """
    if not spec.config:
        return EchoNode()
    from pydantic import ValidationError as PydanticValidationError

    from stargraph.nodes.tool_call import ToolCallNode, ToolCallNodeConfig

    try:
        cfg = ToolCallNodeConfig.model_validate(spec.config)
    except PydanticValidationError as e:
        raise IRValidationError(f"tool node {spec.id!r}: invalid config: {e}") from e
    return ToolCallNode(config=cfg)


def _build_prebuilt(spec: NodeSpec) -> NodeBase:
    """Signature-preset DSPy kinds (reason/summarize/classify/extract/judge/plan).

    Lazy import: the prebuilt module reaches ``dspy_node_from_config``
    (and therefore ``import dspy``) only when one of these kinds is
    actually built.
    """
    from stargraph.nodes.prebuilt import build_prebuilt

    return build_prebuilt(spec)


_NODE_FACTORIES: dict[str, NodeBuilder] = {
    "echo": _build_echo,
    "halt": _build_echo,  # halt is a marker terminal
    "passthrough": _build_passthrough,
    "dspy": _build_dspy,
    "broker": _build_broker,
    "write_artifact": _build_write_artifact,
    "interrupt": _build_interrupt,
    "ml": _build_ml,
    "subgraph": _build_subgraph,
    "tool": _build_tool,
    # Prebuilt signature-preset DSPy nodes (P3a). Emit standardized
    # fields (verdict/confidence/score/...); Fathom routes on the facts.
    "reason": _build_prebuilt,
    "summarize": _build_prebuilt,
    "classify": _build_prebuilt,
    "extract": _build_prebuilt,
    "judge": _build_prebuilt,
    "plan": _build_prebuilt,
}


def node_kinds() -> list[str]:
    """Sorted list of node ``kind:`` values the registry currently builds.

    Custom nodes are addressable via ``module.path:ClassName`` in addition to
    these. Used by ``stargraph context dump`` to advertise the node surface.
    """
    return sorted(_NODE_FACTORIES)


def register_node_kind(kind: str, builder: NodeBuilder, *, replace: bool = False) -> None:
    """Install ``builder`` as the factory for short-kind ``kind``.

    ``builder`` is called with the full :class:`NodeSpec` and must return a
    :class:`NodeBase`. Duplicate registrations raise
    :class:`~stargraph.errors.PluginLoadError` unless ``replace`` is set —
    the same conflict contract the tool registry applies to tool ids.
    """
    if not replace and kind in _NODE_FACTORIES:
        raise PluginLoadError(f"node kind {kind!r} is already registered")
    _NODE_FACTORIES[kind] = builder


def install_plugin_node_kinds(pm: Any) -> None:
    """Merge every plugin's ``register_nodes()`` result into the kind table.

    ``pm`` is the pluggy :class:`~stargraph.plugin.manager.PluginManager`
    built by :func:`stargraph.plugin.loader.build_plugin_manager`. Each hook
    returns a list of :class:`~stargraph.plugin.types.NodeKindSpec`; kind
    collisions raise :class:`~stargraph.errors.PluginLoadError` (fail-loud,
    same as duplicate tool ids).
    """
    for specs in pm.hook.register_nodes():
        for spec in specs:
            register_node_kind(spec.kind, spec.builder)


def _resolve_class_kind(kind: str) -> type[NodeBase]:
    """Resolve a ``module.path:ClassName`` ref to its :class:`NodeBase` subclass."""
    module_path, _, class_name = kind.partition(":")
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise IRValidationError(
            f"cannot import module {module_path!r} for node kind {kind!r}: {e}"
        ) from e
    cls: Any = getattr(module, class_name, None)
    if cls is None:
        raise IRValidationError(
            f"class {class_name!r} not found in {module_path!r} (kind={kind!r})"
        )
    if not isinstance(cls, type) or not issubclass(cls, NodeBase):
        cls_type_name: str = type(cast("object", cls)).__name__
        raise IRValidationError(f"{kind!r} is not a NodeBase subclass (got {cls_type_name})")
    return cls


def _resolve_node_factory(kind: str) -> NodeBuilder:
    """Map ``NodeSpec.kind`` to a NodeSpec→NodeBase builder.

    Short kinds come from :data:`_NODE_FACTORIES` (built-ins plus any
    plugin-installed kinds). Any kind containing ``:`` is treated as
    ``module.path:ClassName`` and imported via :mod:`importlib`; the
    resolved class is constructed from ``NodeSpec.config``.
    """
    if kind in _NODE_FACTORIES:
        return _NODE_FACTORIES[kind]
    if ":" not in kind:
        raise IRValidationError(
            f"unknown node kind {kind!r}; "
            f"expected one of {sorted(_NODE_FACTORIES)} or 'module.path:ClassName'"
        )
    cls = _resolve_class_kind(kind)

    # ``module:ClassName`` refs bind NodeSpec.config uniformly: a class
    # that declares a ``config_model`` class attr (BrokerNode pattern)
    # gets a validated config instance; otherwise a non-empty config maps
    # to constructor kwargs; otherwise zero-arg (back-compat with the
    # wrapper-class idiom).
    def _build_class(spec: NodeSpec) -> NodeBase:
        ctor: Any = cls  # constructor signatures vary per subclass
        config_model: Any = getattr(cls, "config_model", None)
        if config_model is not None:
            return cast("NodeBase", ctor(config=config_model.model_validate(spec.config)))
        if spec.config:
            return cast("NodeBase", ctor(**spec.config))
        return cls()

    return _build_class


#: Parent-IR directory captured during :func:`build_node_registry` so the
#: ``subgraph`` short-kind builder can resolve relative ``NodeSpec.spec``
#: paths without changing every builder signature. Reset to its prior
#: value on each registry build so nested sub-graphs see their own parent
#: dir.
_IR_DIR_VAR: contextvars.ContextVar[Path | None] = contextvars.ContextVar(
    "_stargraph_ir_dir", default=None
)


def build_node_registry(
    nodes: list[NodeSpec],
    *,
    ir_dir: Path | None = None,
) -> dict[str, NodeBase]:
    """Map ``node_id -> NodeBase`` for every node in ``nodes``.

    Each ``NodeSpec.kind`` is resolved via :func:`_resolve_node_factory`
    and the resulting builder is invoked with the full :class:`NodeSpec`
    so :attr:`NodeSpec.config` flows into per-node constructors
    (broker/ml/write_artifact/interrupt configs).

    ``ir_dir`` is the directory of the IR being built, captured in
    :data:`_IR_DIR_VAR` so :func:`_build_subgraph` can resolve relative
    sub-IR ``NodeSpec.spec`` paths against it.
    """
    token = _IR_DIR_VAR.set(ir_dir)
    try:
        registry: dict[str, NodeBase] = {}
        for node in nodes:
            builder = _resolve_node_factory(node.kind)
            registry[node.id] = builder(node)
        return registry
    finally:
        _IR_DIR_VAR.reset(token)
