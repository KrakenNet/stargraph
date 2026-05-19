# SPDX-License-Identifier: Apache-2.0
"""Pack-test helpers — Fathom CLIPS construct loading.

Mirrors the pattern used by ``tests/integration/bosun/_helpers.py`` in the
Harbor repo: walk a ``rules.clp`` file, strip comments, split into
top-level s-expressions, and build each construct into the engine's CLIPS
environment so a per-construct compile error surfaces precisely.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fathom import Engine

PACK_ROOT = Path(__file__).resolve().parent.parent / "rules"


def strip_comments(src: str) -> str:
    """Drop CLIPS line-comments while respecting string literals.

    CLIPS comments start with ``;`` and run to end-of-line, EXCEPT when the
    ``;`` lives inside a double-quoted string. The minimal-but-correct
    walker tracks quote state across newlines (CLIPS allows multi-line
    strings) so a ``;`` inside a docstring is preserved.
    """
    out: list[str] = []
    in_string = False
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                # preserve escaped char as-is
                out.append(src[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ";":
            # skip to end of line
            while i < n and src[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_constructs(src: str) -> list[str]:
    """Return each top-level s-expression in *src* as a string."""
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


def load_pack_rules(engine: Engine, pack_id: str) -> int:
    """Load every construct from ``rules/<pack_id>/rules.clp``."""
    rules_path = PACK_ROOT / pack_id / "rules.clp"
    src = rules_path.read_text(encoding="utf-8")
    count = 0
    for construct in split_constructs(src):
        engine._env.build(construct)  # pyright: ignore[reportPrivateUsage]
        count += 1
    return count


def violations(engine: Engine) -> list[dict[str, object]]:
    """Return all current ``bosun.violation`` facts as plain dicts."""
    return [
        dict(v)
        for v in engine._env.find_template("bosun.violation").facts()  # pyright: ignore[reportPrivateUsage]
    ]


def facts_of(engine: Engine, template: str) -> list[dict[str, object]]:
    """Return all facts of *template* as plain dicts."""
    return [
        dict(f)
        for f in engine._env.find_template(template).facts()  # pyright: ignore[reportPrivateUsage]
    ]
