# SPDX-License-Identifier: Apache-2.0
""":mod:`harbor.serve.inspect` -- read-only timeline + state + fact-diff views (FR-26, FR-27).

The implementation body for the ``harbor inspect <run_id>`` CLI surface
(design §3.1 ``inspect.py`` row, AC-9.1, AC-9.2). The CLI shim in
:mod:`harbor.cli.inspect` parses arguments and delegates here; this
module owns the read-only inspector contract.

Three views per design §3.1:

* **Timeline view** (default) -- one line per checkpointed step with
  ``(step, transition_type, node_id, tool_calls, rule_firings)``
  derived from the Checkpointer's per-step rows + the JSONL audit log
  filtered by ``run_id``. The audit log is seeked via the
  :class:`harbor.serve.history.RunHistory` ``run_event_offsets`` index
  (design §6.5) so the timeline does not full-scan the JSONL for each
  lookup.

* **State-at-step view** (``--step N``) -- prints the IR-canonical
  ``state`` dict from the Checkpointer at step ``N`` as one
  pretty-printed JSON document.

* **Fact-diff view** (``--diff N M``) -- prints CLIPS facts added /
  removed between steps ``N`` and ``M`` from the Checkpointer's
  ``clips_facts`` rows.

**Read-only invariant** (FR-26, design §3.1): this module never opens
the Checkpointer DB or the JSONL audit file in write mode. Every code
path goes through :meth:`Checkpointer.read_at_step`,
:meth:`Checkpointer.read_latest`, :meth:`Checkpointer.list_runs`,
:meth:`RunHistory.get_event_offset`, or a plain ``"rb"`` file open.
No checkpoint writes, no audit emissions, no event-bus side-effects.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

import orjson

from harbor.audit.jsonl import unwrap_audit_record

if TYPE_CHECKING:
    from pathlib import Path

    from harbor.checkpoint.protocol import Checkpoint, Checkpointer
    from harbor.serve.history import RunHistory

__all__ = [
    "TimelineRow",
    "build_timeline",
    "fact_diff",
    "format_timeline",
    "state_at_step",
]


class TimelineRow:
    """One timeline-view row: ``(step, transition_type, node_id, tool_calls, rule_firings)``.

    A plain dataclass-shaped object (no Pydantic validation; this is an
    internal-only render helper). Constructed by :func:`build_timeline`
    from a Checkpointer row + the JSONL events filtered by ``run_id``
    at that ``step``.
    """

    __slots__ = ("node_id", "rule_firings", "step", "tool_calls", "transition_type")

    def __init__(
        self,
        *,
        step: int,
        transition_type: str,
        node_id: str,
        tool_calls: list[str],
        rule_firings: list[str],
    ) -> None:
        self.step = step
        self.transition_type = transition_type
        self.node_id = node_id
        self.tool_calls = tool_calls
        self.rule_firings = rule_firings

    def as_text(self) -> str:
        """Render as ``step=N transition=... node=... tool_calls=[...] rules=[...]``."""
        tools = ",".join(self.tool_calls) if self.tool_calls else "-"
        rules = ",".join(self.rule_firings) if self.rule_firings else "-"
        return (
            f"step={self.step} transition={self.transition_type} "
            f"node={self.node_id} tool_calls=[{tools}] rules=[{rules}]"
        )


# --------------------------------------------------------------------------- #
# JSONL event helpers (read-only)                                             #
# --------------------------------------------------------------------------- #


def _read_run_events(
    history: RunHistory | None,
    jsonl_path: Path | None,
    run_id: str,
) -> dict[int, list[dict[str, Any]]]:
    """Return ``{step: [event, ...]}`` for all events of ``run_id`` -- read-only.

    Single-pass walk of the JSONL file (or just the slice of lines for
    ``run_id`` when the ``run_event_offsets`` index is wired). The
    timeline view calls this once per :func:`build_timeline` invocation
    and indexes by step in O(1); per-step reads would re-walk the file.

    The ``history`` argument is currently unused for the seek (the
    index records the *last* offset per ``(run_id, step)``, which is
    the event-watermark hint rather than a step-start anchor; using it
    naively would skip earlier events at the same step). Phase 5 may
    introduce a ``(run_id, step) -> (first_offset, last_offset)`` shape
    so the timeline can seek the slice; for now we full-scan the JSONL
    once per invocation. The ``history`` parameter is kept on the
    surface so the CLI shim can pass it through without a future
    breaking change when the seek lands.
    """
    del history  # documented above; reserved for the (first, last) index shape
    if jsonl_path is None or not jsonl_path.exists():
        return {}
    by_step: dict[int, list[dict[str, Any]]] = {}
    with jsonl_path.open("rb") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                record: Any = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(record, dict):
                continue
            record_dict = cast("dict[str, Any]", record)
            payload_raw = unwrap_audit_record(record_dict)
            if not isinstance(payload_raw, dict):
                continue
            payload = cast("dict[str, Any]", payload_raw)
            if payload.get("run_id") != run_id:
                continue
            ev_step = payload.get("step")
            if not isinstance(ev_step, int):
                continue
            by_step.setdefault(ev_step, []).append(payload)
    return by_step


def _summarize_events(events: list[dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    """Extract ``(transition_type, tool_calls, rule_firings)`` from per-step events.

    * ``transition_type`` -- last ``transition`` event's ``reason`` (e.g.
      ``rule:fired``) or ``"-"`` when no transition fired.
    * ``tool_calls`` -- list of ``tool_name`` from ``tool_call`` events.
    * ``rule_firings`` -- list of ``rule_id`` from ``transition`` events.
    """
    transition_type = "-"
    tool_calls: list[str] = []
    rule_firings: list[str] = []
    for ev in events:
        ev_type = ev.get("type")
        if ev_type == "tool_call":
            name = ev.get("tool_name")
            if isinstance(name, str):
                tool_calls.append(name)
        elif ev_type == "transition":
            rule_id = ev.get("rule_id")
            if isinstance(rule_id, str):
                rule_firings.append(rule_id)
            reason = ev.get("reason")
            if isinstance(reason, str):
                transition_type = reason
    return transition_type, tool_calls, rule_firings


# --------------------------------------------------------------------------- #
# Public surface                                                              #
# --------------------------------------------------------------------------- #


async def build_timeline(
    checkpointer: Checkpointer,
    run_id: str,
    *,
    history: RunHistory | None = None,
    jsonl_path: Path | None = None,
) -> list[TimelineRow]:
    """Walk all checkpoints for ``run_id`` and return :class:`TimelineRow` rows.

    Read-only: drives :meth:`Checkpointer.read_latest` to find the
    highest step, then :meth:`Checkpointer.read_at_step` per step. The
    JSONL events are seeked via ``history.get_event_offset`` -- one
    seek per step -- so the timeline build is O(steps) reads + O(steps)
    seek-then-walk-locally on the JSONL.
    """
    latest = await checkpointer.read_latest(run_id)
    if latest is None:
        return []
    by_step = _read_run_events(history, jsonl_path, run_id)
    rows: list[TimelineRow] = []
    for step in range(latest.step + 1):
        ckpt = await checkpointer.read_at_step(run_id, step)
        if ckpt is None:
            continue
        events = by_step.get(step, [])
        transition_type, tool_calls, rule_firings = _summarize_events(events)
        rows.append(
            TimelineRow(
                step=step,
                transition_type=transition_type,
                node_id=ckpt.last_node,
                tool_calls=tool_calls,
                rule_firings=rule_firings,
            )
        )
    return rows


def format_timeline(rows: list[TimelineRow]) -> str:
    """Render a list of timeline rows as one-line-per-step text.

    Empty input renders as the literal ``"timeline: <no checkpoints>"``
    so operators can distinguish "run does not exist" from "run exists
    but no checkpoints" downstream.
    """
    if not rows:
        return "timeline: <no checkpoints>"
    return "\n".join(row.as_text() for row in rows)


async def state_at_step(
    checkpointer: Checkpointer, run_id: str, step: int
) -> dict[str, Any] | None:
    """Return the IR-canonical ``state`` dict at ``(run_id, step)`` (read-only).

    ``None`` when no checkpoint exists at that step. The caller is
    responsible for surfacing the ``None`` to the operator (the CLI
    shim renders it as a non-zero exit; library callers may treat it
    as a soft miss).
    """
    ckpt = await checkpointer.read_at_step(run_id, step)
    if ckpt is None:
        return None
    return ckpt.state


def _facts_set(ckpt: Checkpoint) -> set[str]:
    """Coerce ``clips_facts`` to a string-set for diff arithmetic.

    The ``clips_facts`` column is ``list[str]`` (FR-16 ``save_facts``
    text-format) for new runs and ``list[dict]`` for legacy rows. We
    normalise to ``str(item)`` so set-difference operations work for
    both shapes -- callers care about *fact identity* (dedup via the
    string form), not the structured shape.
    """
    out: set[str] = set()
    for fact in ckpt.clips_facts:
        if isinstance(fact, str):
            out.add(fact)
        else:
            # ``orjson.dumps`` on a dict produces a stable JCS-ish form
            # for the legacy ``list[dict]`` shape. Sorting keys keeps
            # the set membership stable across runs that emit dicts in
            # different insertion orders.
            try:
                out.add(orjson.dumps(fact, option=orjson.OPT_SORT_KEYS).decode("utf-8"))
            except (TypeError, ValueError):
                out.add(repr(fact))
    return out


async def fact_diff(
    checkpointer: Checkpointer, run_id: str, step_a: int, step_b: int
) -> dict[str, list[str]] | None:
    """Return ``{"added": [...], "removed": [...]}`` between two steps.

    ``added`` are facts present at ``step_b`` but not ``step_a``;
    ``removed`` are facts present at ``step_a`` but not ``step_b``.
    Returns ``None`` when either step has no checkpoint.
    """
    a = await checkpointer.read_at_step(run_id, step_a)
    b = await checkpointer.read_at_step(run_id, step_b)
    if a is None or b is None:
        return None
    a_set = _facts_set(a)
    b_set = _facts_set(b)
    return {
        "added": sorted(b_set - a_set),
        "removed": sorted(a_set - b_set),
    }
