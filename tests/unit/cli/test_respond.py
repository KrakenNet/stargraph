# SPDX-License-Identifier: Apache-2.0
"""Unit: ``stargraph respond <run_id>`` HITL response CLI (task 4.9).

Per design §3.1 (``respond.py`` row), the CLI is a thin wrapper over
``POST /v1/runs/{run_id}/respond`` (httpx) for the production path.
The shape:

    stargraph respond <run_id> --response @file.json --actor <name>
                   [--server http://localhost:8000]

Task 4.9 RED tests (TDD):

1. ``test_respond_help_mentions_actor`` -- the CLI help text mentions
   the ``--actor`` flag (verify command for the spec).
2. ``test_respond_posts_to_server`` -- monkeypatch ``httpx.Client.post``
   to capture the URL + JSON body + actor; assert the CLI POSTed the
   correct payload.
3. ``test_respond_surfaces_404_message`` -- when the server returns
   404, the CLI exits non-zero with a clear "run not found" message.
4. ``test_respond_surfaces_409_message`` -- when the server returns
   409, the CLI exits non-zero with a clear "already responded" /
   "not awaiting input" message.
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 -- runtime use by pytest fixture type
from typing import cast

import httpx
import pytest
from typer.testing import CliRunner

from stargraph.cli import app

_runner = CliRunner()


@pytest.fixture
def response_file(tmp_path: Path) -> Path:
    """Write a minimal response.json the CLI will load + POST."""
    p = tmp_path / "response.json"
    p.write_text(json.dumps({"decision": "approve", "note": "looks good"}), encoding="utf-8")
    return p


@pytest.mark.unit
def test_respond_help_mentions_actor() -> None:
    """``stargraph respond --help`` mentions the actor flag (verify cmd)."""
    result = _runner.invoke(app, ["respond", "--help"])
    assert result.exit_code == 0, result.output
    assert "actor" in result.output.lower()


@pytest.mark.unit
def test_respond_posts_to_server(
    response_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stargraph respond ...`` POSTs ``{"response": ...}`` to /v1/runs/{id}/respond."""
    captured: dict[str, object] = {}

    class _FakeResponse:
        status_code = 200
        text = '{"run_id": "r1", "status": "running"}'

        def json(self) -> dict[str, object]:
            return {"run_id": "r1", "status": "running"}

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def post(
            self,
            url: str,
            *,
            json: dict[str, object] | None = None,
            headers: dict[str, str] | None = None,
            **kwargs: object,
        ) -> _FakeResponse:
            del kwargs
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers or {}
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    result = _runner.invoke(
        app,
        [
            "respond",
            "r1",
            "--response",
            str(response_file),
            "--actor",
            "alice",
            "--server",
            "http://localhost:8000",
        ],
    )
    assert result.exit_code == 0, result.output + str(result.exception)
    assert captured["url"] == "http://localhost:8000/v1/runs/r1/respond"
    assert captured["json"] == {"response": {"decision": "approve", "note": "looks good"}}
    headers = cast("dict[str, str]", captured["headers"])
    assert isinstance(headers, dict)
    # The CLI sends the actor identity via the Authorization header so
    # the BypassAuthProvider (POC) and BearerJwtProvider (Phase 2) can
    # both extract it. The exact format is "Bypass <actor>" by spec.
    auth = headers.get("Authorization", "")
    assert "alice" in auth


@pytest.mark.unit
def test_respond_surfaces_404_message(
    response_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """404 from server -> non-zero exit + 'not found' in stderr."""

    class _FakeResponse:
        status_code = 404
        text = '{"detail": "run not found"}'

        def json(self) -> dict[str, object]:
            return {"detail": "run not found"}

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            del args, kwargs
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    result = _runner.invoke(
        app,
        [
            "respond",
            "missing-run",
            "--response",
            str(response_file),
            "--actor",
            "alice",
            "--server",
            "http://localhost:8000",
        ],
    )
    assert result.exit_code != 0, result.output
    combined = result.output + str(result.exception)
    assert "not found" in combined.lower()


@pytest.mark.unit
def test_respond_surfaces_409_message(
    response_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """409 from server -> non-zero exit + 'awaiting' / 'conflict' in stderr."""

    class _FakeResponse:
        status_code = 409
        text = '{"detail": "run not awaiting input"}'

        def json(self) -> dict[str, object]:
            return {"detail": "run not awaiting input"}

    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args, kwargs

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        def post(self, *args: object, **kwargs: object) -> _FakeResponse:
            del args, kwargs
            return _FakeResponse()

    monkeypatch.setattr(httpx, "Client", _FakeClient)
    result = _runner.invoke(
        app,
        [
            "respond",
            "r1",
            "--response",
            str(response_file),
            "--actor",
            "alice",
            "--server",
            "http://localhost:8000",
        ],
    )
    assert result.exit_code != 0, result.output
    combined = result.output + str(result.exception)
    assert "awaiting" in combined.lower() or "conflict" in combined.lower()
