# SPDX-License-Identifier: Apache-2.0
"""stargraph.checkpoint -- Checkpointer Protocol + Pydantic records (FR-16).

Phase 1 ships the storage-driver contract at
:mod:`stargraph.checkpoint.protocol` -- :class:`Checkpoint` and
:class:`RunSummary` Pydantic records plus the :class:`Checkpointer`
``Protocol``. Concrete drivers (aiosqlite-WAL, asyncpg-pgbouncer-safe)
land in subsequent tasks (1.20, 3.20).
"""

from __future__ import annotations

from stargraph.checkpoint.protocol import Checkpoint, Checkpointer, RunSummary

__all__ = [
    "Checkpoint",
    "Checkpointer",
    "RunSummary",
]
