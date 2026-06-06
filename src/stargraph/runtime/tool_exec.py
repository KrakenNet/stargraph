# SPDX-License-Identifier: Apache-2.0
"""Tool execution path -- the FR-24 / design §3.4.4 nine-step pipeline.

Centralizes every gate every Stargraph tool must traverse before its body
fires (and every fact every successful invocation must emit). The
public entry point :func:`execute_tool` is independent of the run loop;
the loop wires the call site once tool-bearing nodes land.

Steps (verbatim from design §3.4.4):

1. Validate ``args`` against ``tool.spec.input_schema`` (jsonschema draft 2020-12).
2. Check ``tool.spec.permissions`` against ``run_ctx.capabilities`` (NFR-7).
3. Replay routing: when ``run_ctx.is_replay`` and ``side_effects`` is
   ``write`` or ``external``:

   * ``must_stub`` / ``recorded_result`` -> return cassette entry, never
     invoke the tool body.
   * ``fail_loud`` -> raise :class:`stargraph.errors.ReplayError`.
4. Emit ``stargraph.tool-call`` fact via the Fathom adapter (provenance).
5. Invoke the tool body (skipped on replay-stub branch).
6. Validate the result against ``tool.spec.output_schema``.
7. Sanitize string leaves (HTML-escape + strip control chars + remove
   ``__system__`` markers) before returning to LM context.
8. Emit ``stargraph.tool-result`` fact (provenance).
9. Optionally emit ``stargraph.tokens-used`` when the tool returns a
   ``_tokens`` field with non-zero token counts.

The :class:`RunContext` dataclass here is a minimal shim -- it pins the
fields :func:`execute_tool` consults so callers can construct one
without dragging in the full :class:`stargraph.graph.GraphRun` graph
(useful in tests, replay engines, and CLI subcommands). The real
``GraphRun`` exposes the same attributes, so a coercion call site can
just pass ``run`` directly.
"""

from __future__ import annotations

import html
import inspect
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from jsonschema import Draft202012Validator

from stargraph.errors import (
    CapabilityError,
    IRValidationError,
    ReplayError,
    StargraphRuntimeError,
)
from stargraph.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    from stargraph.fathom import FathomAdapter
    from stargraph.ir._models import ToolSpec
    from stargraph.security.capabilities import Capabilities

__all__ = [
    "RunContext",
    "ToolResult",
    "execute_tool",
]


# ---------------------------------------------------------------------------
# Sanitization (mirrors stargraph.adapters.mcp).
# ---------------------------------------------------------------------------

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SYSTEM_MARKER_RE = re.compile(r"__system__|<\|im_start\|>|<\|im_end\|>", re.IGNORECASE)


def _sanitize_str(value: str) -> str:
    """HTML-escape, strip control chars, and remove ``__system__`` markers."""
    stripped = _CONTROL_CHARS_RE.sub("", value)
    escaped = html.escape(stripped, quote=True)
    return _SYSTEM_MARKER_RE.sub("", escaped)


def _sanitize(value: object) -> object:
    """Recursively sanitize all string leaves of a JSON-shaped payload."""
    if isinstance(value, str):
        return _sanitize_str(value)
    if isinstance(value, dict):
        return {k: _sanitize(v) for k, v in cast("dict[str, object]", value).items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in cast("list[object]", value)]
    return value


# ---------------------------------------------------------------------------
# Cassette store + run context shim.
# ---------------------------------------------------------------------------


@runtime_checkable
class CassetteStore(Protocol):
    """Replay cassette lookup contract (FR-21, NFR-8).

    ``get(tool_id, args)`` returns a previously-recorded result or
    ``None`` if no recording exists for that ``(tool_id, args_hash)``
    pair. The pipeline raises :class:`ReplayError` on cache miss when
    the policy demands a stub.
    """

    def get(self, tool_id: str, args: dict[str, Any]) -> dict[str, Any] | None: ...


@dataclass(slots=True)
class RunContext:
    """Minimal context object consumed by :func:`execute_tool`.

    Attributes mirror the subset of :class:`stargraph.graph.GraphRun` that
    tool execution actually needs. Defaults exist so test fixtures and
    standalone callers can construct contexts without wiring the full
    run loop.
    """

    run_id: str
    step: int = 0
    capabilities: Capabilities | None = None
    fathom: FathomAdapter | None = None
    is_replay: bool = False
    cassette: CassetteStore | None = None
    deployment: str = "dev"
    tokens_emitted: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


@dataclass(slots=True)
class ToolResult:
    """Outcome of a single :func:`execute_tool` invocation.

    ``output`` is the sanitized payload (dict). ``replayed`` is ``True``
    when the result came from a cassette rather than a fresh tool call.
    ``tokens`` carries the optional ``{prompt, completion, total}`` map
    when the tool reported token consumption (step 9).
    """

    output: dict[str, Any]
    replayed: bool = False
    tokens: dict[str, int] | None = None


# ---------------------------------------------------------------------------
# Pipeline implementation.
# ---------------------------------------------------------------------------


def _validate(
    payload: dict[str, Any],
    schema: dict[str, object],
    *,
    kind: str,
    tool_id: str,
) -> None:
    """Validate ``payload`` against ``schema`` (Draft 2020-12) or raise."""
    validator = Draft202012Validator(schema)  # pyright: ignore[reportUnknownArgumentType]
    errors = sorted(validator.iter_errors(payload), key=lambda e: e.path)  # pyright: ignore[reportUnknownArgumentType, reportUnknownLambdaType, reportUnknownMemberType, reportUnknownVariableType]
    if errors:
        first = errors[0]  # pyright: ignore[reportUnknownVariableType]
        raise IRValidationError(
            f"tool {tool_id!r} {kind} schema validation failed: {first.message}",  # pyright: ignore[reportUnknownMemberType]
            tool_id=tool_id,
            violation=f"tool-{kind}-schema",
            schema_path=list(first.absolute_path),  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
        )


def _emit_fact(
    fathom: FathomAdapter | None,
    template: str,
    slots: dict[str, Any],
    *,
    run_id: str,
    step: int,
    origin: str,
) -> None:
    """Emit a Fathom fact with a minimal provenance bundle.

    No-op when ``fathom`` is ``None`` -- contexts without a Fathom
    adapter (CLI standalone, replay reconstruction) still satisfy the
    pipeline shape; provenance is required only when an adapter is wired.
    """
    if fathom is None:
        return
    fathom.assert_with_provenance(
        template,
        slots,
        {
            "origin": origin,
            "source": "tool-exec",
            "run_id": run_id,
            "step": step,
            "confidence": Decimal("1.0"),
            "timestamp": datetime.now(UTC),
        },
    )


def _replay_route(
    spec: ToolSpec,
    args: dict[str, Any],
    run_ctx: RunContext,
) -> dict[str, Any] | None:
    """Step 3: resolve the replay branch.

    Returns the recorded payload when the tool must be stubbed, ``None``
    when execution should proceed natively. Raises :class:`ReplayError`
    on ``fail_loud`` policy or on cache miss for stub-required tools.
    """
    if not run_ctx.is_replay:
        return None
    if spec.side_effects not in (SideEffects.write, SideEffects.external):
        # ``none`` / ``read`` re-execute natively per design §3.4.4 step 3
        # (replay-routing only applies to the write/external side-effect set).
        return None

    if spec.replay_policy == ReplayPolicy.fail_loud:
        raise ReplayError(
            f"tool {spec.name!r} forbidden on replay (fail-loud policy)",
            run_id=run_ctx.run_id,
            tool_id=spec.name,
            side_effects=str(spec.side_effects),
        )

    if run_ctx.cassette is None:
        raise ReplayError(
            f"tool {spec.name!r} requires a cassette on replay but none is wired",
            run_id=run_ctx.run_id,
            tool_id=spec.name,
            side_effects=str(spec.side_effects),
        )
    recorded = run_ctx.cassette.get(spec.name, args)
    if recorded is None:
        raise ReplayError(
            f"tool {spec.name!r} has no recorded cassette entry for the given args",
            run_id=run_ctx.run_id,
            tool_id=spec.name,
            side_effects=str(spec.side_effects),
        )
    return dict(recorded)


def _extract_tokens(payload: dict[str, Any]) -> dict[str, int] | None:
    """Pull a ``_tokens`` block off a tool's payload (step 9).

    Convention: tools that report consumption attach a ``_tokens`` key
    with at least ``{prompt, completion, total}`` integer fields. The
    payload is *not* stripped of the marker -- callers may want to
    inspect it -- but the marker is filtered out of LM-bound output by
    convention upstream when needed.
    """
    raw = payload.get("_tokens")
    if not isinstance(raw, dict):
        return None
    out: dict[str, int] = {}
    for k, v in cast("dict[Any, Any]", raw).items():
        if isinstance(k, str) and isinstance(v, int):
            out[k] = v
    return out or None


async def execute_tool(
    tool: Any,
    args: dict[str, Any],
    *,
    run_ctx: RunContext,
) -> ToolResult:
    """Run ``tool`` through the FR-24 / design §3.4.4 nine-step pipeline.

    ``tool`` is any callable whose ``.spec`` attribute is a
    :class:`stargraph.ir._models.ToolSpec` (the contract produced by
    :func:`stargraph.tool`). Sync and async tool bodies are both
    accepted -- async ones are awaited; sync returns are used directly.

    Returns a :class:`ToolResult` whose ``output`` is the sanitized
    payload. ``replayed`` is ``True`` when step 3 satisfied the call
    from a cassette. ``tokens`` carries the ``_tokens`` block when the
    tool reported usage.
    """
    spec_obj: Any = getattr(tool, "spec", None)
    if spec_obj is None:
        raise StargraphRuntimeError(
            "execute_tool requires a tool with a `.spec` ToolSpec attribute",
            tool=type(tool).__name__,
        )
    spec: ToolSpec = spec_obj

    # Step 1: input-schema validation.
    _validate(args, spec.input_schema, kind="input", tool_id=spec.name)

    # Step 2: capability gate (NFR-7 default-deny).
    caps = run_ctx.capabilities
    if spec.permissions and (caps is None or not caps.check(spec)):
        raise CapabilityError(
            f"tool {spec.name!r} requires permissions not granted",
            capability=",".join(spec.permissions),
            tool_id=spec.name,
            deployment=run_ctx.deployment,
        )

    # Step 3: replay routing.
    cassette_payload = _replay_route(spec, args, run_ctx)

    # Step 4: emit stargraph.tool-call fact.
    _emit_fact(
        run_ctx.fathom,
        "stargraph.tool-call",
        {"tool_id": spec.name, "args_count": len(args)},
        run_id=run_ctx.run_id,
        step=run_ctx.step,
        origin="tool",
    )

    # Step 5: invoke the tool body (or use the cassette).
    raw_payload: dict[str, Any]
    if cassette_payload is not None:
        raw_payload = cassette_payload
    else:
        result = tool(**args)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, dict):
            raise IRValidationError(
                f"tool {spec.name!r} returned non-dict output",
                tool_id=spec.name,
                violation="tool-non-dict-output",
            )
        raw_payload = cast("dict[str, Any]", result)

    # Step 6: output-schema validation.
    _validate(raw_payload, spec.output_schema, kind="output", tool_id=spec.name)

    # Step 7: sanitize for LM context.
    sanitized = _sanitize(raw_payload)
    if not isinstance(sanitized, dict):
        raise IRValidationError(
            f"tool {spec.name!r} sanitization produced non-dict output",
            tool_id=spec.name,
            violation="tool-sanitize-shape",
        )
    output: dict[str, Any] = cast("dict[str, Any]", sanitized)

    # Step 8: emit stargraph.tool-result fact.
    _emit_fact(
        run_ctx.fathom,
        "stargraph.tool-result",
        {"tool_id": spec.name, "fields": len(output)},
        run_id=run_ctx.run_id,
        step=run_ctx.step,
        origin="tool",
    )

    # Step 9: optionally emit stargraph.tokens-used.
    tokens = _extract_tokens(raw_payload)
    if tokens is not None:
        run_ctx.tokens_emitted.append({"tool_id": spec.name, **tokens})
        _emit_fact(
            run_ctx.fathom,
            "stargraph.tokens-used",
            {"tool_id": spec.name, "total": tokens.get("total", 0)},
            run_id=run_ctx.run_id,
            step=run_ctx.step,
            origin="tool",
        )

    return ToolResult(output=output, replayed=cassette_payload is not None, tokens=tokens)
