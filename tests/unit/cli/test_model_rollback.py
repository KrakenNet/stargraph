# SPDX-License-Identifier: Apache-2.0
"""Unit: ``stargraph model rollback <name>`` (W2 ops surface).

Against a real temp :class:`ModelRegistry` SQLite DB (no stubs):

* rollback repoints the alias to the previously-registered version and
  appends a ``model_rollback`` audit event to the default
  ``<registry>.audit.jsonl``.
* refusal when the alias already points at the earliest version (exit
  non-zero, alias untouched).
* refusal when the alias / registry does not exist.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import TYPE_CHECKING, Any, cast

import pytest
from typer.testing import CliRunner

from stargraph.audit.jsonl import unwrap_audit_record
from stargraph.cli import app
from stargraph.ml.registry import ModelRegistry

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def _make_registry(db_path: Path, versions: list[str], *, alias_to: str) -> None:
    """Register ``versions`` in order and point ``production`` at ``alias_to``."""

    async def _build() -> None:
        reg = ModelRegistry(db_path)
        await reg.bootstrap()
        try:
            for v in versions:
                await reg.register(
                    model_id="clf",
                    version=v,
                    runtime="sklearn",
                    file_uri=f"file:///models/clf-{v}.skops",
                    content_hash="0" * 64,
                )
            await reg.alias(model_id="clf", alias="production", version=alias_to)
        finally:
            await reg.close()

    asyncio.run(_build())


def _alias_target(db_path: Path) -> str | None:
    db = sqlite3.connect(db_path)
    try:
        row = db.execute(
            "SELECT version FROM model_aliases WHERE model_id='clf' AND alias='production'"
        ).fetchone()
    finally:
        db.close()
    return None if row is None else str(row[0])


@pytest.mark.unit
def test_rollback_repoints_alias_and_audits(tmp_path: Path) -> None:
    db_path = tmp_path / "models.db"
    _make_registry(db_path, ["1.0.0", "2.0.0"], alias_to="2.0.0")

    result = runner.invoke(app, ["model", "rollback", "clf", "--registry", str(db_path)])

    assert result.exit_code == 0, result.output
    assert _alias_target(db_path) == "1.0.0"
    assert "2.0.0 -> 1.0.0" in result.output

    audit_path = tmp_path / "models.db.audit.jsonl"
    assert audit_path.exists(), "rollback must emit an audit event"
    # The chained sink writes an anchor/checkpoint line alongside the
    # record; unwrap every line and keep the audit-event payloads.
    events: list[dict[str, Any]] = []
    for ln in audit_path.read_text().splitlines():
        if not ln.strip():
            continue
        unwrapped: Any = unwrap_audit_record(json.loads(ln))
        if isinstance(unwrapped, dict) and "fact" in unwrapped:
            events.append(cast("dict[str, Any]", unwrapped))
    assert len(events) == 1
    payload = events[0]
    assert payload["fact"]["kind"] == "model_rollback"
    assert payload["fact"]["model_id"] == "clf"
    assert payload["fact"]["from_version"] == "2.0.0"
    assert payload["fact"]["to_version"] == "1.0.0"


@pytest.mark.unit
def test_rollback_refuses_without_previous_version(tmp_path: Path) -> None:
    db_path = tmp_path / "models.db"
    _make_registry(db_path, ["1.0.0"], alias_to="1.0.0")

    result = runner.invoke(app, ["model", "rollback", "clf", "--registry", str(db_path)])

    assert result.exit_code != 0
    assert "no previous version" in result.output
    assert _alias_target(db_path) == "1.0.0", "refusal must not touch the alias"
    assert not (tmp_path / "models.db.audit.jsonl").exists()


@pytest.mark.unit
def test_rollback_refuses_unknown_alias(tmp_path: Path) -> None:
    db_path = tmp_path / "models.db"

    async def _build() -> None:
        reg = ModelRegistry(db_path)
        await reg.bootstrap()
        try:
            await reg.register(
                model_id="clf",
                version="1.0.0",
                runtime="sklearn",
                file_uri="file:///models/clf-1.skops",
                content_hash="0" * 64,
            )
        finally:
            await reg.close()

    asyncio.run(_build())

    result = runner.invoke(app, ["model", "rollback", "clf", "--registry", str(db_path)])

    assert result.exit_code != 0
    assert "no alias" in result.output


@pytest.mark.unit
def test_rollback_refuses_missing_registry(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["model", "rollback", "clf", "--registry", str(tmp_path / "absent.db")],
    )

    assert result.exit_code != 0
    assert "registry not found" in result.output
