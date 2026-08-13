# SPDX-License-Identifier: Apache-2.0
"""Real spawn -> ready -> teardown for :func:`stargraph.lm.sglang.sglang_server`.

SGLang is a GPU-only dependency, so the launch argv is swapped for a stub
OpenAI-compatible server (stdlib ``http.server``) that reports one model id.
Everything else is the production path: a real subprocess in its own process
group, real HTTP readiness polling, real SIGTERM teardown.
"""

from __future__ import annotations

import socket
import textwrap
from typing import TYPE_CHECKING

import pytest

from stargraph.errors import LMServerError
from stargraph.ir import SGLangServer
from stargraph.lm import sglang as sg

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

_STUB = textwrap.dedent(
    '''
    """Minimal OpenAI-compatible stub: GET /v1/models -> one model id."""
    import json
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    MODEL = sys.argv[1]
    PORT = int(sys.argv[2])


    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"data": [{"id": MODEL}]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):  # keep the captured log quiet
            return


    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    '''
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    path = tmp_path / "stub_server.py"
    path.write_text(_STUB, encoding="utf-8")
    return path


def test_spawns_waits_for_ready_then_tears_down(
    stub: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    port = _free_port()
    spec = SGLangServer(model="stub/model", port=port, startup_timeout_s=30)

    def _stub_argv(spec: SGLangServer) -> list[str]:
        return [sys.executable, str(stub), spec.model, str(spec.port)]

    monkeypatch.setattr(sg, "_launch_argv", _stub_argv)
    log = tmp_path / "sglang.log"

    with sg.sglang_server(spec, log_path=log) as url:
        assert url == f"http://127.0.0.1:{port}/v1"
        assert sg.served_models(url) == ["stub/model"]

    # Teardown is real: nothing answers on the port once the block exits.
    assert sg.served_models(f"http://127.0.0.1:{port}/v1", timeout=1.0) is None


def test_launch_that_dies_reports_exit_code_and_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    spec = SGLangServer(model="stub/model", port=_free_port(), startup_timeout_s=30)

    def _dying_argv(_spec: SGLangServer) -> list[str]:
        return [sys.executable, "-c", 'import sys; print("CUDA go boom"); sys.exit(3)']

    monkeypatch.setattr(sg, "_launch_argv", _dying_argv)
    log = tmp_path / "sglang.log"

    with (
        pytest.raises(LMServerError, match="exited with code 3") as excinfo,
        sg.sglang_server(spec, log_path=log),
    ):
        pytest.fail("must not yield when the launch dies")

    assert excinfo.value.context["exit_code"] == 3
    assert "CUDA go boom" in excinfo.value.context["tail"]


def test_startup_timeout_is_loud(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    spec = SGLangServer(model="stub/model", port=_free_port(), startup_timeout_s=1)

    def _hanging_argv(_spec: SGLangServer) -> list[str]:
        return [sys.executable, "-c", "import time; time.sleep(60)"]

    monkeypatch.setattr(sg, "_launch_argv", _hanging_argv)

    with (
        pytest.raises(LMServerError, match="did not answer"),
        sg.sglang_server(spec, log_path=tmp_path / "sglang.log"),
    ):
        pytest.fail("must not yield before the endpoint answers")
