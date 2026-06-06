# SPDX-License-Identifier: Apache-2.0
"""``@tool`` decorator skeleton (FR-26, design 3.4.3, Open Q8).

Binds a Python callable to a :class:`harbor.ir._models.ToolSpec`. The returned
wrapper preserves the original callable's behavior (``__call__``) and exposes
the bound spec as ``wrapper.spec`` (Open Q8 resolution: callable wrapper, not
descriptor, not staticmethod-binding).

When ``input_schema`` / ``output_schema`` are omitted, schemas are auto-derived
from the wrapped callable's annotated signature via
:class:`pydantic.TypeAdapter` over a synthetic :func:`pydantic.create_model`
shell whose fields mirror the parameters. The return-type annotation drives
``output_schema``.

When ``replay_policy`` is omitted, it defaults from ``side_effects`` per FR-21
/ NFR-8: ``none|read`` -> ``recorded_result``; ``write|external`` -> ``must_stub``.

This module ships as the **skeleton** referenced by task 1.6 -- the test
matrix (task 1.13) lands in Phase 3. Edge cases of signature introspection
(``*args``, ``**kwargs``, positional-only, ``Annotated[...]`` metadata
filtering) are intentionally out of scope here and tracked as TODOs inline.
"""

from __future__ import annotations

import inspect
from decimal import Decimal  # noqa: TC003 -- runtime use in keyword default
from typing import TYPE_CHECKING, Any, cast, get_type_hints

from pydantic import BaseModel, TypeAdapter, create_model

from harbor.tools.spec import ReplayPolicy, SideEffects

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["tool"]


def _default_replay_policy(side_effects: SideEffects) -> ReplayPolicy:
    """Derive the FR-21 / NFR-8 default replay policy from side-effects.

    ``none`` / ``read`` -> ``recorded_result`` (safe to re-run, but we still
    prefer recorded results during replay for determinism).
    ``write`` / ``external`` -> ``must_stub`` (re-running mutates the world;
    replay must hit a recorded cassette).
    """
    if side_effects in (SideEffects.none, SideEffects.read):
        return ReplayPolicy.recorded_result
    return ReplayPolicy.must_stub


def _derive_input_schema(
    input_schema: type[BaseModel] | dict[str, Any] | None,
    fn: Callable[..., Any],
) -> dict[str, Any]:
    """Build a JSON Schema for *input_schema* or, when ``None``, the callable's
    positional/keyword parameters.

    Three branches (T19):

    1. ``type[BaseModel]`` subclass -- returned via
       ``.model_json_schema(mode="serialization")``.
    2. ``dict`` -- returned unchanged (already a JSON Schema).
    3. ``None`` -- derived from *fn*'s annotated signature via
       :class:`pydantic.TypeAdapter` over a synthetic :func:`pydantic.create_model`
       shell (original behavior).

    Skips ``*args`` / ``**kwargs`` in the signature path -- they are not part of
    the schema surface.
    """
    if isinstance(input_schema, type) and issubclass(input_schema, BaseModel):  # pyright: ignore[reportUnnecessaryIsInstance]
        return input_schema.model_json_schema(mode="serialization")
    if isinstance(input_schema, dict):
        return input_schema

    # input_schema is None -- derive from fn's signature.
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=False)
    except Exception:
        hints = {}

    fields: dict[str, Any] = {}
    for name, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation: Any = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = Any
        default: Any = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (annotation, default)

    model_name = f"{fn.__name__.title().replace('_', '')}Args"
    arg_model = create_model(model_name, **fields)
    return TypeAdapter(arg_model).json_schema()


def _derive_output_schema(fn: Callable[..., Any]) -> dict[str, Any]:
    """Build a JSON Schema for the callable's return annotation.

    Falls back to a permissive ``{}`` (any-type) when the return is
    un-annotated -- ToolSpec requires a dict, and ``{}`` is the JSON Schema
    encoding of "any value".
    """
    try:
        hints = get_type_hints(fn, include_extras=False)
    except Exception:
        hints = {}
    return_t: Any = hints.get("return", inspect.Parameter.empty)
    if return_t is inspect.Parameter.empty or return_t is None or return_t is type(None):
        # ``None`` returns: emit a "null" schema. Missing annotation: any.
        if return_t is None or return_t is type(None):
            return {"type": "null"}
        return {}
    return TypeAdapter(return_t).json_schema()


def tool(
    *,
    name: str,
    namespace: str,
    version: str,
    side_effects: SideEffects,
    replay_policy: ReplayPolicy | None = None,
    requires_capability: str | list[str] | None = None,
    input_schema: type[BaseModel] | dict[str, Any] | None = None,
    output_schema: dict[str, Any] | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
    cost_estimate: Decimal | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Bind a Pydantic-typed callable to a :class:`ToolSpec` (FR-26).

    Returns a decorator that wraps the target callable so it remains directly
    callable (``wrapper(*args, **kwargs)`` proxies to the underlying function)
    while exposing ``wrapper.spec`` -- the constructed :class:`ToolSpec`.

    Parameters
    ----------
    name, namespace, version
        Tool identity. ``f"{namespace}.{name}@{version}"`` is the registry key.
    side_effects
        FR-33 enum -- determines replay-routing in ``runtime.tool_exec``.
    replay_policy
        Optional override; defaults per :func:`_default_replay_policy`.
    requires_capability
        Capability gate (NFR-7). Single string is normalized to a one-element
        list for ``ToolSpec.permissions``; ``None`` -> empty list.
    input_schema, output_schema
        Optional explicit JSON Schemas. When ``None``, derived from the
        wrapped callable's signature via :class:`pydantic.TypeAdapter`.
    description
        Human-readable summary; falls back to the callable's first docstring
        line, then to an empty string.
    idempotency_key, cost_estimate
        Forwarded verbatim to :class:`ToolSpec`.

    Notes
    -----
    The wrapper is *not* coerced to async here -- async-vs-sync routing is
    the execution-path's job (design 3.4.4 step 5). The wrapper preserves
    whatever calling convention the wrapped callable uses.
    """
    requires_capability_list: list[str]
    if requires_capability is None:
        requires_capability_list = []
    elif isinstance(requires_capability, str):
        requires_capability_list = [requires_capability]
    else:
        requires_capability_list = list(requires_capability)

    resolved_policy: ReplayPolicy = (
        replay_policy if replay_policy is not None else _default_replay_policy(side_effects)
    )

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        # Lazy ToolSpec import: see harbor.tools.__init__ docstring for the
        # circular-import rationale (harbor.ir._models imports our enums).
        from harbor.ir._models import ToolSpec

        resolved_input: dict[str, Any] = _derive_input_schema(input_schema, fn)
        resolved_output: dict[str, Any] = (
            output_schema if output_schema is not None else _derive_output_schema(fn)
        )

        if description is not None:
            resolved_description = description
        elif fn.__doc__:
            resolved_description = fn.__doc__.strip().splitlines()[0]
        else:
            resolved_description = ""

        spec = ToolSpec(
            name=name,
            namespace=namespace,
            version=version,
            description=resolved_description,
            input_schema=cast("dict[str, object]", resolved_input),
            output_schema=cast("dict[str, object]", resolved_output),
            side_effects=side_effects,
            replay_policy=resolved_policy,
            permissions=requires_capability_list,
            idempotency_key=idempotency_key,
            cost_estimate=cost_estimate,
        )

        # Attach the spec as an attribute on the original callable. We avoid
        # wrapping in a separate object so users can keep using the function
        # exactly as written (positional, keyword, async, sync -- all preserved).
        # Pyright requires a cast here because Callable lacks a ``spec`` attr.
        setattr(fn, "spec", spec)  # noqa: B010 -- attribute is part of the public Tool surface
        return fn

    return decorate
