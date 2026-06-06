# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``stargraph.fathom._adapter`` and ``stargraph.fathom._template``.

Pins the contract documented in ``stargraph.fathom._adapter`` against a mocked
:class:`fathom.Engine` so the suite runs in milliseconds and is independent
of the live-engine integration smoke (1.38 / ``test_poc_smoke.py``).

Coverage map:

* AC-6.1 -- :class:`FathomAdapter` constructor performs no eager mutation of
  the wrapped engine. ``register_stargraph_action_template`` is the only entry
  point that calls into ``engine.load_clips_function``.
* AC-6.2 -- :meth:`FathomAdapter.assert_with_provenance` runs three structural
  checks (NUL bytes, unbalanced parens, identifier-shape regex on
  ``_origin``/``_source``) on every encoded slot value.
* AC-6.3 -- provenance encoding round-trips through
  :func:`_sanitize_provenance_slot`; covered exhaustively in
  ``test_fathom_provenance.py``. Here we only assert the merge semantics.
* AC-7.1 -- :func:`register_stargraph_action_template` is idempotent (WeakSet).
* AC-7.4 -- :func:`extract_actions` is exercised end-to-end through
  :meth:`FathomAdapter.evaluate` with a mocked engine.
* AC-8.4 -- :meth:`FathomAdapter.mirror_state` walks ``Mirror``-annotated
  fields and emits one ``AssertSpec`` per field with the resolved template.
"""

from __future__ import annotations

import weakref
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any
from unittest.mock import MagicMock
from uuid import UUID

import fathom
import pytest
from pydantic import BaseModel

from stargraph.errors import ValidationError
from stargraph.fathom import (
    AssertAction,
    FathomAdapter,
    GotoAction,
    HaltAction,
    ParallelAction,
    ProvenanceBundle,
    RetractAction,
    RetryAction,
    extract_actions,
)
from stargraph.fathom._adapter import (
    _CLIPS_IDENT_RE,  # pyright: ignore[reportPrivateUsage]
    _check_slot_value,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.fathom._template import (
    STARGRAPH_ACTION_DEFTEMPLATE,
    register_stargraph_action_template,
)
from stargraph.ir._mirror import Mirror

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_engine() -> MagicMock:
    """A ``MagicMock`` typed against :class:`fathom.Engine` with a fresh identity.

    Each test gets its own engine so the module-level WeakSet behind
    :func:`register_stargraph_action_template` doesn't leak idempotency state
    across tests.
    """
    return MagicMock(spec=fathom.Engine)


@pytest.fixture
def good_provenance() -> ProvenanceBundle:
    """A complete provenance bundle whose every slot encodes cleanly."""
    return {
        "origin": "user",
        "source": "test",
        "run_id": UUID("12345678-1234-5678-1234-567812345678"),
        "step": 1,
        "confidence": Decimal("0.95"),
        "timestamp": datetime(2026, 4, 26, 12, 0, 0, tzinfo=UTC),
    }


# ---------------------------------------------------------------------------
# AC-6.1: constructor does not mutate the engine.
# ---------------------------------------------------------------------------


def test_constructor_does_not_call_engine_methods(mock_engine: MagicMock) -> None:
    """AC-6.1: ``FathomAdapter(engine)`` performs zero engine method calls."""
    adapter = FathomAdapter(mock_engine)

    # No load_clips_function, no assert_fact, no evaluate, no query, no anything.
    assert mock_engine.method_calls == []
    assert mock_engine.load_clips_function.call_count == 0
    assert mock_engine.assert_fact.call_count == 0
    assert mock_engine.evaluate.call_count == 0
    assert mock_engine.query.call_count == 0

    # The engine reference is stored verbatim.
    assert adapter.engine is mock_engine


def test_constructor_does_not_register_template(mock_engine: MagicMock) -> None:
    """AC-6.1: template registration is deferred to an explicit call."""
    FathomAdapter(mock_engine)

    # Pre-condition: the WeakSet does not contain the engine.
    from stargraph.fathom._template import (
        _registered_engines,  # pyright: ignore[reportPrivateUsage]
    )

    assert mock_engine not in _registered_engines
    assert mock_engine.load_clips_function.call_count == 0


# ---------------------------------------------------------------------------
# AC-7.1: stargraph_action template registration is idempotent.
# ---------------------------------------------------------------------------


def test_register_stargraph_action_template_calls_load_clips_function(
    mock_engine: MagicMock,
) -> None:
    """First registration call passes the deftemplate string to the engine."""
    register_stargraph_action_template(mock_engine)

    mock_engine.load_clips_function.assert_called_once_with(STARGRAPH_ACTION_DEFTEMPLATE)


def test_register_stargraph_action_template_is_idempotent(mock_engine: MagicMock) -> None:
    """AC-7.1: subsequent calls are no-ops on the same engine identity."""
    register_stargraph_action_template(mock_engine)
    register_stargraph_action_template(mock_engine)
    register_stargraph_action_template(mock_engine)

    # Even after three calls, only one CLIPS load happened.
    assert mock_engine.load_clips_function.call_count == 1


def test_register_stargraph_action_template_distinct_engines_each_register() -> None:
    """A fresh engine identity gets a fresh registration."""
    e1 = MagicMock(spec=fathom.Engine)
    e2 = MagicMock(spec=fathom.Engine)
    register_stargraph_action_template(e1)
    register_stargraph_action_template(e2)

    e1.load_clips_function.assert_called_once_with(STARGRAPH_ACTION_DEFTEMPLATE)
    e2.load_clips_function.assert_called_once_with(STARGRAPH_ACTION_DEFTEMPLATE)


def test_adapter_register_method_delegates(mock_engine: MagicMock) -> None:
    """The instance method delegates to the module-level registrar."""
    adapter = FathomAdapter(mock_engine)
    adapter.register_stargraph_action_template()
    mock_engine.load_clips_function.assert_called_once_with(STARGRAPH_ACTION_DEFTEMPLATE)
    # Idempotent through the adapter too.
    adapter.register_stargraph_action_template()
    assert mock_engine.load_clips_function.call_count == 1


def test_register_uses_weakset_so_engines_are_not_pinned() -> None:
    """The registry uses weakrefs so dropping the engine permits GC."""
    from stargraph.fathom._template import (
        _registered_engines,  # pyright: ignore[reportPrivateUsage]
    )

    assert isinstance(_registered_engines, weakref.WeakSet)


def test_stargraph_action_deftemplate_constant_is_canonical() -> None:
    """Deftemplate string carries every Stargraph verb in its allowed-symbols list."""
    txt = STARGRAPH_ACTION_DEFTEMPLATE
    assert "(deftemplate stargraph_action" in txt
    for verb in ("goto", "parallel", "halt", "retry", "assert", "retract"):
        assert verb in txt
    # Strategy slot enumerates the four join strategies.
    for strat in ("all", "any", "race", "quorum"):
        assert strat in txt


# ---------------------------------------------------------------------------
# AC-6.2: three structural sanitization checks.
# ---------------------------------------------------------------------------


def test_check_slot_value_rejects_nul_byte() -> None:
    """A NUL byte anywhere in a string slot is rejected."""
    with pytest.raises(ValidationError) as excinfo:
        _check_slot_value("any_slot", "before\x00after")
    assert "NUL byte" in excinfo.value.message
    assert excinfo.value.context.get("slot") == "any_slot"


def test_check_slot_value_rejects_unbalanced_parens() -> None:
    """Unbalanced parens could escape the s-expression layer -- forbidden."""
    with pytest.raises(ValidationError) as excinfo:
        _check_slot_value("payload", "evil(content")
    assert "unbalanced parentheses" in excinfo.value.message
    assert excinfo.value.context.get("slot") == "payload"


def test_check_slot_value_accepts_balanced_parens() -> None:
    """Balanced parens are not rejected at the adapter layer."""
    _check_slot_value("payload", "balanced(content)")
    _check_slot_value("payload", "((nested))")
    _check_slot_value("payload", "")


def test_check_slot_value_rejects_non_identifier_origin() -> None:
    """``_origin`` must match the CLIPS identifier shape."""
    with pytest.raises(ValidationError) as excinfo:
        _check_slot_value("_origin", "has spaces")
    assert "valid CLIPS identifier" in excinfo.value.message
    assert excinfo.value.context.get("slot") == "_origin"
    assert excinfo.value.context.get("pattern") == _CLIPS_IDENT_RE.pattern


def test_check_slot_value_rejects_non_identifier_source() -> None:
    """``_source`` is also identifier-checked (same allow-list)."""
    with pytest.raises(ValidationError) as excinfo:
        _check_slot_value("_source", "(deftemplate evil)")
    assert "valid CLIPS identifier" in excinfo.value.message


def test_check_slot_value_accepts_valid_identifier_origin() -> None:
    """Identifier-shaped origin values pass."""
    for ok in ("user", "system_v1", "Origin-2", "_internal"):
        _check_slot_value("_origin", ok)


def test_check_slot_value_skips_identifier_check_on_other_slots() -> None:
    """Only ``_origin`` / ``_source`` get the identifier check.

    Other slots accept arbitrary content (subject to NUL / paren rules).
    """
    _check_slot_value("_run_id", "has spaces and dashes-too")
    _check_slot_value("payload", "user@example.com")


def test_check_slot_value_passes_int_values() -> None:
    """Non-string values (e.g. encoded ints) skip the string-only checks."""
    _check_slot_value("_step", 42)
    _check_slot_value("_step", 0)
    # Even on _origin: a non-string value is structurally not a CLIPS identifier
    # concern (encoder only emits str|int; ints don't carry the same hazards).
    _check_slot_value("_origin", 7)


# ---------------------------------------------------------------------------
# assert_with_provenance flow: encode + sanitize + merge + forward.
# ---------------------------------------------------------------------------


def test_assert_with_provenance_merges_and_forwards(
    mock_engine: MagicMock, good_provenance: ProvenanceBundle
) -> None:
    """Encoded provenance is merged into caller slots and forwarded to the engine."""
    adapter = FathomAdapter(mock_engine)
    adapter.assert_with_provenance(
        template="evidence",
        slots={"field": "phase", "value": "poc"},
        provenance=good_provenance,
    )

    mock_engine.assert_fact.assert_called_once()
    template_arg, slots_arg = mock_engine.assert_fact.call_args.args
    assert template_arg == "evidence"

    # Provenance slots are present, encoded.
    assert slots_arg["_origin"] == "user"
    assert slots_arg["_source"] == "test"
    # UUID -> hex (no dashes).
    assert slots_arg["_run_id"] == "12345678123456781234567812345678"
    assert slots_arg["_step"] == 1
    assert slots_arg["_confidence"] == "0.95"
    assert slots_arg["_timestamp"] == "2026-04-26T12:00:00Z"
    # Caller slots merged in.
    assert slots_arg["field"] == "phase"
    assert slots_arg["value"] == "poc"


def test_assert_with_provenance_caller_wins_on_conflict(
    mock_engine: MagicMock, good_provenance: ProvenanceBundle
) -> None:
    """Caller slots override provenance keys on conflict (documented behavior)."""
    adapter = FathomAdapter(mock_engine)
    adapter.assert_with_provenance(
        template="evidence",
        slots={"_origin": "caller_override", "field": "x"},
        provenance=good_provenance,
    )

    _, slots_arg = mock_engine.assert_fact.call_args.args
    assert slots_arg["_origin"] == "caller_override"


def test_assert_with_provenance_rejects_hazardous_origin(
    mock_engine: MagicMock, good_provenance: ProvenanceBundle
) -> None:
    """A non-identifier ``origin`` fails the AC-6.2 identifier check."""
    bad: ProvenanceBundle = dict(good_provenance)  # type: ignore[assignment]
    bad["origin"] = "has spaces"

    adapter = FathomAdapter(mock_engine)
    with pytest.raises(ValidationError) as excinfo:
        adapter.assert_with_provenance(
            template="evidence",
            slots={"field": "x"},
            provenance=bad,
        )
    assert "valid CLIPS identifier" in excinfo.value.message
    # Engine assert_fact must NOT have been called -- fail-closed.
    assert mock_engine.assert_fact.call_count == 0


def test_assert_with_provenance_rejects_float_confidence(
    mock_engine: MagicMock, good_provenance: ProvenanceBundle
) -> None:
    """A float in any provenance slot trips the encoder before assert_fact runs."""
    bad: dict[str, Any] = dict(good_provenance)
    bad["confidence"] = 0.95  # float, not Decimal

    adapter = FathomAdapter(mock_engine)
    with pytest.raises(ValidationError) as excinfo:
        adapter.assert_with_provenance(
            template="evidence",
            slots={},
            provenance=bad,  # type: ignore[arg-type]
        )
    assert "float not permitted" in excinfo.value.message
    assert mock_engine.assert_fact.call_count == 0


def test_assert_with_provenance_rejects_naive_datetime(
    mock_engine: MagicMock, good_provenance: ProvenanceBundle
) -> None:
    """Naive datetimes are rejected before reaching the engine."""
    bad: dict[str, Any] = dict(good_provenance)
    bad["timestamp"] = datetime(2026, 4, 26, 12, 0, 0)  # naive

    adapter = FathomAdapter(mock_engine)
    with pytest.raises(ValidationError) as excinfo:
        adapter.assert_with_provenance(
            template="evidence",
            slots={},
            provenance=bad,  # type: ignore[arg-type]
        )
    assert "naive datetime" in excinfo.value.message
    assert mock_engine.assert_fact.call_count == 0


# ---------------------------------------------------------------------------
# evaluate(): mocked engine flow -> extract_actions translation (AC-7.4).
# ---------------------------------------------------------------------------


def test_evaluate_calls_engine_evaluate_then_query(mock_engine: MagicMock) -> None:
    """``evaluate`` runs the engine, then queries ``stargraph_action`` facts."""
    mock_engine.query.return_value = []
    adapter = FathomAdapter(mock_engine)

    actions = adapter.evaluate()

    assert actions == []
    mock_engine.evaluate.assert_called_once_with()
    mock_engine.query.assert_called_once_with("stargraph_action", None)


def test_evaluate_translates_facts_to_actions(mock_engine: MagicMock) -> None:
    """Each verb's fact dict round-trips through :func:`extract_actions`."""
    mock_engine.query.return_value = [
        {"kind": "goto", "target": "node_a"},
        {"kind": "halt", "reason": "done"},
        {
            "kind": "parallel",
            "targets": ["a", "b", "c"],
            "join": "join_node",
            "strategy": "all",
        },
        {"kind": "retry", "target": "foo", "backoff_ms": 100},
        {"kind": "assert", "fact": "marker", "slots": '{"k":"v"}'},
        {"kind": "retract", "pattern": "evidence"},
    ]
    adapter = FathomAdapter(mock_engine)

    actions = adapter.evaluate()

    assert len(actions) == 6
    goto, halt, parallel, retry, assert_action, retract = actions
    assert isinstance(goto, GotoAction) and goto.target == "node_a"
    assert isinstance(halt, HaltAction) and halt.reason == "done"
    assert isinstance(parallel, ParallelAction)
    assert parallel.targets == ["a", "b", "c"]
    assert parallel.join == "join_node"
    assert parallel.strategy == "all"
    assert isinstance(retry, RetryAction)
    assert retry.target == "foo" and retry.backoff_ms == 100
    assert isinstance(assert_action, AssertAction)
    assert assert_action.fact == "marker"
    assert assert_action.slots == '{"k":"v"}'
    assert isinstance(retract, RetractAction) and retract.pattern == "evidence"


def test_extract_actions_rejects_unknown_kind() -> None:
    """Unknown ``kind`` values raise :class:`ValidationError` (no silent fallback)."""
    with pytest.raises(ValidationError) as excinfo:
        extract_actions([{"kind": "unsupported_verb"}])
    assert "unknown stargraph_action kind" in excinfo.value.message
    assert excinfo.value.context.get("kind") == "unsupported_verb"


def test_extract_actions_handles_empty_list() -> None:
    """Empty fact list -> empty action list."""
    assert extract_actions([]) == []


def test_extract_actions_halt_default_reason() -> None:
    """``halt`` ``reason`` defaults to empty string when absent."""
    actions = extract_actions([{"kind": "halt"}])
    assert isinstance(actions[0], HaltAction)
    assert actions[0].reason == ""


def test_extract_actions_parallel_defaults() -> None:
    """``parallel`` defaults: empty targets, empty join, ``all`` strategy."""
    actions = extract_actions([{"kind": "parallel"}])
    assert isinstance(actions[0], ParallelAction)
    assert actions[0].targets == []
    assert actions[0].join == ""
    assert actions[0].strategy == "all"


# ---------------------------------------------------------------------------
# mirror_state introspection (AC-8.4).
# ---------------------------------------------------------------------------


class _ExplicitTemplateState(BaseModel):
    """State with explicit ``Mirror(template=...)`` overrides."""

    name: Annotated[str, Mirror(template="agent_name")]
    counter: Annotated[int, Mirror(template="agent_counter", lifecycle="step")]
    not_mirrored: str = "ignored"


class _DefaultTemplateState(BaseModel):
    """State relying on FR-13 default (template = field name)."""

    phase: Annotated[str, Mirror()]


def test_mirror_state_emits_assert_spec_per_mirrored_field(
    mock_engine: MagicMock,
) -> None:
    """One ``AssertSpec`` per ``Mirror``-marked field, with resolved template."""
    adapter = FathomAdapter(mock_engine)
    state = _ExplicitTemplateState(name="alpha", counter=7)

    specs = adapter.mirror_state(state, annotations={})

    assert len(specs) == 2
    by_template = {s.template: s for s in specs}
    assert "agent_name" in by_template
    assert "agent_counter" in by_template
    assert by_template["agent_name"].slots == {"value": "alpha"}
    assert by_template["agent_counter"].slots == {"value": "7"}


def test_mirror_state_skips_non_mirrored_fields(mock_engine: MagicMock) -> None:
    """Fields without a ``Mirror`` marker are ignored entirely."""
    adapter = FathomAdapter(mock_engine)
    state = _ExplicitTemplateState(name="alpha", counter=1, not_mirrored="hidden")

    specs = adapter.mirror_state(state, annotations={})

    templates = {s.template for s in specs}
    assert "not_mirrored" not in templates


def test_mirror_state_uses_field_name_default(mock_engine: MagicMock) -> None:
    """FR-13 default: ``Mirror()`` resolves template to the field name."""
    adapter = FathomAdapter(mock_engine)
    state = _DefaultTemplateState(phase="boot")

    specs = adapter.mirror_state(state, annotations={})

    assert len(specs) == 1
    assert specs[0].template == "phase"
    assert specs[0].slots == {"value": "boot"}


def test_mirror_state_does_not_call_engine(mock_engine: MagicMock) -> None:
    """``mirror_state`` is purely introspective -- no engine mutation."""
    adapter = FathomAdapter(mock_engine)
    state = _DefaultTemplateState(phase="boot")
    adapter.mirror_state(state, annotations={"unused": "metadata"})
    assert mock_engine.method_calls == []


def test_mirror_state_returns_empty_for_unannotated_model(
    mock_engine: MagicMock,
) -> None:
    """A model with no ``Mirror`` annotations emits zero ``AssertSpec`` entries."""

    class Plain(BaseModel):
        x: int = 0

    adapter = FathomAdapter(mock_engine)
    specs = adapter.mirror_state(Plain(x=1), annotations={})
    assert specs == []


# ---------------------------------------------------------------------------
# reload_rules: forwards untouched.
# ---------------------------------------------------------------------------


def test_reload_rules_forwards_to_engine(mock_engine: MagicMock) -> None:
    """``reload_rules`` is a thin pass-through; signature preserved."""
    mock_engine.reload_rules = MagicMock(return_value=("hash_before", "hash_after"))
    adapter = FathomAdapter(mock_engine)

    out = adapter.reload_rules(b"yaml-bytes", sig=b"sig", pubkey=b"pem")

    assert out == ("hash_before", "hash_after")
    mock_engine.reload_rules.assert_called_once_with(
        b"yaml-bytes", signature=b"sig", pubkey_pem=b"pem"
    )


def test_reload_rules_default_args(mock_engine: MagicMock) -> None:
    """``sig`` and ``pubkey`` default to ``None`` when omitted."""
    mock_engine.reload_rules = MagicMock(return_value=("a", "b"))
    adapter = FathomAdapter(mock_engine)

    adapter.reload_rules("yaml-text")

    mock_engine.reload_rules.assert_called_once_with("yaml-text", signature=None, pubkey_pem=None)
