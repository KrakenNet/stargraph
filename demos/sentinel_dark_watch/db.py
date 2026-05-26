# SPDX-License-Identifier: Apache-2.0
"""Shared Postgres connection helpers for Sentinel Dark Watch."""

from __future__ import annotations

import os
from typing import Any

_pool: Any = None


def get_pg_dsn() -> str:
    """Return the Postgres DSN from ``POSTGRES_DSN`` env var or default."""
    return os.environ.get(
        "POSTGRES_DSN",
        "postgresql://harbor:harbor@localhost:5441/sdw",
    )


async def get_pg_pool() -> Any:
    """Return a cached asyncpg connection pool (min=2, max=10).

    Creates the pool on first call; subsequent calls return the same pool.
    """
    global _pool  # noqa: PLW0603
    if _pool is not None:
        return _pool

    import asyncpg

    _pool = await asyncpg.create_pool(get_pg_dsn(), min_size=2, max_size=10)
    return _pool
