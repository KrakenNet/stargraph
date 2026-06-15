# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.errors` (AC-3.1, AC-3.2, NFR-3).

Covers the public exception hierarchy:

* Instantiation of :class:`StargraphError` and each of the five subclasses.
* ``message`` attribute round-trips through :class:`Exception` ``args``.
* Keyword arguments are captured into the ``context`` dict (untouched).
* ``raise X from Y`` preserves the cause chain on ``__cause__``.
* Subclasses are :class:`StargraphError` (and therefore :class:`Exception`).
* Subclass identity is distinct: catching one subclass does not catch others.
"""

from __future__ import annotations

import pytest

from stargraph.errors import (
    CheckpointError,
    PluginLoadError,
    ReplayError,
    StargraphError,
    StargraphRuntimeError,
    ValidationError,
)

_SUBCLASSES: tuple[type[StargraphError], ...] = (
    ValidationError,
    PluginLoadError,
    StargraphRuntimeError,
    CheckpointError,
    ReplayError,
)


@pytest.mark.unit
def test_stargraph_error_stores_message_and_empty_context() -> None:
    """Bare :class:`StargraphError` stores ``message`` and an empty ``context`` dict."""
    err = StargraphError("boom")

    assert err.message == "boom"
    assert err.context == {}
    # Round-trips through Exception.args so ``str(err)`` returns the message.
    assert str(err) == "boom"
    assert err.args == ("boom",)


@pytest.mark.unit
def test_stargraph_error_captures_kwargs_into_context() -> None:
    """Keyword arguments populate ``context`` verbatim (no copying, no filtering)."""
    err = StargraphError("validation failed", field="name", value=42, extra=None)

    assert err.context == {"field": "name", "value": 42, "extra": None}
    # ``message`` is not re-injected into ``context``.
    assert "message" not in err.context


@pytest.mark.unit
def test_hint_and_see_surface_as_properties_and_render() -> None:
    """``hint=``/``see=`` are context keys exposed as properties and appended to str()."""
    err = ValidationError("bad state_class", hint="use module:Class", see="docs/x.md")

    # Remain plain context keys (structured logging + back-compat).
    assert err.context == {"hint": "use module:Class", "see": "docs/x.md"}
    # Surfaced as typed properties.
    assert err.hint == "use module:Class"
    assert err.see == "docs/x.md"
    # Rendered for the reader.
    assert str(err) == "bad state_class\nhint: use module:Class\nsee: docs/x.md"


@pytest.mark.unit
def test_missing_hint_and_see_are_none_and_str_is_message_only() -> None:
    """Without hint/see, properties are None and str() is the bare message."""
    err = ValidationError("plain")

    assert err.hint is None
    assert err.see is None
    assert str(err) == "plain"


@pytest.mark.unit
@pytest.mark.parametrize("cls", _SUBCLASSES)
def test_each_subclass_instantiates_with_message_and_context(cls: type[StargraphError]) -> None:
    """Each of the five subclasses accepts ``message`` + kwargs identically."""
    err = cls("oops", run_id="r-1", step=3)

    assert isinstance(err, cls)
    assert isinstance(err, StargraphError)
    assert isinstance(err, Exception)
    assert err.message == "oops"
    assert err.context == {"run_id": "r-1", "step": 3}


@pytest.mark.unit
def test_raise_from_preserves_exception_chain() -> None:
    """``raise X from Y`` sets ``__cause__`` so the original error is recoverable."""
    original = ValueError("root cause")

    with pytest.raises(ValidationError) as excinfo:
        try:
            raise original
        except ValueError as exc:
            raise ValidationError("wrapped", field="x") from exc

    assert excinfo.value.__cause__ is original
    # ``__suppress_context__`` is set by ``raise ... from ...`` so the
    # implicit-context chain is hidden in tracebacks.
    assert excinfo.value.__suppress_context__ is True
    assert excinfo.value.context == {"field": "x"}


@pytest.mark.unit
def test_raise_from_chains_through_multiple_stargraph_errors() -> None:
    """A two-step Stargraph->Stargraph chain preserves both ``__cause__`` links."""
    inner = CheckpointError("disk full", path="/tmp/ck")

    with pytest.raises(StargraphRuntimeError) as excinfo:
        try:
            raise inner
        except CheckpointError as exc:
            raise StargraphRuntimeError("step failed", step=7) from exc

    outer = excinfo.value
    assert outer.__cause__ is inner
    assert isinstance(outer.__cause__, CheckpointError)
    # Both halves of the chain keep their own context dicts intact.
    assert outer.context == {"step": 7}
    assert inner.context == {"path": "/tmp/ck"}


@pytest.mark.unit
def test_subclass_isolation_does_not_cross_categories() -> None:
    """Catching one subclass does not also swallow a sibling subclass."""
    # ``ValidationError`` and ``CheckpointError`` are siblings: they both extend
    # :class:`StargraphError` but neither extends the other.
    with pytest.raises(CheckpointError):
        try:
            raise CheckpointError("ck broke")
        except ValidationError:  # pragma: no cover - must NOT match
            pytest.fail("ValidationError handler must not catch CheckpointError")

    # However, the common base does catch every subclass.
    for cls in _SUBCLASSES:
        with pytest.raises(StargraphError):
            raise cls("x")


@pytest.mark.unit
def test_context_dict_is_per_instance_not_shared() -> None:
    """Each instance owns its own ``context`` dict (no class-level aliasing)."""
    a = StargraphError("a", k=1)
    b = StargraphError("b", k=2)

    a.context["mutated"] = True

    assert b.context == {"k": 2}
    assert a.context == {"k": 1, "mutated": True}


@pytest.mark.unit
def test_stargraph_runtime_error_does_not_shadow_builtin() -> None:
    """:class:`StargraphRuntimeError` is distinct from the stdlib ``RuntimeError``."""
    err = StargraphRuntimeError("nope")

    assert isinstance(err, StargraphError)
    assert not isinstance(err, RuntimeError)
    # And the stdlib RuntimeError is not a StargraphError either.
    assert not isinstance(RuntimeError("x"), StargraphError)
