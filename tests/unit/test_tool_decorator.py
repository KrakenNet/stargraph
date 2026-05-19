# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`harbor.tools.decorator` (FR-26, design 3.4.3).

Covers two key behaviors of the ``@tool`` decorator:

* Auto-schema derivation from the wrapped callable's annotated signature
  -- ``input_schema`` mirrors the parameters via :class:`pydantic.TypeAdapter`,
  ``output_schema`` reflects the return annotation.
* Default ``replay_policy`` resolution per ``side_effects`` (FR-21 / NFR-8):
  ``none|read`` -> ``recorded_result``; ``write|external`` -> ``must_stub``.
  Explicit overrides are preserved.
"""

from __future__ import annotations

from typing import cast

import pytest

from harbor.tools import ReplayPolicy, SideEffects, tool


@pytest.mark.unit
def test_auto_input_schema_derives_required_fields_from_signature() -> None:
    """Annotated parameters with no defaults become required schema fields."""

    @tool(
        name="echo",
        namespace="util",
        version="1.0.0",
        side_effects=SideEffects.none,
    )
    def echo(text: str, count: int) -> str:
        return text * count

    schema = echo.spec.input_schema  # type: ignore[attr-defined]
    props = cast("dict[str, object]", schema["properties"])
    assert "text" in props
    assert "count" in props
    assert cast("dict[str, object]", props["text"])["type"] == "string"
    assert cast("dict[str, object]", props["count"])["type"] == "integer"
    required = cast("list[str]", schema["required"])
    assert set(required) == {"text", "count"}


@pytest.mark.unit
def test_auto_input_schema_marks_default_params_optional() -> None:
    """Parameters with defaults are not in ``required``."""

    @tool(
        name="greet",
        namespace="util",
        version="1.0.0",
        side_effects=SideEffects.none,
    )
    def greet(name: str, salutation: str = "Hello") -> str:
        return f"{salutation}, {name}"

    schema = greet.spec.input_schema  # type: ignore[attr-defined]
    required = cast("list[str]", schema.get("required", []))
    assert "name" in required
    assert "salutation" not in required


@pytest.mark.unit
def test_auto_output_schema_reflects_return_annotation() -> None:
    """The return annotation drives the output schema."""

    @tool(
        name="counter",
        namespace="util",
        version="1.0.0",
        side_effects=SideEffects.none,
    )
    def counter(text: str) -> int:
        return len(text)

    schema = counter.spec.output_schema  # type: ignore[attr-defined]
    assert schema.get("type") == "integer"


@pytest.mark.unit
def test_auto_output_schema_handles_none_return() -> None:
    """A ``-> None`` return becomes a ``{"type": "null"}`` schema."""

    @tool(
        name="noop",
        namespace="util",
        version="1.0.0",
        side_effects=SideEffects.none,
    )
    def noop(x: int) -> None:
        del x

    schema = noop.spec.output_schema  # type: ignore[attr-defined]
    assert schema == {"type": "null"}


@pytest.mark.unit
def test_explicit_schemas_override_auto_derivation() -> None:
    """Explicit ``input_schema`` / ``output_schema`` short-circuit derivation."""
    explicit_in = {"type": "object", "properties": {"q": {"type": "string"}}}
    explicit_out = {"type": "boolean"}

    @tool(
        name="match",
        namespace="util",
        version="1.0.0",
        side_effects=SideEffects.none,
        input_schema=explicit_in,
        output_schema=explicit_out,
    )
    def match(q: str) -> bool:
        return bool(q)

    assert match.spec.input_schema == explicit_in  # type: ignore[attr-defined]
    assert match.spec.output_schema == explicit_out  # type: ignore[attr-defined]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("side_effects", "expected_policy"),
    [
        (SideEffects.none, ReplayPolicy.recorded_result),
        (SideEffects.read, ReplayPolicy.recorded_result),
        (SideEffects.write, ReplayPolicy.must_stub),
        (SideEffects.external, ReplayPolicy.must_stub),
    ],
)
def test_default_replay_policy_resolves_per_side_effects(
    side_effects: SideEffects, expected_policy: ReplayPolicy
) -> None:
    """``replay_policy`` defaults per FR-21 / NFR-8 when not explicitly set."""

    @tool(
        name="t",
        namespace="util",
        version="1.0.0",
        side_effects=side_effects,
    )
    def fn(x: int) -> int:
        return x

    assert fn.spec.replay_policy is expected_policy  # type: ignore[attr-defined]
    assert fn.spec.side_effects is side_effects  # type: ignore[attr-defined]


@pytest.mark.unit
def test_explicit_replay_policy_overrides_default() -> None:
    """An explicit ``replay_policy`` is preserved even when it conflicts with the default."""

    @tool(
        name="risky",
        namespace="util",
        version="1.0.0",
        side_effects=SideEffects.write,
        replay_policy=ReplayPolicy.fail_loud,
    )
    def risky(x: int) -> int:
        return x

    assert risky.spec.replay_policy is ReplayPolicy.fail_loud  # type: ignore[attr-defined]


@pytest.mark.unit
def test_requires_capability_string_normalizes_to_list() -> None:
    """A single capability string becomes a one-element ``permissions`` list."""

    @tool(
        name="reader",
        namespace="fs",
        version="1.0.0",
        side_effects=SideEffects.read,
        requires_capability="fs.read:/workspace/*",
    )
    def reader(path: str) -> str:
        return path

    assert reader.spec.permissions == ["fs.read:/workspace/*"]  # type: ignore[attr-defined]


@pytest.mark.unit
def test_decorator_preserves_callable_behavior() -> None:
    """The wrapper proxies to the underlying function (Open Q8: callable wrapper)."""

    @tool(
        name="add",
        namespace="util",
        version="1.0.0",
        side_effects=SideEffects.none,
    )
    def add(a: int, b: int) -> int:
        return a + b

    assert add(2, 3) == 5


# ---------------------------------------------------------------------------
# T19: _derive_input_schema accepts type[BaseModel]
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_derive_input_schema_from_basemodel_subclass() -> None:
    """A :class:`pydantic.BaseModel` subclass passed as ``input_schema`` is
    converted via ``.model_json_schema(mode="serialization")`` (T19)."""
    from pydantic import BaseModel

    from harbor.tools.decorator import _derive_input_schema

    class _Inputs(BaseModel):
        query: str
        top_k: int = 5

    def _fn(query: str, top_k: int = 5) -> None: ...

    out = _derive_input_schema(_Inputs, _fn)
    assert isinstance(out, dict)
    assert "properties" in out
    assert set(out["properties"].keys()) == {"query", "top_k"}


@pytest.mark.unit
def test_derive_input_schema_from_dict_unchanged() -> None:
    """``input_schema`` already a dict is returned unchanged (T19)."""
    from harbor.tools.decorator import _derive_input_schema

    given = {"type": "object", "properties": {"x": {"type": "string"}}}

    def _fn(x: str) -> None: ...

    out = _derive_input_schema(given, _fn)
    assert out == given


@pytest.mark.unit
def test_derive_input_schema_from_signature_unchanged() -> None:
    """``input_schema=None`` falls through to the ``TypeAdapter`` signature path
    -- existing behavior preserved (T19)."""
    from harbor.tools.decorator import _derive_input_schema

    def _fn(x: str, y: int = 0) -> None: ...

    out = _derive_input_schema(None, _fn)
    assert isinstance(out, dict)
    assert "properties" in out
