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

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stargraph.errors import StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.runtime.tool_exec import RunContext, execute_tool

if TYPE_CHECKING:
    from pydantic import BaseModel as PydanticState

__all__ = ["ToolCallNode", "ToolCallNodeConfig"]


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
        # ``ExecutionContext`` is the minimal protocol; the live driver
        # passes the concrete ``GraphRun``, which carries graph/capabilities/
        # fathom. Read structurally so standalone contexts stay usable.
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

        run_ctx = RunContext(
            run_id=getattr(run, "run_id", "") or "unknown-run",
            step=int(getattr(run, "step", 0) or 0),
            capabilities=getattr(run, "capabilities", None),
            fathom=getattr(run, "fathom", None),
            is_replay=bool(getattr(run, "is_replay", False)),
            cassette=getattr(run, "cassette", None),
        )
        result = await execute_tool(tool, args, run_ctx=run_ctx)
        return {self.config.out: result.output}
