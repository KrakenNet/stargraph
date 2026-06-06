# SPDX-License-Identifier: Apache-2.0
"""ReactSkill -- think -> act -> observe tool-loop subgraph (design §3.9).

Phase-2 scope (FR-25, AC-7.5, AC-10.4): a real iteration loop where
``_think`` calls an injected LLM-shaped callable (production wiring to
the engine model registry lands separately), ``_act`` dispatches a
native function-calling tool dict ``{"name": str, "arguments": dict}``
through the constructor-supplied tool table (NO regex parsing), and
``_observe`` appends a :class:`ReactStep` to the state trajectory.

Termination obeys (in priority order):

1. ``state.done`` flipped True by ``_think`` (final answer reached).
2. ``state.error_budget`` exhausted (decremented on tool exception).
3. ``self.max_steps`` reached (manifest-level wall-clock cap).

Phase 3 moves the loop under :class:`stargraph.nodes.SubGraphNode` driven
by the LangGraph IR referenced via :attr:`Skill.subgraph`; the in-skill
``run`` here is the deterministic shim the tests pin.
"""

from __future__ import annotations

from collections.abc import Callable  # noqa: TC003 - pydantic field type, needed at runtime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from stargraph.errors import StargraphRuntimeError
from stargraph.skills.base import Skill, SkillKind

__all__ = [
    "ReactSkill",
    "ReactState",
    "ReactStep",
    "ToolCallRecord",
]


class ToolCallRecord(BaseModel):
    """Native function-calling tool invocation record (design §3.9).

    Mirrors the shape engine FR-24 dispatchers consume: ``name`` is the
    fully-qualified tool id, ``arguments`` is the validated kwargs
    dict, ``result`` carries the tool's return value, and ``error``
    carries the str-formatted exception when the dispatch raised.
    """

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None


class ReactStep(BaseModel):
    """Single think -> act -> observe trajectory entry (design §3.9)."""

    thought: str
    tool_call: ToolCallRecord | None = None
    observation: str | None = None


class ReactState(BaseModel):
    """ReAct loop state (design §3.9, FR-25).

    Engine ``SubGraphNode`` boundary translator (FR-23) honors these as
    the declared output channels for :class:`ReactSkill`. ``done`` and
    ``error_budget`` drive termination alongside ``max_steps`` (carried
    on the :class:`ReactSkill` manifest, not in state). ``step_index``
    counts completed think -> act -> observe iterations and is the
    surface ``test_max_steps_termination`` pins.
    """

    trajectory: list[ReactStep] = Field(default_factory=list[ReactStep])
    tool_calls: list[ToolCallRecord] = Field(default_factory=list[ToolCallRecord])
    done: bool = False
    error_budget: int = 3
    final_answer: str | None = None
    step_index: int = 0


class ReactSkill(Skill):
    """ReAct tool-loop skill (FR-25, AC-7.5, AC-10.4).

    Subgraph: ``_think`` (injected LLM callable -> reasoning + optional
    ``tool_call``) -> ``_act`` (native function-call dispatch through the
    constructor-supplied ``tools`` table; FR-24) -> ``_observe``
    (trajectory + tool_calls append). Phase 3 moves the three nodes
    under :class:`stargraph.nodes.SubGraphNode`.

    Constructor wiring (POC; production wires through the engine
    registries):

    * ``llm_stub`` -- callable ``(state, ctx) -> dict`` returning
      ``{"reasoning": str, "tool_call": dict | None, "done": bool,
      "final_answer": str | None}``. Phase 3 swaps in the model
      registry; the dict shape is the contract the tests pin.
    * ``tool_impls`` -- ``dict[str, Callable]`` mapping
      ``tool_call["name"]`` to a callable invoked with
      ``**tool_call["arguments"]``. NO regex parsing -- attribute
      access on the dict. Distinct from :attr:`Skill.tools` which is
      the list of declared tool ids on the manifest.
    * ``max_steps`` -- wall-clock cap (default 10).

    Parent-state writes are restricted to :class:`ReactState` field
    names per FR-23 (engine enforces at boundary translation).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: SkillKind = SkillKind.agent
    state_schema: type[BaseModel] = ReactState
    max_steps: int = 10
    llm_stub: Callable[..., dict[str, Any]] | None = None
    tool_impls: dict[str, Callable[..., Any]] = Field(default_factory=dict)

    async def _think(
        self,
        state: ReactState,
        ctx: Any | None = None,
    ) -> dict[str, Any]:
        """Invoke the injected LLM-shaped callable (POC).

        Returns the raw dict ``{"reasoning", "tool_call", "done",
        "final_answer"}`` exactly as the callable produced it -- the
        loop body in :meth:`run` interprets it. ``ctx`` is forwarded
        verbatim to the callable so production wiring can route through
        the engine model registry without changing the call shape.
        """
        if self.llm_stub is None:
            msg = (
                "ReactSkill._think requires an llm_stub callable; "
                "production wiring through the engine model registry "
                "is deferred to Phase 3 (FR-25)."
            )
            raise StargraphRuntimeError(msg)
        return self.llm_stub(state, ctx)

    async def _act(self, tool_call: dict[str, Any]) -> ToolCallRecord:
        """Dispatch a native function-calling tool dict (FR-24).

        ``tool_call`` is a dict with ``name`` (str) and ``arguments``
        (dict) keys -- the same shape engine tool dispatchers consume.
        Lookups go through ``self.tool_impls`` by name; missing tools
        and callee exceptions are captured into the returned
        :class:`ToolCallRecord` ``error`` field (the loop body in
        :meth:`run` decrements ``error_budget`` on truthy ``error``).
        NO regex parsing -- attribute access on the dict only.
        """
        name = tool_call["name"]
        arguments = tool_call.get("arguments", {})
        record = ToolCallRecord(name=name, arguments=dict(arguments))
        fn = self.tool_impls.get(name)
        if fn is None:
            record.error = f"unknown tool: {name!r}"
            return record
        try:
            record.result = fn(**arguments)
        except Exception as exc:
            # Tool exceptions are captured into the record so the loop
            # can decrement error_budget instead of bubbling up; this
            # is the AC-10.4 contract.
            record.error = f"{type(exc).__name__}: {exc}"
        return record

    async def _observe(
        self,
        state: ReactState,
        step: ReactStep,
    ) -> ReactState:
        """Append the completed step to the trajectory (design §3.9).

        Also mirrors any non-None ``step.tool_call`` onto
        ``state.tool_calls`` so consumers can scan the dispatch log
        independently of trajectory ordering.
        """
        new_trajectory = [*state.trajectory, step]
        new_tool_calls = state.tool_calls
        if step.tool_call is not None:
            new_tool_calls = [*new_tool_calls, step.tool_call]
        return state.model_copy(
            update={
                "trajectory": new_trajectory,
                "tool_calls": new_tool_calls,
            }
        )

    async def run(
        self,
        state: ReactState,
        ctx: Any | None = None,
    ) -> ReactState:
        """Drive think -> act -> observe until termination.

        Termination order (whichever fires first):

        1. ``done`` flag set by ``_think`` (final answer reached).
        2. ``error_budget`` reached zero (tool exception streak).
        3. ``max_steps`` reached (manifest cap).
        """
        current = state
        for _ in range(self.max_steps):
            if current.done or current.error_budget <= 0:
                break

            think_out = await self._think(current, ctx)
            thought = str(think_out.get("reasoning", ""))
            tool_call_dict = think_out.get("tool_call")
            think_done = bool(think_out.get("done", False))
            final_answer = think_out.get("final_answer")

            tool_record: ToolCallRecord | None = None
            if tool_call_dict is not None:
                tool_record = await self._act(tool_call_dict)

            step = ReactStep(
                thought=thought,
                tool_call=tool_record,
                observation=(
                    None if tool_record is None else (tool_record.error or str(tool_record.result))
                ),
            )
            current = await self._observe(current, step)

            updates: dict[str, Any] = {"step_index": current.step_index + 1}
            if tool_record is not None and tool_record.error is not None:
                updates["error_budget"] = current.error_budget - 1
            if think_done:
                updates["done"] = True
                if final_answer is not None:
                    updates["final_answer"] = str(final_answer)
            current = current.model_copy(update=updates)

        return current
