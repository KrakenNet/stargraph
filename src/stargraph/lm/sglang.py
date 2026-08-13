# SPDX-License-Identifier: Apache-2.0
"""Attach to -- or boot and tear down -- an SGLang server for one graph run.

``stargraph run`` resolves an :class:`~stargraph.ir.SGLangServer` spec (from
the graph's ``lm:`` block or the ``--sglang-*`` flags) into a live
OpenAI-compatible base URL via :func:`sglang_server`:

1. **Probe.** ``GET {base_url}/models``. If something answers, the endpoint is
   *not ours*: attach when it already serves the requested model, and leave it
   running when the run ends. If it answers with a different model, fail loud
   (:class:`~stargraph.errors.LMServerError`) -- silently running a graph
   against the wrong weights is worse than not running it.
2. **Spawn.** Nothing answering means we own it: launch
   ``python -m sglang.launch_server --model-path ... --host ... --port ...``
   plus the spec's passthrough ``args``, in its own process group, with stdout
   and stderr captured to a log file.
3. **Wait.** Poll the endpoint until it serves the model, the subprocess dies
   (error carries the exit code + a tail of the log), or
   ``startup_timeout_s`` elapses. Weight loading takes minutes for big models.
4. **Teardown.** ``SIGTERM`` the whole process group, then ``SIGKILL`` after a
   grace period -- SGLang forks scheduler/detokenizer children that a bare
   ``proc.terminate()`` would orphan on the GPU.

SGLang itself is never imported here (it is a heavy, GPU-only dependency); it
is invoked as a subprocess of the *current* interpreter, so a missing install
surfaces as a launch failure with the pip hint attached.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import httpx

from stargraph.errors import LMServerError

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from stargraph.ir import SGLangServer

__all__ = ["base_url", "served_models", "sglang_server"]

_PROBE_TIMEOUT_S = 2.0
_POLL_INTERVAL_S = 2.0
_TERM_GRACE_S = 30.0
_LOG_TAIL_LINES = 20


def base_url(spec: SGLangServer) -> str:
    """OpenAI-compatible base URL implied by ``spec`` (no trailing slash)."""
    return f"http://{spec.host}:{spec.port}/v1"


def served_models(url: str, *, timeout: float = _PROBE_TIMEOUT_S) -> list[str] | None:
    """Model ids served at ``url``, or ``None`` when nothing answers there.

    ``None`` (connection refused / timeout / non-2xx) means "no server":
    the caller may spawn one. An empty list means a server answered but
    serves nothing -- a real, distinct condition the caller must not
    confuse with an unused port.
    """
    try:
        resp = httpx.get(f"{url}/models", timeout=timeout)
        resp.raise_for_status()
        payload = cast("dict[str, Any]", resp.json())
    except (httpx.HTTPError, ValueError):
        return None
    data = cast("list[dict[str, Any]]", payload.get("data", []))
    return [str(entry.get("id", "")) for entry in data]


def _launch_argv(spec: SGLangServer) -> list[str]:
    """Argv for the launch subprocess (monkeypatched in tests)."""
    return [
        sys.executable,
        "-m",
        "sglang.launch_server",
        "--model-path",
        spec.model,
        "--host",
        spec.host,
        "--port",
        str(spec.port),
        *spec.args,
    ]


def _log_tail(log_path: Path) -> str:
    """Last :data:`_LOG_TAIL_LINES` lines of the captured server output."""
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-_LOG_TAIL_LINES:])


def _spawn(spec: SGLangServer, log_path: Path) -> subprocess.Popen[bytes]:
    argv = _launch_argv(spec)
    handle = log_path.open("wb")
    try:
        return subprocess.Popen(
            argv,
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        raise LMServerError(
            f"failed to launch sglang: {exc}",
            hint=f"is sglang installed for {sys.executable}? `pip install sglang`",
            argv=" ".join(argv),
        ) from exc
    finally:
        # The child holds its own dup of the fd; ours is dead weight either way.
        with contextlib.suppress(OSError):
            handle.close()


def _await_ready(
    proc: subprocess.Popen[bytes],
    spec: SGLangServer,
    url: str,
    log_path: Path,
    echo: Callable[[str], None] | None,
) -> None:
    """Block until ``url`` serves ``spec.model``; raise on death or timeout."""
    deadline = time.monotonic() + spec.startup_timeout_s
    while True:
        code = proc.poll()
        if code is not None:
            raise LMServerError(
                f"sglang exited with code {code} before serving {spec.model!r}",
                hint=f"server output: {log_path}",
                base_url=url,
                exit_code=code,
                log=str(log_path),
                tail=_log_tail(log_path),
            )
        models = served_models(url)
        if models is not None:
            if spec.model not in models:
                raise LMServerError(
                    f"sglang on {url} serves {models} but the graph asked for {spec.model!r}",
                    hint="pass --served-model-name via args, or fix the model id",
                    base_url=url,
                )
            if echo is not None:
                echo(f"sglang ready on {url} ({spec.model})")
            return
        if time.monotonic() >= deadline:
            raise LMServerError(
                f"sglang did not answer on {url} within {spec.startup_timeout_s}s",
                hint=(f"raise startup_timeout_s for a big model; server output: {log_path}"),
                base_url=url,
                log=str(log_path),
                tail=_log_tail(log_path),
            )
        time.sleep(_POLL_INTERVAL_S)


def _terminate(proc: subprocess.Popen[bytes]) -> None:
    """SIGTERM the process group, SIGKILL what survives the grace period."""
    if proc.poll() is not None:
        return
    _signal_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=_TERM_GRACE_S)
    except subprocess.TimeoutExpired:
        _signal_group(proc, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_TERM_GRACE_S)


def _signal_group(proc: subprocess.Popen[bytes], sig: int) -> None:
    """Signal the child's process group, falling back to the child alone."""
    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        os.killpg(os.getpgid(proc.pid), sig)
        return
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.send_signal(sig)


@contextlib.contextmanager
def sglang_server(
    spec: SGLangServer,
    *,
    log_path: Path | None = None,
    echo: Callable[[str], None] | None = None,
) -> Generator[str]:
    """Yield the base URL of a server for ``spec``, booting one if needed.

    Attaches to an already-running server that serves ``spec.model`` and
    leaves it running on exit; otherwise spawns one and terminates it (and
    its process group) when the block ends. ``log_path`` receives the
    spawned server's stdout+stderr (default: a temp file); ``echo`` gets
    one-line progress messages.
    """
    url = base_url(spec)
    existing = served_models(url)
    if existing is not None:
        if spec.model not in existing:
            raise LMServerError(
                f"a server already listening on {url} serves {existing}, not {spec.model!r}",
                hint="pick a free --sglang-port, or stop that server",
                base_url=url,
            )
        if echo is not None:
            echo(f"attached to running sglang on {url} ({spec.model})")
        yield url
        return

    if log_path is None:
        fd, name = tempfile.mkstemp(prefix=f"sglang-{spec.port}-", suffix=".log")
        os.close(fd)
        log_path = Path(name)
    if echo is not None:
        echo(f"starting sglang ({spec.model}) on {url}; output -> {log_path}")
    proc = _spawn(spec, log_path)
    try:
        _await_ready(proc, spec, url, log_path, echo)
        yield url
    finally:
        _terminate(proc)
