# SPDX-License-Identifier: Apache-2.0
"""``std.shell`` -- run a shell command. Capability-gated, default-deny.

The full-trust escape hatch: the command runs under ``/bin/sh -c`` with
the parent's environment and (optionally) a caller-chosen working
directory -- no jail, no env stripping. That is exactly why it requires
the ``tools:std:shell`` capability: a run that has not been explicitly
granted the claim cannot invoke it at all (default-deny, NFR-7). Graphs
that only need Python execution should grant ``tools:std:exec`` /
``std.python_exec`` instead, which does strip the environment.
"""

from __future__ import annotations

import asyncio
from typing import Any

import anyio

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["shell"]

_MAX_OUTPUT_CHARS = 65_536
_MAX_TIMEOUT_S = 300.0


def _truncate(raw: bytes) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > _MAX_OUTPUT_CHARS:
        return text[:_MAX_OUTPUT_CHARS], True
    return text, False


@tool(
    name="shell",
    namespace="std",
    version="1",
    side_effects=SideEffects.external,
    requires_capability="tools:std:shell",
    description=(
        "Run a shell command (/bin/sh -c) with the parent environment and a "
        "wall-clock timeout. Requires the tools:std:shell capability; blocked "
        "by default."
    ),
)
async def shell(command: str, timeout_s: float = 60.0, cwd: str | None = None) -> dict[str, Any]:
    """Run ``command``; return ``{stdout, stderr, exit_code, timed_out, truncated}``."""
    timeout_s = max(1.0, min(timeout_s, _MAX_TIMEOUT_S))
    if cwd is not None and not await anyio.Path(cwd).is_dir():
        raise StargraphRuntimeError(f"cwd {cwd!r} is not a directory", cwd=cwd)
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    timed_out = False
    try:
        out_raw, err_raw = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        timed_out = True
        proc.kill()
        out_raw, err_raw = await proc.communicate()
    stdout, out_trunc = _truncate(out_raw)
    stderr, err_trunc = _truncate(err_raw)
    return {
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": -1 if timed_out else int(proc.returncode or 0),
        "timed_out": timed_out,
        "truncated": out_trunc or err_trunc,
    }
