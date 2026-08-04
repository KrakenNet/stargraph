# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.tool_call -- :class:`ToolCallNode`, the ``kind: tool`` node.

The first-class run-loop call site of
:func:`stargraph.runtime.tool_exec.execute_tool` (FR-24 / design §3.4.4):
every configured ``kind: tool`` node routes its invocation through the full
nine-step pipeline -- input-schema validation, capability gate, replay
routing, ``stargraph.tool-call`` / ``stargraph.tool-result`` provenance
facts, output validation, and sanitization -- never around it.

The tool callable is resolved at *execute* time from the run's graph
registry (``ctx.graph.registry``, a
:class:`stargraph.registry.tools.ToolRegistry`), so IRs validate and load
without a registry wired; running one without a registry fails loudly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stargraph.errors import StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.runtime.tool_exec import RunContext, execute_tool

if TYPE_CHECKING:
    from pydantic import BaseModel as PydanticState

__all__ = ["ToolCallContext", "ToolCallNode", "ToolCallNodeConfig"]


@runtime_checkable
class ToolCallContext(Protocol):
    """Structural surface :class:`ToolCallNode` reads from the run context.

    Mirrors the :class:`~stargraph.nodes.artifacts.write_artifact_node.WriteArtifactContext`
    convention: the fields tool execution depends on are *required*, so a
    context that lacks them fails loudly instead of silently defaulting
    (a defaulted ``step`` would stamp every provenance fact with ``0``;
    a defaulted ``is_replay`` would route replay around the must-stub
    gate). ``GraphRun`` provides all of them; ``dispatch_node`` stamps
    ``step`` before each node body.
    """

    run_id: str
    step: int
    capabilities: Any
    fathom: Any
    is_replay: bool


class ToolCallNodeConfig(BaseModel):
    """``NodeSpec.config`` schema for ``kind: tool`` (extra keys rejected).

    ``inputs`` maps tool argument names to run-state field names (read off
    the state at call time); ``static`` maps tool argument names to literal
    values baked into the IR. The two key sets must not overlap. ``out`` is
    the state field the sanitized tool output dict merges into.
    """

    model_config = ConfigDict(extra="forbid")

    tool: str
    inputs: dict[str, str] = Field(default_factory=dict)
    static: dict[str, Any] = Field(default_factory=dict)
    out: str = "tool_result"

    @model_validator(mode="after")
    def _no_overlapping_args(self) -> ToolCallNodeConfig:
        overlap = sorted(set(self.inputs) & set(self.static))
        if overlap:
            raise ValueError(f"tool args declared in both inputs and static: {overlap}")
        return self


class ToolCallNode(NodeBase):
    """Invoke a registered ``@tool`` through the nine-step pipeline.

    Reads the state fields named in ``config.inputs``, merges
    ``config.static`` literals, resolves ``config.tool`` against the run's
    graph registry, and returns ``{config.out: <sanitized output dict>}``
    for the loop's field-merge step (FR-11).
    """

    def __init__(self, *, config: ToolCallNodeConfig) -> None:
        self.config = config

    async def execute(
        self,
        state: PydanticState,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        # The Phase-1 ``ExecutionContext`` Protocol only pins ``run_id``;
        # narrow to the tool-call surface loudly (never silent defaults --
        # a wrong-but-plausible ``step=0``/``is_replay=False`` corrupts
        # provenance and skips replay routing).
        if not isinstance(ctx, ToolCallContext):
            raise AttributeError(
                "ToolCallNode requires an execution context with `run_id`, "
                "`step`, `capabilities`, `fathom`, and `is_replay` "
                "(GraphRun provides these); got " + type(ctx).__name__
            )
        run: Any = ctx
        registry: Any = getattr(getattr(run, "graph", None), "registry", None)
        if registry is None:
            raise StargraphRuntimeError(
                f"tool node cannot resolve {self.config.tool!r}: no tool registry "
                "wired on the run's graph (pass Graph(..., registry=ToolRegistry()))",
                tool_id=self.config.tool,
            )
        tool = registry.get_tool(self.config.tool)  # PluginLoadError when unknown

        args: dict[str, Any] = dict(self.config.static)
        for arg, field_name in self.config.inputs.items():
            if not hasattr(state, field_name):
                raise StargraphRuntimeError(
                    f"tool node input {arg!r} reads state field {field_name!r} "
                    "which does not exist on the run state",
                    tool_id=self.config.tool,
                )
            args[arg] = getattr(state, field_name)

        # ``tool_cassette`` is the args-keyed CassetteStore for tool replay
        # (distinct from the ``(node_id, step)``-keyed ``node_cassette``);
        # optional because live runs never consult it.
        run_ctx = RunContext(
            run_id=ctx.run_id,
            step=ctx.step,
            capabilities=ctx.capabilities,
            fathom=ctx.fathom,
            is_replay=ctx.is_replay,
            cassette=getattr(run, "tool_cassette", None),
        )
        result = await execute_tool(tool, args, run_ctx=run_ctx)
        return {self.config.out: result.output}
