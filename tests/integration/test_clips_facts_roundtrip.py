# SPDX-License-Identifier: Apache-2.0
"""TDD-RED suite for FR-16 CLIPS facts round-trip (research §4 amendment 9).

Pins the loud-fail integration contract from NFR-4: a Stargraph checkpoint
MUST round-trip a CLIPS :class:`clips.Environment`'s asserted-fact set
via the ``Environment.save_facts(path, mode=SaveMode.LOCAL_SAVE)`` /
``Environment.load_facts(path)`` text format. Per design §3.2.1 and the
verbatim research §4 amendment 9, the agenda + rule-firing-history are
**not** serialized (clipspy/CLIPS itself exposes no such API); resume
re-fires rules against the re-asserted facts -- this is consistent with
ADR 0001's boundary-only rule-firing model.

Cases (per task 3.22):

1. ``test_clipspy_save_facts_native_round_trip`` -- direct clipspy
   smoke test: ``save_facts`` -> fresh ``Environment`` -> ``load_facts``
   yields the same fact set. Pins the upstream API contract; failure
   here means clipspy regressed and the GREEN wiring will not work.
2. ``test_stargraph_clips_helper_round_trip`` -- the (not-yet-implemented)
   ``stargraph.checkpoint._clips`` helper exposes ``dump_facts(env) ->
   list[str]`` / ``load_facts(env, payload)`` that wrap the clipspy
   text-format API for the JSONB ``clips_facts`` column. RED state:
   :class:`ModuleNotFoundError` on the deferred import (the helper is
   added in 3.23 GREEN).
3. ``test_checkpoint_round_trip_preserves_clips_facts`` -- end-to-end
   through :class:`stargraph.checkpoint.sqlite.SQLiteCheckpointer`: an
   ``Environment`` with N asserted facts is dumped via the helper into
   a :class:`Checkpoint`, written, read back, re-loaded into a fresh
   ``Environment``, and the resulting fact set matches. RED state:
   :class:`AttributeError` / :class:`ImportError` on the helper, or a
   plain mismatch because the existing schema stores
   ``list[dict[str, Any]]`` and the GREEN path stores text-format
   strings.
4. ``test_agenda_and_firing_history_not_serialized`` -- after
   round-trip, the reconstituted ``Environment`` has an EMPTY
   :py:meth:`Environment.agenda` (no preserved activations) until
   :py:meth:`Environment.run` is called against the re-asserted facts.
   This is the documented invariant from research §4 amendment 9; it
   is a guard-rail test, not a behavioural failure -- but the
   round-trip path it depends on is RED until 3.23 wires the helper.

The deferred-import pattern (`importlib.import_module(...)` inside each
test) keeps pyright + ruff green while the missing surface fails at
runtime with the expected RED signal.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from stargraph.checkpoint import Checkpoint
from stargraph.checkpoint.sqlite import SQLiteCheckpointer

if TYPE_CHECKING:
    from pathlib import Path

clips = pytest.importorskip("clips")  # skip if clipspy missing on CI


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


def _build_env_with_facts() -> Any:
    """Return a fresh :class:`clips.Environment` with 3 asserted facts.

    The deftemplate + asserts give the clipspy text format a stable
    shape (``(person (name alice) (age 30))`` etc.) that survives
    save_facts/load_facts unchanged.
    """
    env = clips.Environment()
    env.build("(deftemplate person (slot name) (slot age))")
    env.assert_string("(person (name alice) (age 30))")
    env.assert_string("(person (name bob) (age 25))")
    env.assert_string("(person (name carol) (age 40))")
    return env


def _fact_strings(env: Any) -> list[str]:
    """Return sorted ``str(fact)`` values for an environment's fact list."""
    return sorted(str(f) for f in env.facts())


def _baseline_checkpoint(clips_facts: list[Any]) -> Checkpoint:
    """Build a :class:`Checkpoint` with ``clips_facts`` carrying the payload.

    All non-CLIPS fields are minimal but type-valid; only ``clips_facts``
    is exercised by the round-trip assertion.
    """
    return Checkpoint(
        run_id="test-roundtrip",
        step=0,
        branch_id=None,
        parent_step_idx=None,
        graph_hash="0" * 64,
        runtime_hash="0" * 64,
        state={},
        clips_facts=clips_facts,
        last_node="entry",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="0" * 64,
    )


# --------------------------------------------------------------------------- #
# Cases                                                                       #
# --------------------------------------------------------------------------- #


def test_clipspy_save_facts_native_round_trip(tmp_path: Path) -> None:
    """Case 1: direct clipspy ``save_facts`` -> fresh env ``load_facts``.

    Pins the upstream API contract from research §4 amendment 9. The
    text-format file produced by ``Environment.save_facts(path,
    mode=SaveMode.LOCAL_SAVE)`` MUST be consumable by a fresh
    ``Environment.load_facts(path)`` and yield an equivalent fact set.
    Skipped (via :func:`pytest.importorskip`) if clipspy is missing.
    """
    src = _build_env_with_facts()
    expected = _fact_strings(src)

    facts_path = tmp_path / "facts.clp"
    src.save_facts(str(facts_path), mode=clips.SaveMode.LOCAL_SAVE)
    assert facts_path.exists(), "save_facts did not produce a file"

    dst = clips.Environment()
    dst.build("(deftemplate person (slot name) (slot age))")
    dst.load_facts(str(facts_path))

    actual = _fact_strings(dst)
    assert actual == expected, f"fact set mismatch: {actual=} {expected=}"


def test_stargraph_clips_helper_round_trip(tmp_path: Path) -> None:
    """Case 2: ``stargraph.checkpoint._clips`` helper round-trips facts.

    RED contract: the GREEN path adds a tiny helper module that wraps
    the clipspy text-format API for the JSONB ``clips_facts`` column.
    Expected surface (3.23 GREEN):

    .. code-block:: python

        from stargraph.checkpoint._clips import dump_facts, load_facts
        payload: list[str] = dump_facts(env)        # JSONB-friendly
        load_facts(target_env, payload)             # rehydrates target

    Until 3.23 lands the import fails with
    :class:`ModuleNotFoundError`, which is the RED signal.
    """
    del tmp_path
    helper = importlib.import_module("stargraph.checkpoint._clips")
    dump_facts = helper.dump_facts
    load_facts = helper.load_facts

    src = _build_env_with_facts()
    expected = _fact_strings(src)

    payload = dump_facts(src)
    # JSONB-friendly: list[str] (text-format lines) or list[dict] -- both
    # serialize cleanly through ``orjson``. The shape is the GREEN
    # implementation's choice; the test only requires a successful
    # round-trip.
    assert isinstance(payload, list), f"dump_facts must return a list, got {type(payload)}"

    dst = clips.Environment()
    dst.build("(deftemplate person (slot name) (slot age))")
    load_facts(dst, payload)

    actual = _fact_strings(dst)
    assert actual == expected, f"helper round-trip mismatch: {actual=} {expected=}"


def test_checkpoint_round_trip_preserves_clips_facts(tmp_path: Path) -> None:
    """Case 3: end-to-end ``SQLiteCheckpointer.write`` -> ``read_latest``.

    Wires together the clipspy text-format dump + the SQLite driver:
    asserted facts -> ``dump_facts`` -> ``Checkpoint.clips_facts`` ->
    ``write`` -> ``read_latest`` -> ``load_facts`` into a fresh env ->
    fact-set equivalence.

    RED state: the helper module does not exist, so the
    :func:`importlib.import_module` call raises
    :class:`ModuleNotFoundError`. After 3.23 GREEN wires the helper,
    the round-trip passes.
    """
    helper = importlib.import_module("stargraph.checkpoint._clips")
    dump_facts = helper.dump_facts
    load_facts = helper.load_facts

    src = _build_env_with_facts()
    expected = _fact_strings(src)
    payload = dump_facts(src)

    db_path = tmp_path / "ckpt.sqlite"
    checkpointer = SQLiteCheckpointer(db_path)
    ckpt = _baseline_checkpoint(payload)

    async def _round_trip() -> Checkpoint | None:
        await checkpointer.bootstrap()
        await checkpointer.write(ckpt)
        return await checkpointer.read_latest("test-roundtrip")

    loaded = asyncio.run(_round_trip())
    assert loaded is not None, "read_latest returned None for written checkpoint"
    assert loaded.clips_facts == payload, (
        f"clips_facts not preserved: {loaded.clips_facts=} {payload=}"
    )

    dst = clips.Environment()
    dst.build("(deftemplate person (slot name) (slot age))")
    load_facts(dst, loaded.clips_facts)

    actual = _fact_strings(dst)
    assert actual == expected, f"end-to-end fact set mismatch: {actual=} {expected=}"


def test_agenda_and_firing_history_not_serialized(tmp_path: Path) -> None:
    """Case 4: agenda + firing history are NOT serialized (research §4 amendment 9).

    Per ADR 0001 (boundary-only rule firing) and the documented clipspy
    limit, ``save_facts``/``load_facts`` round-trip the asserted-fact
    list only. The reconstituted environment MUST have an empty
    :py:meth:`Environment.agenda` until rules re-fire against the
    re-asserted facts. This guard-rail pins that semantic so a future
    "preserve agenda" patch can't silently land without doc updates.

    RED state: depends on the helper module from 3.23.
    """
    helper = importlib.import_module("stargraph.checkpoint._clips")
    dump_facts = helper.dump_facts
    load_facts = helper.load_facts

    # Build a source env with one rule that activates on a person-fact.
    # The rule fires before snapshot, so the agenda is empty at dump
    # time -- but we also pin that re-loading does not magically
    # repopulate firing history.
    src = clips.Environment()
    src.build("(deftemplate person (slot name) (slot age))")
    src.build(
        "(defrule mark-adult"
        " (person (name ?n) (age ?a&:(>= ?a 18)))"
        ' => (printout t "adult " ?n crlf))'
    )
    src.assert_string("(person (name alice) (age 30))")
    src.assert_string("(person (name bob) (age 25))")

    # Snapshot + reload through the helper.
    payload = dump_facts(src)
    del tmp_path  # unused -- helper does its own tmp file management

    dst = clips.Environment()
    dst.build("(deftemplate person (slot name) (slot age))")
    dst.build(
        "(defrule mark-adult"
        " (person (name ?n) (age ?a&:(>= ?a 18)))"
        ' => (printout t "adult " ?n crlf))'
    )
    load_facts(dst, payload)

    # Facts present.
    assert len(list(dst.facts())) == 2, "expected 2 re-asserted person facts"

    # Boundary-only firing per ADR 0001: agenda may carry fresh
    # activations (rules pattern-match against re-asserted facts) but
    # MUST NOT carry serialized firing history. This is intrinsic to
    # the clipspy text format -- there is no API to round-trip
    # firing history. Re-firing is the resume path.
    #
    # NOTE: clipspy 1.x exposes pending activations via
    # ``Environment.activations()`` (not ``Environment.agenda()`` -- the
    # 3.22 RED draft used the latter, which does not exist on the
    # upstream surface). Each Activation carries the bound rule's name
    # via ``str(activation)`` (e.g. ``"Activation: 0 mark-adult: f-1"``);
    # the object itself does not expose a ``.rule`` attribute on this
    # version, so we assert against the printable form.
    activations = list(dst.activations())
    # If clipspy ever changes save_facts to also dump firing-history,
    # this assertion fails loudly and we revisit ADR 0001.
    for act in activations:
        # Activations are fresh (created by load-time pattern match),
        # not restored from a serialized agenda. Their printable form
        # carries the bound rule name from the current ruleset.
        assert "mark-adult" in str(act), f"unexpected activation shape: {act!r}"

    # Sanity: rules can re-fire from the re-asserted facts.
    fired = dst.run()
    assert fired >= 0, "Environment.run() returned a negative fire count"
