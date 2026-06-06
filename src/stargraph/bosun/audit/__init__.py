# SPDX-License-Identifier: Apache-2.0
"""Bosun ``audit`` reference pack — Phase-4 implementation (task 4.2).

The pack's ``rules.clp`` asserts ``bosun.audit`` facts on seven kinds of
audit-relevant Stargraph events (transition, tool_call, node_run, respond,
cancel, pause, artifact_write).

This module ships the **fact-watcher seam**: a stateless query helper
that converts ``bosun.audit`` working-memory facts into typed
:class:`~stargraph.runtime.events.BosunAuditEvent` instances. The shape is
intentionally pull-based (callers query at evaluation boundaries) rather
than push-based (CLIPS callback) — mirroring the pull pattern Stargraph
already uses for ``stargraph_action`` extraction in
:meth:`stargraph.fathom.FathomAdapter.evaluate`.

Single-sink invariant (Resolved Decision #5, design §7.2): the events
returned here are pushed to the existing :class:`EventBus`; the
:class:`~stargraph.audit.jsonl.JSONLAuditSink` consumes them through the
same ``write(ev: Event)`` path every other typed Event uses. No parallel
sink.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from stargraph.runtime.events import BosunAuditEvent

if TYPE_CHECKING:
    from stargraph.fathom import FathomAdapter

__all__ = ["promote_audit_facts"]

_PACK_ID = "stargraph.bosun.audit"
_PACK_VERSION = "1.0"


def promote_audit_facts(
    adapter: FathomAdapter,
    *,
    pack_id: str = _PACK_ID,
    pack_version: str = _PACK_VERSION,
) -> list[BosunAuditEvent]:
    """Drain ``bosun.audit`` facts from the adapter and return typed events.

    Queries the wrapped Fathom engine for ``bosun.audit`` facts and
    builds one :class:`BosunAuditEvent` per fact. Each event carries the
    full slot payload under ``fact`` so downstream sinks have the raw
    data; ``run_id`` and ``step`` are also lifted into the typed envelope
    for index-friendly filtering.

    The function is a pure read — it does NOT retract the facts. Callers
    that need at-most-once promotion semantics should retract after
    pushing to the bus (the bus side has its own dedup if needed).
    """
    # Use the underlying CLIPS env directly: ``Engine.query`` requires the
    # template to be registered in ``_template_registry`` (it's a
    # validation hook for fleet-scope resolution), but pack-local
    # ``deftemplate`` constructs built via raw CLIPS source live in the
    # CLIPS env without that mirror. The env-level walk is the canonical
    # surface for "give me every fact of template X".
    env = adapter.engine._env  # pyright: ignore[reportPrivateUsage]
    try:
        tmpl = env.find_template("bosun.audit")
    except Exception:
        return []
    facts: list[dict[str, object]] = [dict(f) for f in tmpl.facts()]
    now = datetime.now(UTC)
    ts_iso = now.isoformat()
    events: list[BosunAuditEvent] = []
    for fact in facts:
        run_id = str(fact.get("run_id", ""))
        step_raw = fact.get("step", 0)
        if isinstance(step_raw, int):
            step = step_raw
        elif isinstance(step_raw, (str, float)):
            try:
                step = int(step_raw)
            except (TypeError, ValueError):
                step = 0
        else:
            step = 0
        # Provenance bundle (FR-55, AC-11.2): ``origin="system"`` because
        # the audit fact is engine-emitted (the CLIPS rule fired inside
        # the stargraph.bosun.audit pack); ``source`` carries the pack id so
        # downstream lineage tooling can attribute the fact back to the
        # pack version it came from.
        provenance: dict[str, object] = {
            "origin": "system",
            "source": pack_id,
            "run_id": run_id,
            "step": step,
            "confidence": 1.0,
            "timestamp": ts_iso,
        }
        events.append(
            BosunAuditEvent(
                run_id=run_id,
                step=step,
                ts=now,
                pack_id=pack_id,
                pack_version=pack_version,
                fact=dict(fact),
                provenance=provenance,
            )
        )
    return events
