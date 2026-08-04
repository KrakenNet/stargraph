# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.prebuilt -- signature-preset DSPy nodes (P3a).

Six short kinds (``reason`` / ``summarize`` / ``classify`` / ``extract``
/ ``judge`` / ``plan``) that wrap :func:`~stargraph.nodes.dspy.
dspy_node_from_config` with a curated signature, instructions, and a
deterministic post-processor. The contract that matters for routing:
**the LLM never picks the next node** -- these nodes emit standardized
state fields (``verdict``, ``confidence``, ``score``, ...) and Fathom
rules route on the mirrored facts (moat-roadmap hard rule).

Standard output fields per kind (everything else the module produces is
dropped -- the run-state merge is ``model_copy(update=...)``, so an
unwhitelisted key would silently pollute state):

========== ==========================================================
kind       outputs
========== ==========================================================
reason     ``answer``, ``rationale``
summarize  ``summary``
classify   ``verdict`` (one of ``labels``), ``confidence`` (float-as-str)
extract    the declared ``fields`` keys, typed
judge      ``verdict`` (``pass``/``fail``), ``score`` (float-as-str),
           ``rationale``
plan       ``tasks`` (list of str)
========== ==========================================================

``confidence``/``score`` are emitted as ``str(float)`` because Mirror
facts assert string values -- a ``str`` state field routes cleanly.

Config keys shared by every kind: ``input`` (the state field read;
per-kind default), ``instructions`` (overrides the preset),
``model`` / ``api_base`` / ``api_key_env`` (per-node LM override,
passed straight through to :class:`~stargraph.nodes.dspy.DSPyNodeConfig`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic import ValidationError as _PydanticValidationError

from stargraph.errors import IRValidationError, StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from collections.abc import Callable

    from stargraph.ir._models import NodeSpec

__all__ = ["PREBUILT_KINDS", "PrebuiltNode", "build_prebuilt"]


class PrebuiltNode(NodeBase):
    """A DSPy-backed node with a deterministic output post-processor.

    Delegates execution to the wrapped :class:`~stargraph.nodes.dspy.
    DSPyNode`, then normalizes/whitelists the outputs so the run-state
    merge only ever sees the kind's documented fields.
    """

    def __init__(
        self,
        *,
        inner: NodeBase,
        post: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._inner = inner
        self._post = post

    async def execute(
        self,
        state: BaseModel,
        ctx: ExecutionContext,
    ) -> dict[str, Any]:
        return self._post(await self._inner.execute(state, ctx))


# ---------------------------------------------------------------- configs


class _LMConfig(BaseModel):
    """Keys every prebuilt kind shares (extra keys rejected)."""

    model_config = ConfigDict(extra="forbid")

    instructions: str | None = None
    model: str | None = None
    api_base: str | None = None
    api_key_env: str | None = None


class ReasonConfig(_LMConfig):
    input: str = "question"


class SummarizeConfig(_LMConfig):
    input: str = "text"


class ClassifyConfig(_LMConfig):
    labels: list[str] = Field(min_length=2)
    input: str = "text"


class ExtractConfig(_LMConfig):
    fields: dict[str, str] = Field(min_length=1)
    input: str = "text"


class JudgeConfig(_LMConfig):
    rubric: str = Field(min_length=1)
    input: str = "content"


class PlanConfig(_LMConfig):
    input: str = "goal"


# ------------------------------------------------------------- synthesis

_REASON_INSTRUCTIONS = "Answer the question. Think step by step; be precise, complete, and factual."
_SUMMARIZE_INSTRUCTIONS = (
    "Write a faithful, concise summary of the text. Preserve key facts and "
    "numbers; do not add information that is not in the text."
)
_PLAN_INSTRUCTIONS = (
    "Break the goal into a short, ordered list of concrete tasks. Each task "
    "must be independently checkable and small enough to complete in one step."
)

#: Allowed value types for ``extract`` fields, mapped to the DSPy inline
#: signature annotation each compiles to.
_EXTRACT_TYPES: dict[str, str] = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "list": "list[str]",
}


def _check_field_name(name: str, *, spec: NodeSpec, what: str) -> str:
    if not name.isidentifier():
        raise IRValidationError(
            f"{spec.kind} node {spec.id!r}: {what} {name!r} must be a valid "
            "identifier (it becomes a DSPy signature field)"
        )
    return name


def classify_instructions(labels: list[str]) -> str:
    """Preset instructions for ``classify`` (pure; unit-testable)."""
    return (
        "Classify the text into exactly one of these labels: "
        + ", ".join(labels)
        + ". Respond with the chosen label verbatim and a confidence "
        "between 0.0 and 1.0."
    )


def judge_instructions(rubric: str) -> str:
    """Preset instructions for ``judge`` (pure; unit-testable)."""
    return (
        "Judge the content against this rubric:\n"
        + rubric
        + "\nverdict must be exactly 'pass' or 'fail'; score is a number "
        "between 0.0 and 1.0."
    )


def extract_signature(input_field: str, fields: dict[str, str], *, spec: NodeSpec) -> str:
    """Compile the ``extract`` field map to a DSPy inline signature."""
    parts: list[str] = []
    for name, type_name in fields.items():
        _check_field_name(name, spec=spec, what="fields key")
        annotation = _EXTRACT_TYPES.get(type_name)
        if annotation is None:
            raise IRValidationError(
                f"extract node {spec.id!r}: fields.{name}: unknown type "
                f"{type_name!r}; expected one of {sorted(_EXTRACT_TYPES)}"
            )
        parts.append(f"{name}: {annotation}")
    return f"{input_field} -> " + ", ".join(parts)


# -------------------------------------------------------- post-processors


def _as_score_str(value: Any, *, kind: str, field: str) -> str:
    """Coerce a model-produced confidence/score to a clamped float-as-str."""
    try:
        number = float(value)
    except (TypeError, ValueError) as e:
        raise StargraphRuntimeError(
            f"{kind} node produced a non-numeric {field}",
            kind=kind,
            field=field,
            value=repr(value),
        ) from e
    return str(min(1.0, max(0.0, number)))


def post_reason(out: dict[str, Any]) -> dict[str, Any]:
    return {
        "answer": str(out.get("answer", "")),
        "rationale": str(out.get("reasoning", "")),
    }


def post_summarize(out: dict[str, Any]) -> dict[str, Any]:
    return {"summary": str(out.get("summary", ""))}


def post_classify(labels: list[str]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    canonical = {label.strip().lower(): label for label in labels}

    def _post(out: dict[str, Any]) -> dict[str, Any]:
        raw = str(out.get("label", "")).strip()
        label = canonical.get(raw.lower().rstrip("."))
        if label is None:
            raise StargraphRuntimeError(
                "classify node produced a label outside the configured set",
                kind="classify",
                value=raw,
                labels=labels,
            )
        return {
            "verdict": label,
            "confidence": _as_score_str(out.get("confidence"), kind="classify", field="confidence"),
        }

    return _post


def post_extract(fields: dict[str, str]) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def _post(out: dict[str, Any]) -> dict[str, Any]:
        return {name: out.get(name) for name in fields}

    return _post


def post_judge(out: dict[str, Any]) -> dict[str, Any]:
    raw = str(out.get("verdict", "")).strip().lower().rstrip(".")
    if raw not in ("pass", "fail"):
        raise StargraphRuntimeError(
            "judge node produced a verdict other than pass/fail",
            kind="judge",
            value=raw,
        )
    return {
        "verdict": raw,
        "score": _as_score_str(out.get("score"), kind="judge", field="score"),
        "rationale": str(out.get("reasoning", "")),
    }


def post_plan(out: dict[str, Any]) -> dict[str, Any]:
    raw: Any = out.get("tasks")
    if not isinstance(raw, list):
        raise StargraphRuntimeError(
            "plan node produced a non-list tasks output",
            kind="plan",
            value=repr(raw),
        )
    return {"tasks": [str(task) for task in cast("list[Any]", raw)]}


# ---------------------------------------------------------------- builders


def _validate_config(model: type[BaseModel], spec: NodeSpec) -> Any:
    try:
        return model.model_validate(spec.config)
    except _PydanticValidationError as e:
        raise IRValidationError(f"{spec.kind} node {spec.id!r}: invalid config: {e}") from e


def _dspy_inner(
    spec: NodeSpec,
    cfg: _LMConfig,
    *,
    signature: str,
    module: str,
    default_instructions: str,
) -> NodeBase:
    """Synthesize the ``kind: dspy`` config and build the wrapped node."""
    config: dict[str, Any] = {
        "signature": signature,
        "module": module,
        "instructions": cfg.instructions or default_instructions,
    }
    if cfg.model is not None:
        config["model"] = cfg.model
    if cfg.api_base is not None:
        config["api_base"] = cfg.api_base
    if cfg.api_key_env is not None:
        config["api_key_env"] = cfg.api_key_env

    from stargraph.nodes.dspy import dspy_node_from_config

    return dspy_node_from_config(spec.model_copy(update={"config": config}))


def _build_reason(spec: NodeSpec) -> NodeBase:
    cfg = cast("ReasonConfig", _validate_config(ReasonConfig, spec))
    _check_field_name(cfg.input, spec=spec, what="input")
    inner = _dspy_inner(
        spec,
        cfg,
        signature=f"{cfg.input} -> answer",
        module="cot",
        default_instructions=_REASON_INSTRUCTIONS,
    )
    return PrebuiltNode(inner=inner, post=post_reason)


def _build_summarize(spec: NodeSpec) -> NodeBase:
    cfg = cast("SummarizeConfig", _validate_config(SummarizeConfig, spec))
    _check_field_name(cfg.input, spec=spec, what="input")
    inner = _dspy_inner(
        spec,
        cfg,
        signature=f"{cfg.input} -> summary",
        module="cot",
        default_instructions=_SUMMARIZE_INSTRUCTIONS,
    )
    return PrebuiltNode(inner=inner, post=post_summarize)


def _build_classify(spec: NodeSpec) -> NodeBase:
    cfg = cast("ClassifyConfig", _validate_config(ClassifyConfig, spec))
    _check_field_name(cfg.input, spec=spec, what="input")
    labels = [label.strip() for label in cfg.labels]
    if len({label.lower() for label in labels}) != len(labels):
        raise IRValidationError(
            f"classify node {spec.id!r}: labels must be case-insensitively unique"
        )
    inner = _dspy_inner(
        spec,
        cfg,
        signature=f"{cfg.input} -> label: str, confidence: float",
        module="predict",
        default_instructions=classify_instructions(labels),
    )
    return PrebuiltNode(inner=inner, post=post_classify(labels))


def _build_extract(spec: NodeSpec) -> NodeBase:
    cfg = cast("ExtractConfig", _validate_config(ExtractConfig, spec))
    _check_field_name(cfg.input, spec=spec, what="input")
    inner = _dspy_inner(
        spec,
        cfg,
        signature=extract_signature(cfg.input, cfg.fields, spec=spec),
        module="predict",
        default_instructions=(
            "Extract the requested fields from the text. Take values verbatim "
            "from the text; do not infer or invent."
        ),
    )
    return PrebuiltNode(inner=inner, post=post_extract(cfg.fields))


def _build_judge(spec: NodeSpec) -> NodeBase:
    cfg = cast("JudgeConfig", _validate_config(JudgeConfig, spec))
    _check_field_name(cfg.input, spec=spec, what="input")
    inner = _dspy_inner(
        spec,
        cfg,
        signature=f"{cfg.input} -> verdict: str, score: float",
        module="cot",
        default_instructions=judge_instructions(cfg.rubric),
    )
    return PrebuiltNode(inner=inner, post=post_judge)


def _build_plan(spec: NodeSpec) -> NodeBase:
    cfg = cast("PlanConfig", _validate_config(PlanConfig, spec))
    _check_field_name(cfg.input, spec=spec, what="input")
    inner = _dspy_inner(
        spec,
        cfg,
        signature=f"{cfg.input} -> tasks: list[str]",
        module="cot",
        default_instructions=_PLAN_INSTRUCTIONS,
    )
    return PrebuiltNode(inner=inner, post=post_plan)


_BUILDERS: dict[str, Callable[[NodeSpec], NodeBase]] = {
    "reason": _build_reason,
    "summarize": _build_summarize,
    "classify": _build_classify,
    "extract": _build_extract,
    "judge": _build_judge,
    "plan": _build_plan,
}

#: Short kinds this module provides (consumed by the node registry).
PREBUILT_KINDS: tuple[str, ...] = tuple(sorted(_BUILDERS))


def build_prebuilt(spec: NodeSpec) -> NodeBase:
    """Registry entry point: dispatch ``spec.kind`` to its preset builder."""
    return _BUILDERS[spec.kind](spec)
