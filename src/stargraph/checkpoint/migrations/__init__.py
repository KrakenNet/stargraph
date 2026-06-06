# SPDX-License-Identifier: Apache-2.0
"""Hand-rolled checkpoint migrations (design §3.2.5, Open Q12).

Alembic adds 5+ transitive deps for one Phase-1 schema; we ship a ~80 LOC
runner instead. Each migration is a :class:`Migration` record with a
monotonic ``version``, a one-line ``description``, and an idempotent
``up`` coroutine that takes the open driver connection.

The runner is in :mod:`stargraph.checkpoint.sqlite` (driver-specific because
it has to read/write ``stargraph_schema_version`` via the driver's connection
type); this module just exposes the migration list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from stargraph.checkpoint.migrations import _m001_initial, _m002_run_history

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import aiosqlite

__all__ = ["MIGRATIONS", "Migration"]


@dataclass(frozen=True)
class Migration:
    """One forward-only schema migration (design §3.2.5)."""

    version: int
    description: str
    up: Callable[[aiosqlite.Connection], Awaitable[None]]


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="create checkpoints, runs, stargraph_schema_version",
        up=_m001_initial.up,
    ),
    Migration(
        version=2,
        description="create runs_history + pending_runs (serve layer)",
        up=_m002_run_history.up,
    ),
]
