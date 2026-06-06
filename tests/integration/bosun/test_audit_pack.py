# SPDX-License-Identifier: Apache-2.0
"""Integration: ``stargraph.bosun.audit@1.0`` round-trip (FR-35, FR-38, design §7.2).

Drives the audit pack through a stub graph: assert synthetic
``stargraph.transition`` and ``stargraph.tool_call`` facts, run the engine,
and verify ``bosun.audit`` facts are emitted. Then promotes those facts
to typed :class:`BosunAuditEvent` instances via the seam in
:mod:`stargraph.bosun.audit` and writes them through the existing
:class:`JSONLAuditSink`. Finally, reads the JSONL log back to confirm
the audit trail round-tripped end-to-end (single-sink invariant per
design §7.2 + Resolved Decision #5).
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest
from fathom import Engine

from stargraph.audit.jsonl import JSONLAuditSink
from stargraph.bosun.audit import promote_audit_facts
from stargraph.fathom import FathomAdapter
from stargraph.runtime.events import BosunAuditEvent

from ._helpers import install_stargraph_fact_stubs, load_pack_rules

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.serve


def _fresh_audit_engine() -> Engine:
    eng = Engine(default_decision="deny")
    install_stargraph_fact_stubs(eng)
    load_pack_rules(eng, "audit")
    return eng


def test_audit_pack_emits_facts_for_transition_and_tool_call() -> None:
    """Driving a stub graph through one transition + one tool_call emits
    the matching ``bosun.audit`` facts (kinds ``transition`` + ``tool_call``)."""
    eng = _fresh_audit_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.transition (_run_id "r1") (_step 1) (kind "started"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.tool_call (_run_id "r1") (_step 2) (name "broker_request"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    facts = [dict(f) for f in eng._env.find_template("bosun.audit").facts()]  # pyright: ignore[reportPrivateUsage]
    kinds = sorted(f["kind"] for f in facts)
    assert kinds == ["tool_call", "transition"], (
        f"expected tool_call + transition kinds; got {kinds!r}"
    )
    # Each fact carries the run_id + step from the source.
    for f in facts:
        assert f["run_id"] == "r1"
        assert f["step"] in {1, 2}


def test_audit_pack_promotes_facts_to_typed_bosun_audit_events() -> None:
    """``promote_audit_facts`` returns one :class:`BosunAuditEvent` per
    asserted fact, with the right pack identity + run_id."""
    eng = _fresh_audit_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.transition (_run_id "rX") (_step 7) (kind "completed"))'
    )
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.cancel (_run_id "rX") (_step 8) (reason "user-abort"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    adapter = FathomAdapter(eng)
    events = promote_audit_facts(adapter)
    assert len(events) == 2, f"expected 2 events; got {len(events)}"
    for ev in events:
        assert isinstance(ev, BosunAuditEvent)
        assert ev.type == "bosun_audit"
        assert ev.pack_id == "stargraph.bosun.audit"
        assert ev.pack_version == "1.0"
        assert ev.run_id == "rX"
    kinds = sorted(ev.fact["kind"] for ev in events)
    assert kinds == ["cancel", "transition"]


def test_audit_pack_round_trips_through_jsonl_sink(tmp_path: Path) -> None:
    """Promoted ``BosunAuditEvent``s flow through :class:`JSONLAuditSink`
    without parallel sink (single-sink invariant per Resolved Decision #5)."""
    eng = _fresh_audit_engine()
    eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
        '(stargraph.tool_call (_run_id "rZ") (_step 3) (name "search"))'
    )
    eng._env.run()  # pyright: ignore[reportPrivateUsage]
    adapter = FathomAdapter(eng)
    events = promote_audit_facts(adapter)
    assert len(events) == 1

    log_path = tmp_path / "audit.jsonl"

    async def _drive() -> None:
        sink = JSONLAuditSink(log_path)
        try:
            for ev in events:
                await sink.write(ev)
        finally:
            await sink.close()

    asyncio.run(_drive())

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"expected 1 audit line; got {lines!r}"
    rec = json.loads(lines[0])
    assert rec["type"] == "bosun_audit"
    assert rec["pack_id"] == "stargraph.bosun.audit"
    assert rec["pack_version"] == "1.0"
    assert rec["run_id"] == "rZ"
    assert rec["fact"]["kind"] == "tool_call"
    assert rec["fact"]["detail"] == "search"
