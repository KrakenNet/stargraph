# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.react -- ``kind: react``, a tool-loop agent node (P3a).

Wraps ``dspy.ReAct`` over an **allowlist** of registry tools. Every tool
invocation the agent makes goes through
:func:`stargraph.runtime.tool_exec.execute_tool` -- the same nine-step
pipeline as ``kind: tool`` (capability gate, replay routing,
``stargraph.tool-call`` / ``stargraph.tool-result`` provenance facts,
sanitization) -- never around it. The agent picks *tools*; it never
picks the next node: outputs are ``answer`` + ``tool_trace`` state
fields, and Fathom rules route on the mirrored facts.

Async by construction: the node awaits ``module.acall`` so the bridged
registry tools (async ``execute_tool`` closures) run on the node's event
loop instead of blocking it.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel as _PydanticBaseModel
from pydantic import ConfigDict, Field
from pydantic import ValidationError as _PydanticValidationError

from stargraph.errors import IRValidationError, StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.nodes.tool_call import ToolCallContext
from stargraph.runtime.tool_exec import RunContext, execute_tool

if TYPE_CHECKING:
    from pydantic import BaseModel

    from stargraph.ir._models import NodeSpec

__all__ = ["ReactAgentNode", "ReactNodeConfig", "react_node_from_config"]

_REACT_INSTRUCTIONS = (
    "Answer the question using the available tools. Call tools to gather "
    "facts; do not invent tool results. Give a direct, complete answer."
)

# Same logger DSPyNode trips for the FR-6 force-loud seam.
_FALLBACK_LOGGER = logging.getLogger("dspy.adapters.json_adapter")


class ReactNodeConfig(_PydanticBaseModel):
    """``NodeSpec.config`` schema for ``kind: react`` (extra keys rejected)."""

    model_config = ConfigDict(extra="forbid")

    tools: list[str] = Field(min_length=1)  # registry tool ids (allowlist)
    input: str = "question"
    max_iters: int = Field(default=8, ge=1, le=64)
    instructions: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None


def _tool_callable_name(namespace: str, name: str) -> str:
    """LM-facing tool name: ``<ns>_<name>``, sanitized to an identifier."""
    return re.sub(r"[^0-9A-Za-z_]", "_", f"{namespace}_{name}")


def trajectory_to_trace(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    """Compress a dspy ReAct trajectory into the ``tool_trace`` output.

    The trajectory dict carries ``tool_name_<i>`` / ``tool_args_<i>`` /
    ``observation_<i>`` per iteration; the terminal ``finish``
    pseudo-tool is dropped (it is loop plumbing, not a tool call).
    """
    trace: list[dict[str, Any]] = []
    index = 0
    while f"tool_name_{index}" in trajectory:
        name = str(trajectory[f"tool_name_{index}"])
        if name != "finish":
            trace.append(
                {
                    "tool": name,
                    "args": trajectory.get(f"tool_args_{index}") or {},
                    "observation": str(trajectory.get(f"observation_{index}", "")),
                }
            )
        index += 1
    return trace


class ReactAgentNode(NodeBase):
    """``dspy.ReAct`` over capability-gated registry tools.

    Built by :func:`react_node_from_config`; holds config plus the
    (optional) per-node ``dspy.LM`` handle. The ReAct module itself is
    constructed per ``execute`` because the bridged tool closures must
    capture the run context (``run_id``/``step``/``capabilities``) of
    *this* dispatch.
    """

    def __init__(self, *, config: ReactNodeConfig, lm: Any) -> None:
        self.config = config
        self._lm = lm

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(ctx, ToolCallContext):
            raise AttributeError(
                "ReactAgentNode requires an execution context with `run_id`, "
                "`step`, `capabilities`, `fathom`, and `is_replay` "
                "(GraphRun provides these); got " + type(ctx).__name__
            )
        run: Any = ctx
        registry: Any = getattr(getattr(run, "graph", None), "registry", None)
        if registry is None:
            raise StargraphRuntimeError(
                "react node cannot resolve tools: no tool registry wired on "
                "the run's graph (pass Graph(..., registry=ToolRegistry()))",
                tool_ids=list(self.config.tools),
            )
        if not hasattr(state, self.config.input):
            raise StargraphRuntimeError(
                f"react node reads state field {self.config.input!r} "
                "which does not exist on the run state",
            )

        run_ctx = RunContext(
            run_id=ctx.run_id,
            step=ctx.step,
            capabilities=ctx.capabilities,
            fathom=ctx.fathom,
            is_replay=ctx.is_replay,
            cassette=getattr(run, "tool_cassette", None),
        )

        import dspy  # type: ignore[import-untyped]

        dspy_tools: list[Any] = []
        for tool_id in self.config.tools:
            tool = registry.get_tool(tool_id)  # PluginLoadError when unknown
            spec: Any = tool.spec

            def _bridge(_tool: Any = tool) -> Any:
                async def _call(**kwargs: Any) -> Any:
                    result = await execute_tool(_tool, dict(kwargs), run_ctx=run_ctx)
                    return result.output

                return _call

            properties = cast("dict[str, Any]", dict(spec.input_schema.get("properties") or {}))
            dspy_tools.append(
                dspy.Tool(
                    _bridge(),
                    name=_tool_callable_name(spec.namespace, spec.name),
                    desc=spec.description,
                    args=properties,
                )
            )

        signature_factory: Any = dspy.Signature
        signature = signature_factory(
            f"{self.config.input} -> answer",
            self.config.instructions or _REACT_INSTRUCTIONS,
        )
        module: Any = dspy.ReAct(signature, tools=dspy_tools, max_iters=self.config.max_iters)
        if self._lm is not None:
            module.set_lm(self._lm)

        from stargraph.adapters.dspy import FALLBACK_NEEDLE

        try:
            prediction: Any = await module.acall(
                **{self.config.input: getattr(state, self.config.input)}
            )
        except StargraphRuntimeError:
            raise  # tool-pipeline failures (capability denial, ...) pass through
        except Exception as err:
            # Same force-loud seam as DSPyNode.acall: trip the filter, and
            # if it is absent raise AdapterFallbackError explicitly.
            _FALLBACK_LOGGER.warning(FALLBACK_NEEDLE)
            from stargraph.errors import AdapterFallbackError

            raise AdapterFallbackError(FALLBACK_NEEDLE, adapter="dspy") from err

        trajectory = cast("dict[str, Any]", dict(getattr(prediction, "trajectory", {}) or {}))
        return {
            "answer": str(getattr(prediction, "answer", "")),
            "tool_trace": trajectory_to_trace(trajectory),
        }


def react_node_from_config(spec: NodeSpec) -> ReactAgentNode:
    """Build a :class:`ReactAgentNode` from ``NodeSpec.config`` (``kind: react``).

    Mirrors :func:`~stargraph.nodes.dspy.dspy_node_from_config`: validates
    config, enforces the no-LM-anywhere loud failure at build time, and
    installs the FR-6 loud-fallback filter.
    """
    try:
        cfg = ReactNodeConfig.model_validate(spec.config)
    except _PydanticValidationError as e:
        raise IRValidationError(f"react node {spec.id!r}: invalid config: {e}") from e
    if not cfg.input.isidentifier():
        raise IRValidationError(
            f"react node {spec.id!r}: input {cfg.input!r} must be a valid "
            "identifier (it becomes a DSPy signature field)"
        )

    import dspy  # type: ignore[import-untyped]

    settings: Any = dspy.settings
    if cfg.model is None and settings.lm is None:
        raise IRValidationError(
            f"react node {spec.id!r}: no LM configured -- pass --lm-url/--lm-model "
            "(or call dspy.configure(lm=...)), or set config.model for a per-node LM"
        )

    lm: Any = None
    if cfg.model is not None:
        import os

        lm_kwargs: dict[str, Any] = {}
        if cfg.api_base is not None:
            lm_kwargs["api_base"] = cfg.api_base
        if cfg.api_key_env is not None:
            lm_kwargs["api_key"] = os.environ.get(cfg.api_key_env, "")
        lm = dspy.LM(cfg.model, **lm_kwargs)

    from stargraph.adapters.dspy import install_loud_fallback_filter

    install_loud_fallback_filter()
    return ReactAgentNode(config=cfg, lm=lm)
