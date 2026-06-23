# SPDX-License-Identifier: Apache-2.0
"""RAG retrieval primitives — domain-agnostic ranking + formatting.

Lexical (token-overlap) ranking over small, curated corpora, with clipping and
a stable prompt-block format. Each smith builds its own corpus (which repo dirs,
which ledger field) on top of these helpers; an embedding-backed ranker is a
drop-in upgrade if a corpus ever outgrows lexical retrieval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

_TOKEN = re.compile(r"[a-z0-9]+")
DEFAULT_MAX_SNIPPET_CHARS = 1500


@dataclass(frozen=True)
class Snippet:
    """One retrieved grounding chunk: where it came from + its (clipped) text."""

    source: str
    text: str


def tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def clip(text: str, max_chars: int = DEFAULT_MAX_SNIPPET_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n# …(truncated)"


def rank(brief: str, items: list[tuple[str, str]], k: int) -> list[tuple[str, str]]:
    """Top-``k`` ``(label, text)`` items by token overlap of *text* with *brief*.

    Zero-overlap items are dropped (noise, not grounding). Ties break by the
    original order (stable), so callers can pre-sort for deterministic output.
    """
    want = tokens(brief)
    scored = [
        (len(want & tokens(text)), idx, label, text) for idx, (label, text) in enumerate(items)
    ]
    scored.sort(key=lambda t: (t[0], -t[1]), reverse=True)
    return [(label, text) for overlap, _idx, label, text in scored[:k] if overlap > 0]


def interleave(a: list[Snippet], b: list[Snippet], k: int) -> list[Snippet]:
    """Interleave two snippet lists (so neither corpus crowds the other out), capped at ``k``."""
    merged: list[Snippet] = []
    for x, y in zip(a, b, strict=False):
        merged.extend((x, y))
    merged.extend(a[len(b) :])
    merged.extend(b[len(a) :])
    return merged[: max(0, k)]


def format_context(
    snippets: list[Snippet],
    header: str = "Relevant existing code and accepted examples:",
) -> str:
    """Render snippets into a prompt block (empty string when there are none)."""
    if not snippets:
        return ""
    blocks = [f"### {s.source}\n{s.text}" for s in snippets]
    return f"{header}\n\n" + "\n\n".join(blocks)


def assemble_context(
    brief: str,
    *,
    k: int,
    repo_snippets: Callable[[str, int], list[Snippet]],
    recall_examples: Callable[..., list[dict[str, Any]]],
    source_field: str,
) -> list[Snippet]:
    """A smith's grounding for ``brief``: its repo corpus interleaved with the
    gate-accepted ledger pairs, capped at ``k``.

    The empty-brief guard, the ledger-pair rendering (``# brief: …`` + the accepted
    artifact source read from ``source_field``), and the interleave are identical
    for every smith; only the repo corpus (``repo_snippets``) and the ledger source
    field differ. ``recall_examples`` is the smith's ``_ledger.recall_examples``.
    """
    if not brief.strip():
        return []
    ledger = [
        Snippet(
            source=f"ledger:{str(row.get('id', ''))[:12]} (accepted)",
            text=clip(f"# brief: {row.get('brief', '')}\n{row.get(source_field, '')}"),
        )
        for row in recall_examples(brief, limit=k)
    ]
    return interleave(repo_snippets(brief, k), ledger, k)
