# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.code -- ``kind: code``, generate-then-run Python (P3a).

ChainOfThought generates a self-contained Python script from the input
state field, then the script runs through ``std.python_exec@1`` via
:func:`stargraph.runtime.tool_exec.execute_tool` -- so the execution is
capability-gated (``tools:std:exec``, default-deny), sandboxed, replayed
through the tool cassette, and receipted as ``stargraph.tool-call`` /
``stargraph.tool-result`` provenance facts.

Outputs: ``code`` (the generated script), ``run_result`` (the
``python_exec`` envelope: stdout/stderr/exit_code/timed_out/truncated),
``verdict`` (``pass`` when exit_code == 0 and not timed out, else
``fail``) -- Fathom rules route on the verdict fact (fix-loops in P3b).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

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

__all__ = ["CodeNode", "CodeNodeConfig", "code_node_from_config"]

PYTHON_EXEC_TOOL_ID = "std.python_exec@1"

_CODE_INSTRUCTIONS = (
    "Write a complete, self-contained Python script that accomplishes the "
    "task. The script must print its result to stdout and exit 0 on "
    "success. Use only the standard library. Return only the code."
)

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9]*\n(.*?)\n?```\s*$", re.DOTALL)


def strip_code_fences(code: str) -> str:
    """Deterministically unwrap a single markdown code fence, if present."""
    match = _FENCE_RE.match(code.strip())
    return match.group(1) if match else code.strip()


class CodeNodeConfig(_PydanticBaseModel):
    """``NodeSpec.config`` schema for ``kind: code`` (extra keys rejected)."""

    model_config = ConfigDict(extra="forbid")

    input: str = "task"
    timeout_s: float = Field(default=30.0, gt=0, le=600)
    instructions: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None


class CodeNode(NodeBase):
    """Generate Python with CoT, run it through the tool pipeline."""

    def __init__(self, *, inner: NodeBase, config: CodeNodeConfig) -> None:
        self._inner = inner
        self.config = config

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        if not isinstance(ctx, ToolCallContext):
            raise AttributeError(
                "CodeNode requires an execution context with `run_id`, "
                "`step`, `capabilities`, `fathom`, and `is_replay` "
                "(GraphRun provides these); got " + type(ctx).__name__
            )
        run: Any = ctx
        registry: Any = getattr(getattr(run, "graph", None), "registry", None)
        if registry is None:
            raise StargraphRuntimeError(
                f"code node cannot resolve {PYTHON_EXEC_TOOL_ID!r}: no tool "
                "registry wired on the run's graph "
                "(pass Graph(..., registry=ToolRegistry()))",
                tool_id=PYTHON_EXEC_TOOL_ID,
            )
        tool = registry.get_tool(PYTHON_EXEC_TOOL_ID)

        generated = await self._inner.execute(state, ctx)
        code = strip_code_fences(str(generated.get("code", "")))
        if not code:
            raise StargraphRuntimeError(
                "code node generated an empty script",
                kind="code",
            )

        run_ctx = RunContext(
            run_id=ctx.run_id,
            step=ctx.step,
            capabilities=ctx.capabilities,
            fathom=ctx.fathom,
            is_replay=ctx.is_replay,
            cassette=getattr(run, "tool_cassette", None),
        )
        result = await execute_tool(
            tool, {"code": code, "timeout_s": self.config.timeout_s}, run_ctx=run_ctx
        )
        envelope: dict[str, Any] = result.output
        ok = int(envelope.get("exit_code", 1)) == 0 and not envelope.get("timed_out")
        return {
            "code": code,
            "run_result": envelope,
            "verdict": "pass" if ok else "fail",
        }


def code_node_from_config(spec: NodeSpec) -> CodeNode:
    """Build a :class:`CodeNode` from ``NodeSpec.config`` (``kind: code``)."""
    try:
        cfg = CodeNodeConfig.model_validate(spec.config)
    except _PydanticValidationError as e:
        raise IRValidationError(f"code node {spec.id!r}: invalid config: {e}") from e
    if not cfg.input.isidentifier():
        raise IRValidationError(
            f"code node {spec.id!r}: input {cfg.input!r} must be a valid "
            "identifier (it becomes a DSPy signature field)"
        )

    dspy_config: dict[str, Any] = {
        "signature": f"{cfg.input} -> code",
        "module": "cot",
        "instructions": cfg.instructions or _CODE_INSTRUCTIONS,
    }
    if cfg.model is not None:
        dspy_config["model"] = cfg.model
    if cfg.api_base is not None:
        dspy_config["api_base"] = cfg.api_base
    if cfg.api_key_env is not None:
        dspy_config["api_key_env"] = cfg.api_key_env

    from stargraph.nodes.dspy import dspy_node_from_config

    inner = dspy_node_from_config(spec.model_copy(update={"config": dspy_config}))
    return CodeNode(inner=inner, config=cfg)
