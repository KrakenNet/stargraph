# SPDX-License-Identifier: Apache-2.0
"""stargraph.audit -- append-only audit sink (FR-22, design §3.12).

Phase 1 POC ships :class:`stargraph.audit.jsonl.JSONLAuditSink` -- an
``O_APPEND`` JSONL writer that emits one ``orjson``-encoded
:data:`stargraph.runtime.Event` per line. The :class:`AuditSink`
``Protocol`` pins the contract used by ``Graph.start(audit_sink=...)``
in subsequent tasks (1.27).

Ed25519 per-record signing (design §3.12) is **deferred to a later
phase**; the current sink writes unsigned records only. See
:mod:`stargraph.audit.jsonl` for the deferral TODO.
"""

from __future__ import annotations

from stargraph.audit.jsonl import AuditSink, JSONLAuditSink

__all__ = [
    "AuditSink",
    "JSONLAuditSink",
]
