# SPDX-License-Identifier: Apache-2.0
"""Triage — classify an incoming item and pick a route with a CLIPS rule pack.

A ``workflow`` skill with NO LLM: the node loads the bundle-local ``rules.clp``
into a fresh Fathom engine, asserts the item's signals (``item.signal``) and the
tokenized subject+body (``item.keyword``) as facts, fires the rules, and reads
back the resulting ``triage`` fact for the category / route / priority decision.
The names of the rules that fired are collected into ``matched_rules``. The
pack carries a default fallthrough rule, so there is always a route.

The construct-splitter + ``fresh_engine`` loader is a minimal local copy of the
``stargraph.skills.shipwright._pack`` pattern, pointed at this skill directory's
own ``rules.clp`` via :data:`pathlib.Path` ``(__file__)`` — the skill bundle
ships its rules with it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.errors import StargraphRuntimeError
from stargraph.nodes.base import ExecutionContext, NodeBase

if TYPE_CHECKING:
    from fathom import Engine
    from pydantic import BaseModel

_RULES_PATH = Path(__file__).resolve().parent.parent / "rules.clp"
_TOKEN_RE = re.compile(r"[a-z0-9]+")


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
    """Create a `default_decision='deny'` Fathom engine for the triage loader."""
    from fathom import Engine

    return Engine(default_decision="deny")


def load_rules(engine: Engine) -> int:
    """Load this skill's bundle-local ``rules.clp`` into ``engine``.

    Returns the number of top-level constructs built.
    """
    src = _RULES_PATH.read_text(encoding="utf-8")
    count = 0
    for construct in _split_constructs(src):
        engine._env.build(construct)  # pyright: ignore[reportPrivateUsage]
        count += 1
    return count


def _clips_str(value: str) -> str:
    """Quote a Python string as a CLIPS string literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _tokenize(*parts: str) -> list[str]:
    """Lowercase alphanumeric tokens drawn from subject + body, de-duplicated."""
    seen: dict[str, None] = {}
    for part in parts:
        for tok in _TOKEN_RE.findall(part.lower()):
            seen.setdefault(tok, None)
    return list(seen)


class Triage(NodeBase):
    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        del ctx  # rule evaluation needs no per-run context
        subject = str(getattr(state, "subject", "") or "")
        body = str(getattr(state, "body", "") or "")
        signals: dict[str, Any] = getattr(state, "signals", {}) or {}

        eng = fresh_engine()
        load_rules(eng)

        for name, value in signals.items():
            eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
                f'(item.signal (name "{_clips_str(str(name))}") (value "{_clips_str(str(value))}"))'
            )
        for token in _tokenize(subject, body):
            eng._env.assert_string(  # pyright: ignore[reportPrivateUsage]
                f'(item.keyword (value "{_clips_str(token)}"))'
            )

        eng._env.run()  # pyright: ignore[reportPrivateUsage]

        decisions = [
            dict(raw)
            for raw in eng._env.find_template("triage").facts()  # pyright: ignore[reportPrivateUsage]
        ]
        if not decisions:
            raise StargraphRuntimeError("triage rule pack produced no decision fact")

        matched_rules = [str(d["rule"]) for d in decisions]
        # Prefer a specific decision over the default fallthrough when both fired.
        specific = [d for d in decisions if str(d["rule"]) != "triage-default-queue"]
        chosen = specific[0] if specific else decisions[0]

        return {
            "category": str(chosen["category"]),
            "route": str(chosen["route"]),
            "priority": str(chosen["priority"]),
            "matched_rules": matched_rules,
        }
