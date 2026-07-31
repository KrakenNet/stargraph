# SPDX-License-Identifier: Apache-2.0
"""``std.python_exec`` / ``std.shell`` -- capability gates + subprocess behavior.

Gate tests run through :func:`execute_tool` (the real pipeline) to prove
default-deny blocks both tools with no grant; body tests call the tools
directly (the pipeline's other steps are covered elsewhere).
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from stargraph.errors import CapabilityError, StargraphRuntimeError
from stargraph.runtime.tool_exec import RunContext, execute_tool
from stargraph.security.capabilities import Capabilities, CapabilityClaim
from stargraph.tools.std.python_exec import python_exec
from stargraph.tools.std.shell import shell

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Capability gates (through the real pipeline).
# ---------------------------------------------------------------------------


async def test_python_exec_denied_by_default() -> None:
    ctx = RunContext(run_id="r1")  # no capabilities wired
    with pytest.raises(CapabilityError):
        await execute_tool(cast("Any", python_exec), {"code": "print(1)"}, run_ctx=ctx)


async def test_shell_denied_by_default() -> None:
    ctx = RunContext(run_id="r1")
    with pytest.raises(CapabilityError):
        await execute_tool(cast("Any", shell), {"command": "true"}, run_ctx=ctx)


async def test_python_exec_runs_with_granted_claim() -> None:
    caps = Capabilities(granted={CapabilityClaim(name="tools", scope="std:exec")})  # pyright: ignore[reportUnhashable]
    ctx = RunContext(run_id="r1", capabilities=caps)
    result = await execute_tool(cast("Any", python_exec), {"code": "print('hi')"}, run_ctx=ctx)
    assert result.output["stdout"].strip() == "hi"
    assert result.output["exit_code"] == 0


async def test_shell_runs_with_granted_claim() -> None:
    caps = Capabilities(granted={CapabilityClaim(name="tools", scope="std:*")})  # pyright: ignore[reportUnhashable]
    ctx = RunContext(run_id="r1", capabilities=caps)
    result = await execute_tool(cast("Any", shell), {"command": "echo shell-ok"}, run_ctx=ctx)
    assert result.output["stdout"].strip() == "shell-ok"


# ---------------------------------------------------------------------------
# Subprocess behavior (direct calls).
# ---------------------------------------------------------------------------


async def test_python_exec_captures_stderr_and_exit_code() -> None:
    out = await python_exec(code="import sys; sys.stderr.write('boom'); sys.exit(3)")
    assert out["stderr"] == "boom"
    assert out["exit_code"] == 3
    assert out["timed_out"] is False


async def test_python_exec_times_out() -> None:
    out = await python_exec(code="import time; time.sleep(30)", timeout_s=1.0)
    assert out["timed_out"] is True
    assert out["exit_code"] == -1


async def test_python_exec_env_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parent secrets must not reach executed code."""
    monkeypatch.setenv("STARGRAPH_TEST_SECRET", "hunter2")
    out = await python_exec(code="import os; print(os.environ.get('STARGRAPH_TEST_SECRET'))")
    assert out["stdout"].strip() == "None"


async def test_shell_nonzero_exit_code() -> None:
    out = await shell(command="exit 9")
    assert out["exit_code"] == 9


async def test_shell_times_out() -> None:
    out = await shell(command="sleep 30", timeout_s=1.0)
    assert out["timed_out"] is True


async def test_shell_invalid_cwd_is_loud() -> None:
    with pytest.raises(StargraphRuntimeError, match="not a directory"):
        await shell(command="true", cwd="/definitely/not/a/dir")
