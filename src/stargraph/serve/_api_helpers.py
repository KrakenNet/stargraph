# SPDX-License-Identifier: Apache-2.0
"""Pure helpers for the Stargraph serve API factory.

Side-effect-free units extracted from :mod:`stargraph.serve.api` to keep the
app-factory module focused on route wiring. Everything here is re-exported
from :mod:`stargraph.serve.api` so the established import surface (tests +
other modules) is unchanged.

Contents:

* :data:`_LAST_EVENT_ID_RE` / :func:`_parse_last_event_id` -- WS resume cursor
  parsing (task 2.21, design §17 Decision #3).
* :func:`_audit_path_for_run` -- per-run JSONL audit path resolution.
* :func:`_replay_audit_after_cursor` -- forward JSONL replay from a cursor.
* :func:`_resolve_run_registry` / :func:`_resolve_broadcaster_registry` --
  in-memory ``deps`` registry accessors.
* :func:`_summarize` -- live :class:`GraphRun` -> :class:`RunSummary` fold.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, cast

from stargraph.audit.jsonl import unwrap_audit_record

if TYPE_CHECKING:
    from pathlib import Path

    from stargraph.checkpoint.protocol import RunSummary
    from stargraph.graph.run import GraphRun
    from stargraph.serve.broadcast import EventBroadcaster

# Listed so the (intentionally private, underscore-prefixed) helpers are an
# explicit re-export surface: :mod:`stargraph.serve.api` and the test suite
# import them from here / from ``serve.api``. Naming them in ``__all__`` marks
# the cross-module import as deliberate for pyright strict (reportPrivateUsage).
__all__ = [
    "_LAST_EVENT_ID_RE",
    "_audit_path_for_run",
    "_parse_last_event_id",
    "_replay_audit_after_cursor",
    "_resolve_broadcaster_registry",
    "_resolve_run_registry",
    "_summarize",
]


# --- WS resume cursor (task 2.21, design §17 Decision #3) --------------------

#: Strict cursor shape ``{run_id}:{step}:{seq_within_step}`` -- run_id must be
#: alphanumeric/dash/underscore; step + seq_within_step are non-negative ints.
#: Anything outside this regex is rejected with WS close 1008 (policy
#: violation) per task 2.21 constraints.
_LAST_EVENT_ID_RE = re.compile(r"^([A-Za-z0-9_-]+):(\d+):(\d+)$")


def _parse_last_event_id(raw: str) -> tuple[str, int, int] | None:
    """Strict parser for ``last_event_id`` query values.

    Returns ``(run_id, step, seq_within_step)`` on success, ``None`` on
    malformed input. Rejects empty strings, missing colons, non-digit
    components -- the WS handler closes 1008 on ``None``. Don't trust
    the client; the cursor is part of the URL surface.
    """
    match = _LAST_EVENT_ID_RE.match(raw)
    if match is None:
        return None
    return match.group(1), int(match.group(2)), int(match.group(3))


def _audit_path_for_run(deps: dict[str, Any], run_id: str) -> Path | None:
    """Resolve the per-run JSONL audit path from the deps container.

    Two resolution rungs (in order):

    1. ``deps["audit_path"]`` -- explicit override (test harness or
       lifespan factory). When set, the same file is the audit log for
       every run; replay scans it filtered on ``run_id``.
    2. ``deps["run_history"].jsonl_audit_path`` -- the
       :class:`~stargraph.serve.history.RunHistory` instance the lifespan
       factory wires (see :mod:`stargraph.cli.serve`).

    Returns ``None`` when neither rung resolves -- the WS handler then
    closes 1008 with reason "audit not found" rather than tail-spinning
    on a non-existent file.
    """
    explicit = deps.get("audit_path")
    if explicit is not None:
        return cast("Path", explicit)
    run_history: Any | None = deps.get("run_history")
    if run_history is None:
        return None
    path: Path | None = getattr(run_history, "jsonl_audit_path", None)
    return path


def _replay_audit_after_cursor(
    audit_path: Path,
    run_id: str,
    step_cursor: int,
    seq_cursor: int,
) -> list[dict[str, Any]]:
    """Read JSONL audit file and return every event strictly after the cursor.

    Walks the file forward (positional scan per design §17 Decision #3),
    decodes each line as JSON, unwraps the optional signed envelope
    (``{"event": ..., "sig": "..."}``), filters to events with
    ``run_id == run_id``, and emits those whose
    ``(step, seq_within_step)`` is strictly greater than the cursor.
    ``seq_within_step`` is implicit: the 0-indexed ordinal of the event
    within the ``(run_id, step)`` group as written to JSONL.

    Returns the list of event payload dicts (already JSON-mode shape) so
    the WS handler can ``ir_dumps``-equivalent re-serialize them or
    just send the raw line. We send the dict via ``json.dumps`` to keep
    a single hot path.

    Skipped silently:
    * Blank lines (between rotated segments).
    * Malformed JSON (truncated tails, non-dict payloads).
    * Events missing ``run_id`` / ``step`` (corrupted records).
    """
    out: list[dict[str, Any]] = []
    if not audit_path.exists():
        return out
    seq_per_step: dict[int, int] = {}
    with audit_path.open("rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                record_any: Any = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(record_any, dict):
                continue
            record = cast("dict[str, Any]", record_any)
            payload_any: Any = unwrap_audit_record(record)
            if not isinstance(payload_any, dict):
                continue
            payload = cast("dict[str, Any]", payload_any)
            ev_run_id = payload.get("run_id")
            ev_step = payload.get("step")
            if not isinstance(ev_run_id, str) or not isinstance(ev_step, int):
                continue
            if ev_run_id != run_id:
                continue
            seq = seq_per_step.get(ev_step, 0)
            seq_per_step[ev_step] = seq + 1
            # Strict ``>`` comparison: cursor points at an already-seen
            # event; replay starts at the next one (positional event_id+1
            # per design §5.6).
            if (ev_step, seq) > (step_cursor, seq_cursor):
                out.append(payload)
    return out


def _resolve_run_registry(deps: dict[str, Any]) -> dict[str, GraphRun]:
    """Pull (or lazily create) the in-memory ``run_id -> GraphRun`` registry.

    POC: lives in ``deps["runs"]``. Phase 2 task 2.30 replaces this with
    a Checkpointer-backed lookup (canonical persisted row + live handle
    map) and threads it through a proper container instead of an open
    dict.
    """
    runs: dict[str, GraphRun] | None = deps.get("runs")
    if runs is None:
        runs = {}
        deps["runs"] = runs
    return runs


def _resolve_broadcaster_registry(
    deps: dict[str, Any],
) -> dict[str, EventBroadcaster]:
    """Pull (or lazily create) the in-memory ``run_id -> EventBroadcaster`` map.

    POC: parallels :func:`_resolve_run_registry`; the WS route handler
    looks up a per-run :class:`~stargraph.serve.broadcast.EventBroadcaster`
    here. Phase 2 (task 2.30) folds this into the Checkpointer-backed
    run-handle container; for now the lifespan / test harness populates
    this dict directly when a run is registered.
    """
    bcs: dict[str, EventBroadcaster] | None = deps.get("broadcasters")
    if bcs is None:
        bcs = {}
        deps["broadcasters"] = bcs
    return bcs


def _summarize(run: GraphRun) -> RunSummary:
    """Synthesize a :class:`RunSummary` from a live :class:`GraphRun` handle.

    Mirrors the status-lattice fold used by
    :func:`stargraph.serve.lifecycle._build_run_summary` and
    :func:`stargraph.serve.respond._build_run_summary` (task 1.22 / 1.23):
    the wider :class:`GraphRun.state` Literal
    (``pending|running|paused|awaiting-input|done|cancelled|error|failed``)
    is folded onto the narrower :class:`Checkpointer.RunSummary.status`
    Literal (``running|done|failed|paused``). Phase 2 widens the
    :class:`RunSummary` Literal (task 1.2 backfill) and reads the
    canonical row from the Checkpointer instead.
    """
    from datetime import UTC, datetime

    from stargraph.checkpoint.protocol import RunSummary

    now = datetime.now(UTC)
    status_mapped: str
    if run.state == "cancelled":
        status_mapped = "failed"
    elif run.state == "awaiting-input":
        status_mapped = "paused"
    elif run.state in ("running", "done", "failed", "paused"):
        status_mapped = run.state
    else:
        status_mapped = "failed"
    return RunSummary(
        run_id=run.run_id,
        graph_hash=run.graph.graph_hash,
        started_at=now,
        last_step_at=now,
        status=status_mapped,  # pyright: ignore[reportArgumentType]
        parent_run_id=run.parent_run_id,
        # #68: surface the terminal failure reason on the run-detail view.
        error_class=run.error_class,
        error_cause=run.error_cause,
    )
