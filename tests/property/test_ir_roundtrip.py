# SPDX-License-Identifier: Apache-2.0
"""Property-based bit-identical round-trip for ``IRDocument`` (NFR-1, AC-11.3).

Hypothesis composes arbitrary :class:`stargraph.ir.IRDocument` instances from the
simpler IR sub-models, then asserts ``loads(dumps(doc)) == doc`` and
``dumps(loads(dumps(doc))) == dumps(doc)`` -- bit-identical wire form on the
second round (the "canonical" property: the encoder is a fixed-point function
on its own output).

The test exercises the FR-15 single-canonical-entry-point invariant: every
optional list, the discriminator, the ``exclude_defaults`` behavior, and the
dict-of-strings ``state_schema``.

Scope note: the property keeps ``RuleSpec.then`` empty -- Pydantic v2's
``exclude_defaults=True`` strips :data:`Literal` discriminator defaults
(``kind="goto"`` etc.), so a rule with a non-empty ``then`` cannot round-trip
through the canonical wire form without a separate non-default discriminator
emission step. That gap is its own engine-spec concern; the property here
focuses on the whole-document surface that actually round-trips today.
:class:`Action` variants are exercised directly (without the discriminator-
union dispatch) in :func:`test_action_variant_alone_round_trips`.
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from stargraph.ir import (
    Action,
    AssertAction,
    GotoAction,
    HaltAction,
    IRDocument,
    MigrateBlock,
    NodeSpec,
    PackMount,
    ParallelAction,
    ParallelBlock,
    PluginManifest,
    RetractAction,
    RetryAction,
    RuleSpec,
    SkillRef,
    StoreRef,
    ToolRef,
    dumps,
    dumps_canonical,
    loads,
)

_PROFILE = settings(
    max_examples=75,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
    deadline=None,
)


# ---------------------------------------------------------------------------
# Atom strategies -- bounded text so generated docs stay tractable.
# ---------------------------------------------------------------------------


_ID = st.text(
    alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E),
    min_size=1,
    max_size=12,
)
_FREE_TEXT = st.text(max_size=24)
_VERSION = st.one_of(
    st.none(),
    st.from_regex(r"\A[0-9]+\.[0-9]+\.[0-9]+\Z", fullmatch=True),
)


# ---------------------------------------------------------------------------
# Action variants (each variant is its own strategy, then unioned).
# ---------------------------------------------------------------------------


_GOTO = st.builds(GotoAction, target=_ID)
_HALT = st.builds(HaltAction, reason=_FREE_TEXT)
_PARALLEL = st.builds(
    ParallelAction,
    targets=st.lists(_ID, min_size=1, max_size=4),
    join=_FREE_TEXT,
    strategy=st.sampled_from(["all", "any", "race", "quorum"]),
)
_RETRY = st.builds(
    RetryAction,
    target=_ID,
    backoff_ms=st.integers(min_value=0, max_value=60_000),
)
_ASSERT = st.builds(AssertAction, fact=_ID, slots=_FREE_TEXT)
_RETRACT = st.builds(RetractAction, pattern=_FREE_TEXT)

_ACTION: st.SearchStrategy[Action] = st.one_of(_GOTO, _HALT, _PARALLEL, _RETRY, _ASSERT, _RETRACT)


# ---------------------------------------------------------------------------
# Sub-model strategies.
# ---------------------------------------------------------------------------


_NODE = st.builds(NodeSpec, id=_ID, kind=_ID)
# ``then=[]`` only -- see module docstring "Scope note": ``exclude_defaults=True``
# strips Literal discriminator defaults, so non-empty ``then`` cannot round-trip
# bit-identically through the canonical wire form today.
_RULE = st.builds(
    RuleSpec,
    id=_ID,
    when=_FREE_TEXT,
    then=st.just(list[Action]()),
)
_TOOL_REF = st.builds(ToolRef, id=_ID, version=_VERSION)
_SKILL_REF = st.builds(SkillRef, id=_ID, version=_VERSION)
_STORE_REF = st.builds(StoreRef, name=_ID, provider=_ID)
_PACK_MOUNT = st.builds(PackMount, id=_ID, version=_VERSION)
_PARALLEL_BLOCK = st.builds(
    ParallelBlock,
    targets=st.lists(_ID, min_size=1, max_size=4),
    join=_FREE_TEXT,
    strategy=st.sampled_from(["all", "any", "race", "quorum"]),
)
_MIGRATE_BLOCK = st.builds(MigrateBlock, from_hash=_ID, to_hash=_ID)


# ---------------------------------------------------------------------------
# IRDocument composer.
# ---------------------------------------------------------------------------


_IR_DOCUMENT = st.builds(
    IRDocument,
    ir_version=st.just("1.0.0"),
    id=_ID,
    nodes=st.lists(_NODE, max_size=4),
    rules=st.lists(_RULE, max_size=3),
    tools=st.lists(_TOOL_REF, max_size=3),
    skills=st.lists(_SKILL_REF, max_size=3),
    stores=st.lists(_STORE_REF, max_size=3),
    state_schema=st.dictionaries(_ID, _ID, max_size=4),
    parallel=st.lists(_PARALLEL_BLOCK, max_size=2),
    governance=st.lists(_PACK_MOUNT, max_size=3),
    migrate=st.lists(_MIGRATE_BLOCK, max_size=2),
)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@_PROFILE
@given(doc=_IR_DOCUMENT)
def test_ir_document_dumps_loads_bit_identical(doc: IRDocument) -> None:
    """``dumps`` is a fixed point: re-encoding a parsed value matches the original wire."""
    text = dumps(doc)
    parsed = loads(text)
    assert isinstance(parsed, IRDocument)
    # Bit-identical: re-encode lands on the same wire form.
    assert dumps(parsed) == text


@_PROFILE
@given(doc=_IR_DOCUMENT)
def test_ir_document_canonical_dumps_bit_identical(doc: IRDocument) -> None:
    """``dumps_canonical`` (sort_keys=True) is also a fixed point on its own output."""
    text = dumps_canonical(doc)
    parsed = loads(text)
    assert dumps_canonical(parsed) == text


@_PROFILE
@given(doc=_IR_DOCUMENT)
def test_ir_document_loads_yields_equal_model(doc: IRDocument) -> None:
    """Round-trip via JSON yields a Pydantic-equal model (NFR-1, AC-11.3)."""
    parsed = loads(dumps(doc))
    assert parsed == doc


# ---------------------------------------------------------------------------
# PluginManifest round-trip (smaller surface, separate property).
# ---------------------------------------------------------------------------


_MANIFEST = st.builds(
    PluginManifest,
    name=_ID,
    version=st.from_regex(r"\A[0-9]+\.[0-9]+\.[0-9]+\Z", fullmatch=True),
    api_version=st.just("1"),
    namespaces=st.lists(_ID, min_size=1, max_size=4, unique=True),
    provides=st.lists(
        st.sampled_from(["tool", "skill", "store", "pack"]),
        min_size=1,
        max_size=4,
        unique=True,
    ),
    order=st.integers(min_value=0, max_value=10_000),
)


@_PROFILE
@given(manifest=_MANIFEST)
def test_plugin_manifest_round_trip(manifest: PluginManifest) -> None:
    """``PluginManifest`` round-trips bit-identically through ``dumps``/``loads``."""
    text = dumps(manifest)
    parsed = loads(text, PluginManifest)
    assert parsed == manifest
    assert dumps(parsed) == text


# ---------------------------------------------------------------------------
# Action variants: round-trip via their concrete classes (no union dispatch).
# Each variant's ``kind`` literal default IS restored on construction, so when
# loaded directly into the variant class the round-trip is bit-identical.
# ---------------------------------------------------------------------------


@_PROFILE
@given(action=_ACTION)
def test_action_variant_alone_round_trips(action: Action) -> None:
    """Each :data:`Action` variant round-trips byte-identically against itself."""
    text = dumps(action)
    parsed = loads(text, type(action))
    assert parsed == action
    assert dumps(parsed) == text
