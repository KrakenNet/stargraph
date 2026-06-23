# SPDX-License-Identifier: Apache-2.0
"""``pii_guard`` cross-cutting governance hooks.

Two concerns:

* :func:`authorize_action` — default-deny for the PII case only. A
  :class:`~stargraph.plugin.types.BosunAction` whose ``target`` names a
  PII-sensitive capability is denied unless its ``action_kind`` is a
  read/redaction verb. Everything else returns ``None`` to abstain so other
  plugins (and Bosun's own rule packs) keep their turn under the firstresult
  semantics of the hookspec.
* :func:`before_tool_call` / :func:`after_tool_call` — lightweight audit.
  Each tool invocation appends a safe record to :data:`auditor`, an in-memory
  module-level object the test reads. The note carries only tool identity and
  result shape — never raw arguments — so the audit trail itself stays clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from stargraph.plugin._markers import hookimpl

if TYPE_CHECKING:
    from stargraph.plugin.types import BosunAction, ToolCall, ToolResult

__all__ = ["AuditRecord", "Auditor", "auditor"]

# ``target`` substrings that mark a capability as PII-sensitive. The match is a
# case-insensitive substring test so both ``store:pii:write`` and
# ``customer.personal_data`` trip the gate.
_PII_TARGET_MARKERS: tuple[str, ...] = ("pii", "personal_data", "personal-data")

# ``action_kind`` verbs that are always safe for PII targets: pure reads and
# redactions never widen exposure, so they bypass the default-deny.
_SAFE_KINDS: frozenset[str] = frozenset({"read", "redact", "redaction"})


@dataclass(slots=True, frozen=True)
class AuditRecord:
    """One audited tool invocation. ``note`` is a redacted/safe summary."""

    tool_id: str
    note: str


@dataclass(slots=True)
class Auditor:
    """In-memory audit sink the hooks append to (test reads ``records``)."""

    records: list[AuditRecord] = field(default_factory=list[AuditRecord])

    def clear(self) -> None:
        """Drop all recorded entries (test isolation)."""
        self.records.clear()


auditor = Auditor()
"""Module-level audit sink shared by the observation hooks."""


def _is_pii_target(target: str) -> bool:
    lowered = target.lower()
    return any(marker in lowered for marker in _PII_TARGET_MARKERS)


@hookimpl
def authorize_action(action: BosunAction) -> bool | None:
    """Deny PII-sensitive non-read/redaction actions; abstain otherwise.

    Returns ``False`` only when ``action.target`` is PII-sensitive and
    ``action.action_kind`` is not a read/redaction verb. Returns ``None`` for
    every other action so the next plugin in the firstresult chain decides.
    """
    if _is_pii_target(action.target) and action.action_kind.lower() not in _SAFE_KINDS:
        return False
    return None


@hookimpl
def before_tool_call(call: ToolCall) -> None:
    """Record that a tool is about to run (identity only, no raw args)."""
    tool_id = f"{call.namespace}.{call.tool_name}"
    auditor.records.append(
        AuditRecord(tool_id=tool_id, note=f"before: {len(call.args)} arg(s)"),
    )


@hookimpl
def after_tool_call(call: ToolCall, result: ToolResult) -> None:
    """Record that a tool finished (identity + result shape, no payload)."""
    tool_id = f"{call.namespace}.{call.tool_name}"
    note = f"after: {len(result.output)} output key(s), replayed={result.replayed}"
    auditor.records.append(AuditRecord(tool_id=tool_id, note=note))
