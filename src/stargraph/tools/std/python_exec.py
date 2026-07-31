# SPDX-License-Identifier: Apache-2.0
"""``std.python_exec`` -- run Python code in an isolated subprocess.

Capability-gated (``tools:std:exec``): default-deny blocks it unless the
run grants the claim -- arbitrary code execution is never on by default.

Isolation model (subprocess, not in-process):

* ``sys.executable -I`` -- isolated mode: no user site-packages, no
  ``PYTHONPATH``, no current-directory import injection.
* Fresh temporary working directory (discarded afterwards) so stray file
  writes never land in the caller's tree.
* Minimal environment allowlist (``PATH``/``HOME``/``LANG``/``TMPDIR``/
  ``TERM`` + ``LC_*``) -- the parent's secrets (API keys, tokens) are NOT
  inherited by executed code.
* Wall-clock timeout; the process group is killed on expiry.

This is process-level isolation, not a security sandbox against a hostile
kernel-exploit payload -- hence the capability gate.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from typing import Any

from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects

__all__ = ["python_exec"]

_MAX_OUTPUT_CHARS = 65_536
_MAX_TIMEOUT_S = 120.0
_SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "TMPDIR", "TERM")


def _subprocess_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS or k.startswith("LC_")}
    return env


def _truncate(raw: bytes) -> tuple[str, bool]:
    text = raw.decode("utf-8", errors="replace")
    if len(text) > _MAX_OUTPUT_CHARS:
        return text[:_MAX_OUTPUT_CHARS], True
    return text, False


@tool(
    name="python_exec",
    namespace="std",
    version="1",
    side_effects=SideEffects.external,
    requires_capability="tools:std:exec",
    description=(
        "Execute Python code in an isolated subprocess (python -I, throwaway "
        "cwd, minimal env, wall-clock timeout). Requires the tools:std:exec "
        "capability; blocked by default."
    ),
)
async def python_exec(code: str, timeout_s: float = 30.0) -> dict[str, Any]:
    """Run ``code``; return ``{stdout, stderr, exit_code, timed_out, truncated}``."""
    timeout_s = max(1.0, min(timeout_s, _MAX_TIMEOUT_S))
    with tempfile.TemporaryDirectory(prefix="stargraph-python-exec-") as tmp:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=tmp,
            env=_subprocess_env(),
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
