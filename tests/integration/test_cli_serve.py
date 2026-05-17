# SPDX-License-Identifier: Apache-2.0
"""Integration test for ``cli/serve.py`` uvicorn programmatic boot (T15).

Pins that ``cli/serve.cmd`` uses the programmatic ``uvicorn.Config(...)`` +
``uvicorn.Server(cfg).run()`` pattern rather than the synchronous
``uvicorn.run(...)`` call. Phase-3 per-message-deflate and profile-driven
knobs land on top of this.

Hand-rolled ``uvicorn.Server`` stand-in (no ``unittest.mock`` per
anti-cheat rules).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.integration
def test_cli_serve_uses_uvicorn_server_class(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """``harbor serve`` boots via ``uvicorn.Config(...) + uvicorn.Server(cfg).run()``.

    Hand-rolled stand-in classes (no ``unittest.mock``) capture the boot
    call shape and short-circuit the run loop.
    """
    import uvicorn
    from typer.testing import CliRunner

    from harbor.cli import app

    seen: dict[str, object] = {}

    class _StandinConfig:
        def __init__(self, app: object, **kwargs: object) -> None:
            seen["config_app"] = app
            seen["config_kwargs"] = kwargs

    class _StandinServer:
        def __init__(self, cfg: object) -> None:
            seen["server_cfg"] = cfg

        def run(self) -> None:
            seen["server_run"] = True

    monkeypatch.setattr(uvicorn, "Config", _StandinConfig)
    monkeypatch.setattr(uvicorn, "Server", _StandinServer)

    result = CliRunner().invoke(app, ["serve", "--host", "127.0.0.1", "--port", "0"])

    assert result.exit_code == 0, result.output
    assert seen.get("server_run") is True
    assert "config_app" in seen
    assert isinstance(seen["server_cfg"], _StandinConfig)
