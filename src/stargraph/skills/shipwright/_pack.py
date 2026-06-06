# SPDX-License-Identifier: Apache-2.0
"""Pack loader for ``stargraph.bosun.shipwright.*`` sub-packs.

Mirrors ``tests/integration/bosun/_helpers.py`` so production nodes
(``GapCheck``, ``FixLoop``) and tests share one canonical loader. The
loader splits ``rules.clp`` into top-level constructs and feeds each to
``fathom.Engine._env.build`` — the per-construct path Fathom exposes for
precise compile-error attribution.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fathom import Engine

_PACKS_ROOT = Path(__file__).resolve().parents[2] / "bosun" / "shipwright"


def _strip_comments(src: str) -> str:
    out: list[str] = []
    for line in src.splitlines():
        idx = line.find(";")
        if idx >= 0:
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


def _split_constructs(src: str) -> list[str]:
    src = _strip_comments(src)
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


def fresh_engine() -> Engine:
    """Create a `default_decision='deny'` Fathom engine for shipwright loaders."""
    from fathom import Engine

    return Engine(default_decision="deny")


def load_pack(engine: Engine, name: str) -> int:
    """Load `stargraph.bosun.shipwright.<name>` rules into `engine`. Returns construct count."""
    rules_path = _PACKS_ROOT / name / "rules.clp"
    src = rules_path.read_text(encoding="utf-8")
    count = 0
    for construct in _split_constructs(src):
        engine._env.build(construct)  # pyright: ignore[reportPrivateUsage]
        count += 1
    return count
