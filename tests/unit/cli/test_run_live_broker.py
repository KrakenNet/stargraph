# SPDX-License-Identifier: Apache-2.0
"""Tests for ``stargraph run --live-broker`` (S7a).

When the flag is set, ``cmd`` must wrap the run loop in
:func:`stargraph.serve.lifecycle.broker_lifespan` so the lifespan-singleton
:class:`nautilus.Broker` is wired around the run. Soft-fails (no
``nautilus.yaml``) still complete the run; the contextvar simply stays
``None`` and offline-mode broker callers fall back to envelopes.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from typer.testing import CliRunner

import stargraph.cli.run as run_mod
from stargraph.cli.run import cmd
from tests.fixtures.ansi import strip_ansi

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLE_GRAPH = REPO_ROOT / "tests" / "fixtures" / "sample-graph.yaml"


def _make_app() -> typer.Typer:
    app = typer.Typer()
    app.command()(cmd)
    return app


def test_live_broker_flag_invokes_broker_lifespan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--live-broker`` must enter ``broker_lifespan`` exactly once."""
    calls: list[str] = []

    @asynccontextmanager
    async def _fake_lifespan():
        calls.append("enter")
        try:
            yield
        finally:
            calls.append("exit")

    import stargraph.serve.lifecycle as lifecycle_mod

    monkeypatch.setattr(lifecycle_mod, "broker_lifespan", _fake_lifespan)

    runner = CliRunner()
    app = _make_app()
    result = runner.invoke(
        app,
        [
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--no-summary",
            "--live-broker",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == ["enter", "exit"], calls


def test_no_live_broker_skips_broker_lifespan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without ``--live-broker`` the broker lifespan must not be entered."""
    calls: list[str] = []

    @asynccontextmanager
    async def _fake_lifespan():
        calls.append("entered")
        try:
            yield
        finally:
            calls.append("exited")

    import stargraph.serve.lifecycle as lifecycle_mod

    monkeypatch.setattr(lifecycle_mod, "broker_lifespan", _fake_lifespan)

    runner = CliRunner()
    app = _make_app()
    result = runner.invoke(
        app,
        [
            str(SAMPLE_GRAPH),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--quiet",
            "--no-summary",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == [], calls


def test_live_broker_flag_help_listed() -> None:
    """``--live-broker`` must appear in --help output."""
    runner = CliRunner()
    app = _make_app()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "--live-broker" in strip_ansi(result.output)


def test_live_broker_imports_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    """``stargraph.serve.lifecycle`` must NOT be in ``sys.modules`` when --live-broker
    is unset (lazy import keeps cold ``stargraph run`` light).
    """
    import importlib
    import sys

    # Force eviction so the test reflects a cold import path.
    for mod_name in list(sys.modules):
        if mod_name.startswith("stargraph.serve.lifecycle"):
            del sys.modules[mod_name]

    # Sanity: cli.run import alone should not pull serve.lifecycle.
    importlib.reload(run_mod)
    assert "stargraph.serve.lifecycle" not in sys.modules, (
        "stargraph.cli.run is eagerly importing serve.lifecycle; should be lazy"
    )
