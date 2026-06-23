# SPDX-License-Identifier: Apache-2.0
"""Pack smith ledger — the shared :class:`Ledger` bound to ``PACKSMITH_HOME``.

All behavior lives in :mod:`stargraph.skills._smith.ledger`; this module binds it to
the pack smith's home (default ``.stargraph/packsmith``) and exposes the methods as
module-level functions so callers import them from here.
"""

from __future__ import annotations

from stargraph.skills._smith.ledger import (
    COMPILED_FILE,
    LESSONS_FILE,
    SOURCE_EDITED,
    SOURCE_GENERATED,
    SOURCE_SEED,
    TRAINSET_FILE,
    Ledger,
)
from stargraph.skills._smith.ledger import (
    read_jsonl as _read_jsonl,
)

__all__ = [
    "COMPILED_FILE",
    "LESSONS_FILE",
    "SOURCE_EDITED",
    "SOURCE_GENERATED",
    "SOURCE_SEED",
    "TRAINSET_FILE",
    "_read_jsonl",
    "append_lesson",
    "append_trainset",
    "delete_trainset",
    "drift_rate",
    "find_trainset",
    "home",
    "load_compiled_demos",
    "load_trainset",
    "recall_examples",
    "recall_lessons",
    "seed_trainset",
    "trainset_stats",
    "update_trainset",
]

_LEDGER = Ledger(home_env="PACKSMITH_HOME", home_default=".stargraph/packsmith")

home = _LEDGER.home
append_lesson = _LEDGER.append_lesson
recall_lessons = _LEDGER.recall_lessons
recall_examples = _LEDGER.recall_examples
append_trainset = _LEDGER.append_trainset
load_trainset = _LEDGER.load_trainset
find_trainset = _LEDGER.find_trainset
update_trainset = _LEDGER.update_trainset
delete_trainset = _LEDGER.delete_trainset
seed_trainset = _LEDGER.seed_trainset
trainset_stats = _LEDGER.trainset_stats
drift_rate = _LEDGER.drift_rate
load_compiled_demos = _LEDGER.load_compiled_demos
