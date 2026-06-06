# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for the four ``stargraph.bosun.*`` pack integration tests.

The helpers strip CLIPS line-comments and split a ``rules.clp`` file
into individual top-level s-expressions so each construct can be
fed to ``fathom.Engine._env.build`` (the public path Fathom exposes for
raw CLIPS source via :meth:`Engine.load_clips_function` is single-block;
splitting is needed to surface a precise per-construct error if a rule
fails to compile).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fathom import Engine

PACK_ROOT = Path(__file__).parent.parent.parent.parent / "src" / "stargraph" / "bosun"


def strip_comments(src: str) -> str:
    """Drop everything after the first ``;`` on each line (CLIPS comments)."""
    out: list[str] = []
    for line in src.splitlines():
        idx = line.find(";")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def split_constructs(src: str) -> list[str]:
    """Walk the source and return each top-level s-expression as a string.

    Skips whitespace between constructs. Comments must be stripped first.
    """
    src = strip_comments(src)
    constructs: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in src:
        if depth == 0 and ch.isspace():
            continue
        cur.append(ch)
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                constructs.append("".join(cur))
                cur = []
    return [c for c in constructs if c.strip()]


def load_pack_rules(engine: Engine, pack_name: str) -> int:
    """Load all constructs from ``src/stargraph/bosun/<pack_name>/rules.clp``.

    Returns the number of constructs built. Raises whatever exception
    Fathom surfaces if a single construct fails to compile (so the test
    output points to the exact rule).
    """
    rules_path = PACK_ROOT / pack_name / "rules.clp"
    src = rules_path.read_text(encoding="utf-8")
    count = 0
    for construct in split_constructs(src):
        engine._env.build(construct)  # pyright: ignore[reportPrivateUsage]
        count += 1
    return count


# CLIPS template stubs that the audit pack reads. Real Stargraph runtime
# declares these via :class:`stargraph.fathom.FathomAdapter`; the integration
# test bootstraps minimal versions inline so the audit rules can compile
# against them without dragging in the full provenance pipeline.
STARGRAPH_FACT_STUBS = """
(deftemplate stargraph.transition (slot _run_id) (slot _step) (slot kind))
(deftemplate stargraph.tool_call (slot _run_id) (slot _step) (slot name))
(deftemplate stargraph.node_run (slot _run_id) (slot _step) (slot node_id))
(deftemplate stargraph.respond (slot _run_id) (slot _step) (slot caller))
(deftemplate stargraph.cancel (slot _run_id) (slot _step) (slot reason))
(deftemplate stargraph.pause (slot _run_id) (slot _step) (slot reason))
(deftemplate stargraph.artifact_write (slot _run_id) (slot _step) (slot artifact_id))
"""


def install_stargraph_fact_stubs(engine: Engine) -> None:
    """Install the inline ``stargraph.*`` template stubs for the audit pack."""
    for construct in split_constructs(STARGRAPH_FACT_STUBS):
        engine._env.build(construct)  # pyright: ignore[reportPrivateUsage]
