# SPDX-License-Identifier: Apache-2.0
"""Shared edit-to-gold helpers — one implementation for both the CLI and TUI.

The two front-ends differ only in how they open the editor (the TUI must
``suspend`` first) and how they report results; the buffer format, the marker
split, the re-gate, and the store are identical and live here.
"""

from __future__ import annotations

from typing import Any

from stargraph.skills.nodesmith import _ledger
from stargraph.skills.nodesmith.gate import verify_sources

# Separator between node.py and test_node.py in the single edit buffer.
MARKER = "# ======== TEST (test_node.py) — edit above for node.py ========"


def short_id(row: dict[str, Any]) -> str:
    """Display form of a row id (12-char prefix), as used in tables + messages."""
    return str(row.get("id", ""))[:12]


def build_edit_buffer(row: dict[str, Any]) -> str:
    """The text handed to ``$EDITOR``: node source, marker, test source."""
    return f"{row.get('node_source', '')}\n{MARKER}\n{row.get('test_source', '')}"


def apply_edit(ref: str, edited: str) -> tuple[bool, str]:
    """Split an edited buffer, re-gate it, and store it as gold iff it passes.

    Returns ``(ok, message)``. A failing edit is never written — the existing
    row is untouched so the caller can offer a retry.
    """
    row = _ledger.find_trainset(ref)
    if row is None:
        return False, f"no row matching '{ref}'"
    if MARKER not in edited:
        return False, f"marker line removed — keep the '{MARKER}' separator"

    node_src, test_src = (part.strip() + "\n" for part in edited.split(MARKER, 1))
    ok, results = verify_sources(
        node_src,
        test_src,
        reads=list(row.get("reads", [])),
        writes=list(row.get("writes", [])),
        fixture=dict(row.get("fixture", {})),
    )
    if not ok:
        failed = next((r for r in results if not r.passed), None)
        kind = failed.kind if failed else "?"
        msg = failed.findings[0].get("msg", "") if failed and failed.findings else "unknown"
        return False, f"gate failed ({kind}): {str(msg)[:200]}"

    updated = _ledger.update_trainset(
        ref,
        node_source=node_src,
        test_source=test_src,
        source=_ledger.SOURCE_EDITED,
        verdict="accept",
    )
    return True, f"{short_id(updated) if updated else ref} edited, re-gated, stored as gold"
