# SPDX-License-Identifier: Apache-2.0
"""``stargraph model rollback <name>`` -- registry-alias rollback (W2 ops surface).

Repoints a :class:`stargraph.ml.registry.ModelRegistry` alias (default
``production``) to the version registered immediately **before** the
alias's current target, and emits a ``model_rollback``
:class:`~stargraph.runtime.events.BosunAuditEvent` to a JSONL audit log
(default: ``<registry>.audit.jsonl`` beside the registry DB, chained +
signed via the same sink builder ``stargraph run`` uses).

This is a pure metadata operation over the existing ``model_aliases``
table -- no model files are read or verified (a rollback must succeed
even when the *current* version's file is the thing that broke).
"Previous version" is ordered by the registry's ``created_at`` column
(``rowid`` tiebreak for identical timestamps). Refuses (exit 1) when:

* the registry DB does not exist,
* the alias is not registered for the model,
* the alias already points at the earliest registered version
  (nothing to roll back to).
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path  # noqa: TC003 -- runtime use by typer.Annotated
from typing import Annotated, Any

import typer

__all__ = ["model_app"]

model_app = typer.Typer(no_args_is_help=True, help="Model-registry operations.")


def _read_versions(db: sqlite3.Connection, model_id: str) -> list[str]:
    """Registered versions for ``model_id``, oldest first."""
    rows = db.execute(
        "SELECT version FROM models WHERE model_id = ? ORDER BY created_at, rowid",
        (model_id,),
    ).fetchall()
    return [str(r[0]) for r in rows]


def _read_alias(db: sqlite3.Connection, model_id: str, alias: str) -> str | None:
    row = db.execute(
        "SELECT version FROM model_aliases WHERE model_id = ? AND alias = ?",
        (model_id, alias),
    ).fetchone()
    return None if row is None else str(row[0])


async def _emit_rollback_audit(
    audit_log: Path,
    *,
    model_id: str,
    alias: str,
    from_version: str,
    to_version: str,
) -> None:
    """Append a ``model_rollback`` BosunAuditEvent via the shared sink builder."""
    # Local imports: reuse the chain-write/dual-read sink selection from
    # ``stargraph run`` (same precedent as cli.serve importing private
    # helpers from cli.run).
    from stargraph.cli.run import _build_audit_sink  # pyright: ignore[reportPrivateUsage]
    from stargraph.runtime.events import BosunAuditEvent

    now = datetime.now(UTC)
    provenance: dict[str, Any] = {
        "origin": "system",
        "source": "stargraph.cli.model",
        "run_id": "",
        "step": 0,
        "confidence": 1.0,
        "timestamp": now.isoformat(),
    }
    sink = _build_audit_sink(audit_log)
    try:
        await sink.write(
            BosunAuditEvent(
                run_id="",
                step=0,
                ts=now,
                pack_id="stargraph.cli",
                pack_version="1.0",
                fact={
                    "kind": "model_rollback",
                    "model_id": model_id,
                    "alias": alias,
                    "from_version": from_version,
                    "to_version": to_version,
                },
                provenance=provenance,
            )
        )
    finally:
        await sink.close()


@model_app.command("rollback")
def rollback(
    name: Annotated[
        str,
        typer.Argument(help="Model id whose alias should be rolled back."),
    ],
    registry: Annotated[
        Path,
        typer.Option(
            "--registry",
            help="Path to the ModelRegistry SQLite DB (see stargraph.ml.registry).",
        ),
    ],
    alias: Annotated[
        str,
        typer.Option("--alias", help="Alias to repoint."),
    ] = "production",
    audit_log: Annotated[
        Path | None,
        typer.Option(
            "--audit-log",
            help="JSONL audit log for the rollback event (default: <registry>.audit.jsonl).",
        ),
    ] = None,
) -> None:
    """Repoint a model alias to the previous registered version."""
    if not registry.exists():
        typer.echo(f"model rollback: registry not found: {registry}", err=True)
        raise typer.Exit(code=1)

    db = sqlite3.connect(registry)
    try:
        current = _read_alias(db, name, alias)
        if current is None:
            typer.echo(
                f"model rollback: no alias {alias!r} registered for model {name!r}",
                err=True,
            )
            raise typer.Exit(code=1)
        versions = _read_versions(db, name)
        try:
            idx = versions.index(current)
        except ValueError:
            typer.echo(
                f"model rollback: alias {alias!r} points at unregistered "
                f"version {current!r} for model {name!r}",
                err=True,
            )
            raise typer.Exit(code=1) from None
        if idx == 0:
            typer.echo(
                f"model rollback: refusing -- {current!r} is the earliest "
                f"registered version of {name!r}; no previous version to roll back to",
                err=True,
            )
            raise typer.Exit(code=1)
        previous = versions[idx - 1]
    finally:
        db.close()

    async def _repoint() -> None:
        # Reuse the existing alias machinery (FK pre-check + INSERT OR
        # REPLACE) rather than hand-rolling the write.
        from stargraph.ml.registry import ModelRegistry

        reg = ModelRegistry(registry)
        await reg.bootstrap()
        try:
            await reg.alias(model_id=name, alias=alias, version=previous)
        finally:
            await reg.close()
        log_path = (
            audit_log
            if audit_log is not None
            else registry.with_name(registry.name + ".audit.jsonl")
        )
        await _emit_rollback_audit(
            log_path,
            model_id=name,
            alias=alias,
            from_version=current,
            to_version=previous,
        )

    asyncio.run(_repoint())
    typer.echo(f"rolled back {name!r} alias {alias!r}: {current} -> {previous}")
