# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.ir._mirror` (FR-13, FR-14, AC-8.5).

Covers:

* :class:`Mirror` is a frozen dataclass; lifecycle constraint raises
  :class:`TypeError` on construction (not at field-attach time).
* ``__get_pydantic_json_schema__`` round-trip: a field annotated with
  ``Annotated[T, Mirror(template=..., lifecycle=...)]`` emits the expected
  ``stargraph_mirror*`` keys in JSON Schema (AC-8.5).
* ``Mirror(template=None)`` (the default) emits ``stargraph_mirror=true`` and
  the lifecycle but **omits** ``stargraph_mirror_template`` -- the field-name
  fallback (FR-13) is a runtime concept resolved by :func:`mirrored_fields`.
* :func:`mirrored_fields` resolves ``template=None`` to the field name and
  preserves explicit overrides; fields without a :class:`Mirror` marker are
  skipped.
"""

from __future__ import annotations

from typing import Annotated, Any

import pytest
from pydantic import BaseModel, ConfigDict

from stargraph.ir._mirror import Mirror, ResolvedMirror, mirrored_fields

# ---------------------------------------------------------------------------
# Mirror dataclass shape
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mirror_is_frozen_dataclass_with_defaults() -> None:
    """``Mirror()`` defaults to ``template=None`` and ``lifecycle='run'``."""
    m = Mirror()
    assert m.template is None
    assert m.lifecycle == "run"
    # Frozen: cannot reassign.
    with pytest.raises((AttributeError, TypeError)):
        m.template = "x"  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.unit
@pytest.mark.parametrize("lifecycle", ["run", "step", "pinned"])
def test_mirror_accepts_all_three_lifecycles(lifecycle: str) -> None:
    """Each documented lifecycle (``run``/``step``/``pinned``) constructs cleanly."""
    m = Mirror(lifecycle=lifecycle)  # pyright: ignore[reportArgumentType]
    assert m.lifecycle == lifecycle


@pytest.mark.unit
def test_mirror_invalid_lifecycle_raises_typeerror() -> None:
    """``Mirror(lifecycle='bogus')`` raises :class:`TypeError` at construction."""
    with pytest.raises(TypeError) as excinfo:
        Mirror(lifecycle="bogus")  # pyright: ignore[reportArgumentType]
    assert "lifecycle must be one of" in str(excinfo.value)


# ---------------------------------------------------------------------------
# JSON Schema round-trip (AC-8.5)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mirror_explicit_template_round_trips_to_json_schema() -> None:
    """``Annotated[T, Mirror(template=..., lifecycle=...)]`` emits all three keys."""

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        a: Annotated[int, Mirror(template="alpha", lifecycle="step")]

    schema: dict[str, Any] = M.model_json_schema()
    prop: dict[str, Any] = schema["properties"]["a"]
    assert prop["stargraph_mirror"] is True
    assert prop["stargraph_mirror_template"] == "alpha"
    assert prop["stargraph_mirror_lifecycle"] == "step"
    assert prop["type"] == "integer"


@pytest.mark.unit
def test_mirror_template_none_omits_template_key_in_json_schema() -> None:
    """FR-13: the field-name fallback is **not** emitted to JSON Schema."""

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        b: Annotated[str, Mirror()]  # template=None

    schema: dict[str, Any] = M.model_json_schema()
    prop: dict[str, Any] = schema["properties"]["b"]
    assert prop["stargraph_mirror"] is True
    assert "stargraph_mirror_template" not in prop
    assert prop["stargraph_mirror_lifecycle"] == "run"


@pytest.mark.unit
def test_unmarked_field_has_no_stargraph_mirror_keys() -> None:
    """Plain (unmarked) fields stay free of ``stargraph_mirror*`` keys."""

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        c: int

    prop: dict[str, Any] = M.model_json_schema()["properties"]["c"]
    assert "stargraph_mirror" not in prop
    assert "stargraph_mirror_template" not in prop
    assert "stargraph_mirror_lifecycle" not in prop


# ---------------------------------------------------------------------------
# mirrored_fields() -- FR-13 default fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mirrored_fields_resolves_template_none_to_field_name() -> None:
    """FR-13: ``Mirror(template=None)`` resolves to the field's own name."""

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        run_id: Annotated[str, Mirror()]  # template defaults to 'run_id'

    out = mirrored_fields(M)
    assert out == {"run_id": ResolvedMirror(template="run_id", lifecycle="run")}


@pytest.mark.unit
def test_mirrored_fields_preserves_explicit_template_override() -> None:
    """An explicit ``Mirror(template=...)`` is preserved verbatim."""

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        attr: Annotated[int, Mirror(template="custom_tpl", lifecycle="pinned")]

    out = mirrored_fields(M)
    assert out == {"attr": ResolvedMirror(template="custom_tpl", lifecycle="pinned")}


@pytest.mark.unit
def test_mirrored_fields_skips_fields_without_marker() -> None:
    """Fields without a :class:`Mirror` annotation are omitted from the result."""

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        marked: Annotated[str, Mirror(template="m")]
        unmarked: int

    out = mirrored_fields(M)
    assert "unmarked" not in out
    assert "marked" in out


@pytest.mark.unit
def test_mirrored_fields_takes_first_marker_when_multiple_metadata() -> None:
    """Only the first :class:`Mirror` marker is kept (extra metadata is benign).

    The implementation breaks on the first match; we exercise that path with
    a non-Mirror metadata entry preceding the Mirror, and confirm the Mirror
    is still picked up.
    """

    class _Sentinel:
        pass

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        f: Annotated[int, _Sentinel(), Mirror(template="t1", lifecycle="step")]

    out = mirrored_fields(M)
    assert out["f"].template == "t1"
    assert out["f"].lifecycle == "step"


@pytest.mark.unit
def test_mirrored_fields_empty_for_plain_model() -> None:
    """A model with no Mirror markers yields ``{}``."""

    class M(BaseModel):
        model_config = ConfigDict(extra="forbid")
        a: int
        b: str

    assert mirrored_fields(M) == {}


@pytest.mark.unit
def test_resolvedmirror_is_frozen_dataclass() -> None:
    """:class:`ResolvedMirror` is frozen (cannot reassign ``template``)."""
    rm = ResolvedMirror(template="t", lifecycle="run")
    with pytest.raises((AttributeError, TypeError)):
        rm.template = "other"  # pyright: ignore[reportAttributeAccessIssue]
