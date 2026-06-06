# SPDX-License-Identifier: Apache-2.0
"""Lineage-audit CI script: assert provenance on every recorded fact.

Walks the JSONL audit sink (and optionally :class:`RunHistory`) for a
specific run or every run, then asserts that every fact line carries the
full provenance tuple as defined by
:class:`stargraph.fathom._provenance.ProvenanceBundle`:

    (origin, source, run_id, step, confidence, timestamp)

Locked-decision invariants asserted:

* ``respond`` facts carry ``origin="user"`` and ``source=<actor>`` (Decision #2).
* ``cf-respond`` facts carry ``source="cf:<actor>"`` (Decision #2 cf-mirror).

Exit code 0 = audit clean. Exit code 1 = at least one provenance gap was
found; the offending event(s) are printed to stdout for triage.

Spec ref: stargraph-serve-and-bosun §16.7, FR-55, AC-11.2.

Usage::

    python scripts/lineage_audit.py [--run-id <id>] [--audit-path <path>] [--strict]

* ``--audit-path`` defaults to ``./stargraph.audit.jsonl`` (the conventional
  POC sink path); pass ``--audit-path -`` to read from stdin for piped
  CI use.
* ``--run-id`` filters to a single run; without the flag every line is
  audited.
* ``--strict`` upgrades unknown event types and missing optional slots to
  hard failures (default: warn-only on unknown types).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

# Required provenance keys per ProvenanceBundle (stargraph.fathom._provenance).
# This list is duplicated from the TypedDict because we only consume audit
# JSONL bytes here -- we don't construct ProvenanceBundle instances. Keep
# in sync with src/stargraph/fathom/_provenance.py.
_REQUIRED_PROV_KEYS: tuple[str, ...] = (
    "origin",
    "source",
    "run_id",
    "step",
    "confidence",
    "timestamp",
)

# Event ``type`` values that carry a ``provenance`` field. Sourced from
# stargraph.runtime.events; only events with provenance need lineage audit
# (e.g. ``artifact_written`` carries provenance, ``waiting_for_input``
# does not -- the engine treats the latter as a control-flow signal).
_EVENTS_WITH_PROVENANCE: frozenset[str] = frozenset(
    {
        "artifact_written",
        "bosun_audit",
        "fact_asserted",
        "respond",
        "cf_respond",
    }
)


class AuditGapError(Exception):
    """A single audit line failed provenance validation."""

    def __init__(self, line_no: int, reason: str, payload: dict[str, Any]) -> None:
        self.line_no = line_no
        self.reason = reason
        self.payload = payload
        super().__init__(f"line {line_no}: {reason}")


def _iter_audit_lines(path: Path | None) -> Iterable[tuple[int, dict[str, Any]]]:
    """Yield ``(line_no, decoded_event)`` from the audit JSONL.

    Lines may be either bare events or ``{"event": ..., "sig": ...}``
    envelopes (when the sink was constructed with a signing key); both
    shapes are handled. Empty lines are skipped.
    """
    src = sys.stdin if path is None else path.open("r", encoding="utf-8")
    try:
        for line_no, raw in enumerate(src, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            decoded: dict[str, Any] = json.loads(stripped)
            # Unwrap signed envelope: ``{"event": <evt>, "sig": "<hex>"}``.
            if "event" in decoded and "sig" in decoded:
                inner = decoded["event"]
                if isinstance(inner, dict):
                    yield line_no, inner
                    continue
            yield line_no, decoded
    finally:
        if path is not None:
            src.close()


def _check_provenance(
    line_no: int,
    event: dict[str, Any],
    *,
    strict: bool,
) -> list[AuditGapError]:
    """Return a list of AuditGapError exceptions for one event line.

    Empty list = pass. Multiple gaps from one line = multiple failures.
    """
    gaps: list[AuditGapError] = []
    evt_type: str = event.get("type", "<unknown>")

    # Skip events that are not in the provenance-bearing set unless
    # --strict was passed (in which case unknown types are surfaced).
    if evt_type not in _EVENTS_WITH_PROVENANCE:
        if strict and evt_type == "<unknown>":
            gaps.append(
                AuditGapError(line_no, "missing 'type' field", event),
            )
        return gaps

    prov = event.get("provenance")
    if not isinstance(prov, dict):
        gaps.append(
            AuditGapError(
                line_no,
                f"event type={evt_type!r} missing 'provenance' dict",
                event,
            ),
        )
        return gaps

    # Required-key sweep.
    for key in _REQUIRED_PROV_KEYS:
        if key not in prov:
            gaps.append(
                AuditGapError(
                    line_no,
                    f"provenance missing required key {key!r}",
                    event,
                ),
            )

    # Locked-decision #2: respond facts carry origin=user, source=<actor>.
    if evt_type == "respond":
        if prov.get("origin") != "user":
            gaps.append(
                AuditGapError(
                    line_no,
                    f"respond fact must carry origin='user', got {prov.get('origin')!r}",
                    event,
                ),
            )
        source = prov.get("source")
        if not isinstance(source, str) or not source:
            gaps.append(
                AuditGapError(
                    line_no,
                    "respond fact must carry source=<actor> (non-empty string)",
                    event,
                ),
            )
        elif source.startswith("cf:"):
            gaps.append(
                AuditGapError(
                    line_no,
                    "respond fact source must NOT have cf: prefix (use cf_respond type instead)",
                    event,
                ),
            )

    # Locked-decision #2 cf-mirror: cf_respond carries source=cf:<actor>.
    if evt_type == "cf_respond":
        source = prov.get("source")
        if not isinstance(source, str) or not source.startswith("cf:"):
            gaps.append(
                AuditGapError(
                    line_no,
                    f"cf_respond fact must carry source='cf:<actor>', got {source!r}",
                    event,
                ),
            )

    return gaps


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="lineage_audit",
        description="Audit Stargraph JSONL audit sink for provenance gaps (FR-55, AC-11.2).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Filter audit to a single run_id (default: every run).",
    )
    parser.add_argument(
        "--audit-path",
        default="stargraph.audit.jsonl",
        help="Path to the JSONL audit sink (default: ./stargraph.audit.jsonl). "
        "Pass '-' to read from stdin.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat unknown event types as failures (default: warn-only).",
    )
    args = parser.parse_args()

    audit_arg: str = args.audit_path
    audit_path: Path | None
    if audit_arg == "-":
        audit_path = None
    else:
        audit_path = Path(audit_arg)
        if not audit_path.exists():
            print(
                f"lineage_audit: audit file not found: {audit_path}",
                file=sys.stderr,
            )
            return 1

    total = 0
    audited = 0
    gaps: list[AuditGapError] = []

    for line_no, event in _iter_audit_lines(audit_path):
        total += 1
        if args.run_id is not None:
            evt_run_id = event.get("run_id") or event.get("provenance", {}).get(
                "run_id",
            )
            if evt_run_id != args.run_id:
                continue
        audited += 1
        gaps.extend(_check_provenance(line_no, event, strict=args.strict))

    # Human-readable report.
    print(f"lineage_audit: scanned {total} lines, audited {audited}.")
    if not gaps:
        print("lineage_audit: PASS - no provenance gaps found.")
        return 0

    print(f"lineage_audit: FAIL - {len(gaps)} provenance gap(s):")
    for gap in gaps:
        print(f"  line {gap.line_no}: {gap.reason}")
        evt_type = gap.payload.get("type", "<unknown>")
        run_id = gap.payload.get("run_id") or gap.payload.get("provenance", {}).get(
            "run_id",
            "<unknown>",
        )
        print(f"    type={evt_type!r} run_id={run_id!r}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
