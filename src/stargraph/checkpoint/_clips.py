# SPDX-License-Identifier: Apache-2.0
"""CLIPS fact text-format helpers for the JSONB ``clips_facts`` column (FR-16).

Wraps clipspy's ``Environment.save_facts(path, mode=SaveMode.LOCAL_SAVE)`` /
``Environment.load_facts(path)`` text-format API (research §4 amendment 9)
behind a JSONB-friendly :class:`list[str]` payload. Each list entry is one
text-format line as written by ``save_facts`` (e.g.
``"(person (name alice) (age 30))"``).

Per ADR 0001 (boundary-only rule firing) and the documented clipspy limit,
only the asserted-fact set round-trips; agenda + rule-firing-history are
intentionally NOT serialized -- resume re-fires rules against the
re-asserted facts. The integration contract is pinned by
``tests/integration/test_clips_facts_roundtrip.py``.

The helper does its own ``tempfile`` management so callers never touch the
filesystem -- the dump path is created, written, read back, and unlinked
inside :func:`dump_facts`; the load path is created, written, consumed by
``Environment.load_facts``, and unlinked inside :func:`load_facts`. Both
helpers are sync because clipspy itself is sync.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from typing import Any

__all__ = ["dump_facts", "load_facts"]


def dump_facts(env: Any) -> list[str]:
    """Serialize ``env``'s asserted-fact set to a JSONB-friendly list.

    Calls ``env.save_facts(path, mode=SaveMode.LOCAL_SAVE)`` against a
    temp file, reads it back, and returns the non-empty lines as
    :class:`list[str]`. The list shape stores cleanly through orjson into
    the ``clips_facts`` JSONB column.
    """
    import clips  # pyright: ignore[reportMissingTypeStubs]  # local: optional dep

    fd, path = tempfile.mkstemp(suffix=".clp", prefix="stargraph-clips-")
    os.close(fd)
    try:
        env.save_facts(path, mode=clips.SaveMode.LOCAL_SAVE)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
    return [line for line in text.splitlines() if line.strip()]


def load_facts(env: Any, payload: list[str]) -> None:
    """Re-assert ``payload`` lines into ``env`` via ``Environment.load_facts``.

    Writes the payload to a temp file in the clipspy text format and
    invokes ``env.load_facts(path)``. Per ADR 0001 the agenda is NOT
    repopulated from a serialized firing history -- callers must invoke
    ``env.run()`` to re-fire rules against the re-asserted facts.
    """
    fd, path = tempfile.mkstemp(suffix=".clp", prefix="stargraph-clips-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for line in payload:
                fh.write(line)
                fh.write("\n")
        env.load_facts(path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path)
