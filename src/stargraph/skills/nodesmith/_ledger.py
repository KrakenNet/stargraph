# SPDX-License-Identifier: Apache-2.0
"""File-based ledger: reflexion lessons + (spec → node) trainset + drift.

One substrate serves both loops. Idea 1 (reliability): ``recall_lessons``
feeds prior failures back into generation. Idea 2 (self-improvement):
``append_trainset`` accumulates gate-passing pairs for offline DSPy
optimization, ``drift_rate`` is the trigger signal, and ``load_compiled_demos``
lets a freshly-optimized program feed back into generation.

Everything is append-only JSONL under ``.stargraph/nodesmith/`` (override with
``NODESMITH_HOME``). No DB, no schema migration — deliberately the simplest
thing that lets pairs flow.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

LESSONS_FILE = "lessons.jsonl"
TRAINSET_FILE = "trainset.jsonl"
COMPILED_FILE = "compiled.json"

_TOKEN = re.compile(r"[a-z0-9]+")


def home() -> Path:
    base = Path(os.environ.get("NODESMITH_HOME", ".stargraph/nodesmith"))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")


# --------------------------------------------------------------------------- #
# Reflexion lessons (idea 1)
# --------------------------------------------------------------------------- #
def append_lesson(*, brief: str, failed_kind: str, finding: str, attempts: int) -> None:
    _append_jsonl(
        home() / LESSONS_FILE,
        {
            "ts": _now(),
            "brief": brief,
            "failed_kind": failed_kind,
            "finding": finding[:800],
            "attempts": attempts,
        },
    )


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def recall_lessons(brief: str, *, limit: int = 3) -> list[str]:
    """Return up to ``limit`` past failures most relevant to ``brief``.

    Scored by token overlap with the brief; ties broken by recency (lessons
    are appended in time order, so later rows win). Returns human-readable
    strings ready to drop into the generation prompt.
    """
    rows = _read_jsonl(home() / LESSONS_FILE)
    if not rows:
        return []
    want = _tokens(brief)
    scored = [(len(want & _tokens(r.get("brief", ""))), idx, r) for idx, r in enumerate(rows)]
    scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
    out: list[str] = []
    for overlap, _idx, r in scored[:limit]:
        if overlap == 0 and len(out) >= 1:
            break  # only surface irrelevant lessons if we have nothing better
        out.append(f"[{r.get('failed_kind', '?')}] {r.get('finding', '')}")
    return out


# --------------------------------------------------------------------------- #
# Trainset + drift (idea 2)
# --------------------------------------------------------------------------- #
def append_trainset(record: dict[str, Any]) -> None:
    """Append one gate-passing ``(spec → node)`` pair. Stamps ``ts`` if absent."""
    record.setdefault("ts", _now())
    _append_jsonl(home() / TRAINSET_FILE, record)


def load_trainset() -> list[dict[str, Any]]:
    return _read_jsonl(home() / TRAINSET_FILE)


def drift_rate(window: int = 20) -> float:
    """Rolling first-try pass rate over the last ``window`` recorded builds.

    First-try = gate passed with ``attempts == 1`` (the generator nailed it
    with no repair). A falling rate is the signal to re-optimize. Returns
    ``1.0`` when there is no history yet (no evidence of drift).
    """
    rows = load_trainset()
    if not rows:
        return 1.0
    recent = rows[-window:]
    first_try = sum(1 for r in recent if int(r.get("attempts", 1)) == 1)
    return first_try / len(recent)


def load_compiled_demos() -> list[dict[str, Any]] | None:
    """Few-shot demos written by the optimizer, if any (idea 2 → idea 1)."""
    path = home() / COMPILED_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    demos = data.get("demos")
    return cast("list[dict[str, Any]]", demos) if isinstance(demos, list) else None
