# SPDX-License-Identifier: Apache-2.0
"""File-based ledger — the substrate both smith loops feed on, domain-agnostic.

One ``Ledger`` per smith (each bound to its own ``*_HOME`` dir). Idea 1
(reliability): ``recall_lessons`` / ``recall_examples`` feed prior failures and
accepted exemplars back into generation. Idea 2 (self-improvement):
``append_trainset`` accumulates gate-passing pairs for offline DSPy optimization,
``drift_rate`` is the trigger signal, ``load_compiled_demos`` feeds an optimized
program back in.

Everything is append-only JSONL under the bound home dir. No DB, no schema
migration — deliberately the simplest thing that lets pairs flow. The artifact
*shape* (which source/test fields a row carries) is the smith's business; the
ledger only cares about ``id`` / ``ts`` / ``source`` / ``verdict`` / ``brief``.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from uuid_utils import uuid7

LESSONS_FILE = "lessons.jsonl"
TRAINSET_FILE = "trainset.jsonl"
COMPILED_FILE = "compiled.json"

# Trainset provenance: hand-authored seed | generator output | human-edited gold.
SOURCE_SEED = "seed"
SOURCE_GENERATED = "generated"
SOURCE_EDITED = "edited"

_TOKEN = re.compile(r"[a-z0-9]+")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


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


# Public alias — smiths (and their tests) read raw JSONL without reaching at a
# private name.
read_jsonl = _read_jsonl


class Ledger:
    """JSONL ledger bound to one smith's home directory.

    ``home_env`` is read fresh on every call so per-process / per-test home
    overrides take effect; the resolved path is absolute so the ledger can't
    silently fork if the cwd changes mid-process.
    """

    def __init__(self, *, home_env: str, home_default: str) -> None:
        self._home_env = home_env
        self._home_default = home_default

    def home(self) -> Path:
        base = Path(os.environ.get(self._home_env, self._home_default)).resolve()
        base.mkdir(parents=True, exist_ok=True)
        return base

    # --- reflexion lessons (idea 1) ------------------------------------------ #
    def append_lesson(self, *, brief: str, failed_kind: str, finding: str, attempts: int) -> None:
        _append_jsonl(
            self.home() / LESSONS_FILE,
            {
                "ts": _now(),
                "brief": brief,
                "failed_kind": failed_kind,
                "finding": finding[:800],
                "attempts": attempts,
            },
        )

    def recall_lessons(self, brief: str, *, limit: int = 3) -> list[str]:
        """Up to ``limit`` past failures most relevant to ``brief`` (token overlap;
        ties broken by recency). Human-readable strings ready for the prompt."""
        rows = _read_jsonl(self.home() / LESSONS_FILE)
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

    def recall_examples(self, brief: str, *, limit: int = 3) -> list[dict[str, Any]]:
        """Up to ``limit`` gate-accepted trainset pairs most relevant to ``brief``.

        Only ``accept``-verdict rows qualify — seeds ship accepted and edited golds
        are accepted, so this is the curated, known-good slice. Zero-overlap rows
        are dropped rather than padding with noise.
        """
        rows = [r for r in self.load_trainset() if r.get("verdict") == "accept"]
        if not rows:
            return []
        want = _tokens(brief)
        scored = [
            (len(want & _tokens(str(r.get("brief", "")))), idx, r) for idx, r in enumerate(rows)
        ]
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        return [r for overlap, _idx, r in scored[:limit] if overlap > 0]

    # --- trainset + drift (idea 2) ------------------------------------------- #
    def append_trainset(self, record: dict[str, Any]) -> dict[str, Any]:
        """Append one ``(spec → artifact)`` pair, stamping ``id``/``ts``/``source``/
        ``verdict`` if absent. Returns the stored record (with its assigned id)."""
        record.setdefault("id", uuid7().hex)
        record.setdefault("ts", _now())
        record.setdefault("source", SOURCE_GENERATED)
        record.setdefault("verdict", None)  # None = unreviewed; "accept" / "reject"
        _append_jsonl(self.home() / TRAINSET_FILE, record)
        return record

    def load_trainset(self) -> list[dict[str, Any]]:
        return _read_jsonl(self.home() / TRAINSET_FILE)

    def _rewrite_trainset(self, rows: list[dict[str, Any]]) -> None:
        # Temp file + atomic replace, so a crash or concurrent reader never sees a
        # truncated ledger. (Cross-process lost-update is still possible but
        # acceptable for a single-user curation tool.)
        path = self.home() / TRAINSET_FILE
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        tmp.replace(path)

    @staticmethod
    def _match(rows: list[dict[str, Any]], ref: str) -> dict[str, Any] | None:
        """Row whose id equals ``ref`` or uniquely starts with it (git-style)."""
        exact = [r for r in rows if r.get("id") == ref]
        if exact:
            return exact[0]
        pref = [r for r in rows if str(r.get("id", "")).startswith(ref)]
        return pref[0] if len(pref) == 1 else None

    def find_trainset(self, ref: str) -> dict[str, Any] | None:
        """Resolve a row by exact id or unique id-prefix (git-style)."""
        return self._match(self.load_trainset(), ref)

    def update_trainset(self, ref: str, **updates: Any) -> dict[str, Any] | None:
        """Patch the row matched by ``ref`` (id or unique prefix); rewrite the file."""
        rows = self.load_trainset()
        target = self._match(rows, ref)
        if target is None:
            return None
        target.update(updates)
        target["ts"] = _now()
        self._rewrite_trainset(rows)
        return target

    def delete_trainset(self, ref: str) -> bool:
        rows = self.load_trainset()
        target = self._match(rows, ref)
        if target is None:
            return False
        self._rewrite_trainset([r for r in rows if r.get("id") != target.get("id")])
        return True

    def seed_trainset(self, rows: list[dict[str, Any]]) -> int:
        """Idempotently append seed pairs (skip ids already present). Returns # added."""
        have = {r.get("id") for r in self.load_trainset()}
        added = 0
        for row in rows:
            if row.get("id") in have:
                continue
            self.append_trainset({**row, "source": SOURCE_SEED})
            added += 1
        return added

    def trainset_stats(self) -> dict[str, int]:
        rows = self.load_trainset()
        return {
            "total": len(rows),
            "accepted": sum(1 for r in rows if r.get("verdict") == "accept"),
            "rejected": sum(1 for r in rows if r.get("verdict") == "reject"),
            "unreviewed": sum(1 for r in rows if r.get("verdict") not in ("accept", "reject")),
            "seed": sum(1 for r in rows if r.get("source") == SOURCE_SEED),
            "generated": sum(1 for r in rows if r.get("source") == SOURCE_GENERATED),
            "edited": sum(1 for r in rows if r.get("source") == SOURCE_EDITED),
        }

    def drift_rate(self, window: int = 20) -> float:
        """Rolling first-try pass rate over the last ``window`` GENERATED builds.

        First-try = gate passed with ``attempts == 1``. Seeds + edited golds are
        excluded so the signal reflects the live generator. ``1.0`` with no history.
        """
        rows = [
            r for r in self.load_trainset() if r.get("source", SOURCE_GENERATED) == SOURCE_GENERATED
        ]
        if not rows:
            return 1.0
        recent = rows[-window:]
        first_try = sum(1 for r in recent if int(r.get("attempts", 1)) == 1)
        return first_try / len(recent)

    def load_compiled_demos(self) -> list[dict[str, Any]] | None:
        """Few-shot demos written by the optimizer, if any (idea 2 → idea 1)."""
        path = self.home() / COMPILED_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        demos = data.get("demos")
        return cast("list[dict[str, Any]]", demos) if isinstance(demos, list) else None
