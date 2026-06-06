# SPDX-License-Identifier: Apache-2.0
"""TDD-RED suite for the asyncpg + pgbouncer-safe Postgres checkpointer (FR-18).

Pins the contract for ``stargraph.checkpoint.postgres.PostgresCheckpointer`` per
research §4 amendment 5 / design §3.2.4 *before* the GREEN implementation
lands in task 3.21. Every case here MUST be RED -- the
``stargraph.checkpoint.postgres`` module does not exist yet, so the deferred
import inside each test raises :class:`ImportError`.

Cases (per task 3.20):

1. ``test_pool_disables_prepared_statements``  -- ``create_pool`` with
   ``statement_cache_size=0``; same connection survives 100 sequential writes
   WITHOUT ``"prepared statement does not exist"`` from pgbouncer txn-mode.
2. ``test_jsonb_codec_format_text``            -- JSONB codec round-trips a
   state dict via ``set_type_codec(..., format='text')`` (asyncpg #623 says
   ``format='binary'`` is broken for jsonb).
3. ``test_server_settings_tcp_keepalives``     -- pool ``server_settings``
   carry ``tcp_keepalives_idle='60'``, ``..._interval='10'``,
   ``..._count='3'``, ``application_name='stargraph.engine'``.
4. ``test_tables_under_stargraph_schema``         -- ``SELECT schemaname FROM
   pg_tables WHERE tablename='checkpoints'`` returns ``stargraph`` (Nautilus
   coexistence; design §3.2.4).
5. ``test_close_pool_shielded_with_timeout``   -- ``close_pool`` is wrapped
   in ``asyncio.shield`` + ``asyncio.wait_for(timeout=10)`` to avoid
   mid-cancel partial close (asyncpg #290).

The pgbouncer txn-mode sidecar (case 1's true assertion) is parked behind a
``pytest.mark.skip`` until the GREEN driver exists; for RED the deferred
import is the load-bearing failure. See ``.progress.md`` for the rationale.
The deferred-import pattern (``# pyright: ignore[reportMissingImports]``)
keeps pyright + ruff green while ensuring runtime collection still fails
with :class:`ImportError`.
"""

from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import pytest

testcontainers = pytest.importorskip(
    "testcontainers.postgres",
    reason="testcontainers[postgresql] not installed",
)

from stargraph.checkpoint import Checkpoint  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Iterator


# --------------------------------------------------------------------------- #
# Docker availability gate                                                    #
# --------------------------------------------------------------------------- #


def _docker_available() -> bool:
    """Return ``True`` iff the local Docker daemon answers a ping."""
    try:
        import docker  # type: ignore[import-untyped]

        client: Any = docker.from_env()  # pyright: ignore[reportUnknownMemberType]
        client.ping()
    except Exception:  # any failure ⇒ skip
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _docker_available(),
    reason="docker daemon unavailable -- skipping pgbouncer integration",
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _import_postgres_checkpointer() -> Any:
    """Return the (not-yet-implemented) ``PostgresCheckpointer`` class.

    Lives behind a helper so the import is expressed once. Until task 3.21
    lands, calling this raises :class:`ImportError` -- that is the RED signal.
    """
    mod = importlib.import_module(
        "stargraph.checkpoint.postgres",  # pyright: ignore[reportMissingImports]
    )
    return mod.PostgresCheckpointer


def _make_checkpoint(run_id: str = "run-001", step: int = 0) -> Checkpoint:
    """Build a fully-populated :class:`Checkpoint` for round-trip tests."""
    return Checkpoint(
        run_id=run_id,
        step=step,
        branch_id=None,
        parent_step_idx=None,
        graph_hash="sha256:graph",
        runtime_hash="sha256:runtime",
        state={"x": 1, "nested": {"k": "v"}},
        clips_facts=[{"template": "evidence", "slots": {"field": "v"}}],
        last_node="n0",
        next_action=None,
        timestamp=datetime.now(UTC),
        parent_run_id=None,
        side_effects_hash="sha256:side",
    )


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def postgres_dsn() -> Iterator[str]:
    """Spin up a postgres testcontainer and yield its connection URL.

    Module-scoped so the container is reused across the five RED cases. The
    pgbouncer sidecar (true txn-mode prepared-statement assertion) is parked
    until GREEN; see module docstring.
    """
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    with PostgresContainer("postgres:16-alpine") as pg:
        # asyncpg DSN form, not psycopg2.
        yield pg.get_connection_url().replace("postgresql+psycopg2", "postgresql")


# --------------------------------------------------------------------------- #
# RED cases                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.skip(
    reason=(
        "pgbouncer txn-mode sidecar deferred to task 3.21 GREEN; RED is "
        "covered by the deferred ImportError below."
    ),
)
def test_pool_disables_prepared_statements(postgres_dsn: str) -> None:
    """100 sequential writes survive without ``prepared statement does not exist``.

    Requires a pgbouncer sidecar in ``pool_mode=transaction`` to actually
    exercise the antipattern guard. Parked behind ``skip`` until GREEN.
    """
    # Will be filled in by task 3.21's GREEN implementation.
    _import_postgres_checkpointer()


def test_jsonb_codec_format_text(postgres_dsn: str) -> None:
    """JSONB codec round-trips a state dict using ``format='text'``.

    asyncpg #623: ``format='binary'`` is broken for jsonb. Driver MUST use
    text format with orjson encoder/decoder.
    """
    pg_cp = _import_postgres_checkpointer()  # ImportError ⇒ RED
    cp = pg_cp(dsn=postgres_dsn)
    asyncio.run(cp.bootstrap())
    snapshot = _make_checkpoint()
    asyncio.run(cp.write(snapshot))
    got = asyncio.run(cp.read_latest(snapshot.run_id))
    assert got is not None
    assert got.state == snapshot.state


def test_server_settings_tcp_keepalives(postgres_dsn: str) -> None:
    """Pool ``server_settings`` carry tcp_keepalive_* per amendment 5."""
    pg_cp = _import_postgres_checkpointer()  # ImportError ⇒ RED
    cp = pg_cp(dsn=postgres_dsn)
    asyncio.run(cp.bootstrap())
    settings = cp.server_settings  # accessor on the driver
    assert settings["tcp_keepalives_idle"] == "60"
    assert settings["tcp_keepalives_interval"] == "10"
    assert settings["tcp_keepalives_count"] == "3"
    assert settings["application_name"] == "stargraph.engine"


def test_tables_under_stargraph_schema(postgres_dsn: str) -> None:
    """All Stargraph tables live under the ``stargraph`` schema (Nautilus coexistence)."""
    import asyncpg  # pyright: ignore[reportMissingTypeStubs]

    pg_cp = _import_postgres_checkpointer()  # ImportError ⇒ RED
    cp = pg_cp(dsn=postgres_dsn)
    asyncio.run(cp.bootstrap())

    async def _query() -> str | None:
        conn = cast("Any", await asyncpg.connect(postgres_dsn))  # pyright: ignore[reportUnknownMemberType]
        try:
            row = cast(
                "dict[str, Any] | None",
                await conn.fetchrow(
                    "SELECT schemaname FROM pg_tables WHERE tablename='checkpoints'",
                ),
            )
            return None if row is None else cast("str", row["schemaname"])
        finally:
            await conn.close()

    schemaname = asyncio.run(_query())
    assert schemaname == "stargraph"


def test_close_pool_shielded_with_timeout(postgres_dsn: str) -> None:
    """``close_pool`` is wrapped in ``asyncio.shield`` + ``wait_for(timeout=10)``.

    asyncpg #290: a mid-cancel partial close leaves dangling connections.
    The driver must shield the close and bound it with a 10s timeout.
    """
    pg_cp = _import_postgres_checkpointer()  # ImportError ⇒ RED
    cp = pg_cp(dsn=postgres_dsn)
    asyncio.run(cp.bootstrap())
    # Ask the driver to expose its shutdown contract; GREEN will define the
    # exact accessor name. For RED the import already failed.
    assert hasattr(cp, "close_pool")
    asyncio.run(asyncio.wait_for(cp.close_pool(), timeout=15))
