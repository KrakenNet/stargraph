# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.postgres`` -- PostgreSQL tools (namespace ``postgres``).

| tool | capability | side effects |
|---|---|---|
| ``postgres.query@1`` | ``tools:postgres:read`` | read (server-enforced read-only) |
| ``postgres.execute@1`` | ``tools:postgres:write`` | write (dry-run by default) |

``psycopg`` (the ``stargraph[tools-saas]`` extra) is imported lazily
inside :mod:`._conn`, so the tools always register and a missing
dependency fails loudly with a pip hint at call time. The DSN comes
from ``STARGRAPH_POSTGRES_DSN`` (never a tool argument -- credentials
must not flow through graph state). The blocking driver runs in a
worker thread.

``query`` opens its session with ``default_transaction_read_only=on``,
so a smuggled INSERT/UPDATE fails server-side -- the read capability is
enforced by PostgreSQL, not by string inspection. ``execute`` follows
the SaaS safety boundaries (:mod:`stargraph.tools._saas`): dry-run
unless ``STARGRAPH_POSTGRES_LIVE`` is truthy, and the required
``dedupe_key`` is stamped into ``application_name`` so the statement is
attributable in ``pg_stat_activity`` / server logs (PostgreSQL has no
server-side dedupe; retries are the caller's contract).
"""

from __future__ import annotations

from stargraph.tools.postgres.execute import pg_execute
from stargraph.tools.postgres.query import pg_query

__all__ = ["pg_execute", "pg_query"]
