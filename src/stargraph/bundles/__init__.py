# SPDX-License-Identifier: Apache-2.0
"""stargraph.bundles -- prebuilt subgraph bundles (P3b, plan §Phase-3).

Each bundle is a packaged directory holding a ``SKILL.md`` (Claude-
compatible skill wrapper), a ``graph.yaml`` (IR the SKILL.md's
``subgraph:`` key points at), and -- when the graph routes on state
values -- a state module the IR's ``state_class`` references. Bundles
run standalone (``stargraph run $(python -c "import stargraph.bundles as
b; print(b.bundle_path('rag-qa'))")/graph.yaml``) or mount as a
rule-routed child via ``kind: subgraph`` + ``spec: <path>``.

Loop bundles follow one shape: a ``template`` node re-injects the
judge's ``rationale`` into the field the generator reads, so every
retry sees why the last attempt failed; Fathom rules (``when`` mapping
sugar) route on the judge's mirrored ``verdict``.
"""

from __future__ import annotations

from pathlib import Path

from stargraph.errors import StargraphRuntimeError

__all__ = ["BUNDLE_NAMES", "bundle_path", "list_bundles"]

#: User-facing bundle names (kebab-case; directories use underscores).
BUNDLE_NAMES: tuple[str, ...] = (
    "coding-agent",
    "deep-research",
    "evaluator-optimizer",
    "hitl-approval",
    "orchestrator-workers",
    "rag-qa",
    "triage-router",
)

_ROOT = Path(__file__).resolve().parent


def list_bundles() -> list[str]:
    """The shipped bundle names, sorted."""
    return list(BUNDLE_NAMES)


def bundle_path(name: str) -> Path:
    """Directory of bundle ``name`` (holds ``SKILL.md`` + ``graph.yaml``)."""
    if name not in BUNDLE_NAMES:
        raise StargraphRuntimeError(
            f"unknown bundle {name!r}",
            hint=f"one of: {', '.join(BUNDLE_NAMES)}",
        )
    return _ROOT / name.replace("-", "_")
