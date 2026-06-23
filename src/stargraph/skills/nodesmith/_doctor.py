# SPDX-License-Identifier: Apache-2.0
"""Preflight: prove nodesmith actually has the tools to build + run + verify nodes.

The headline check (``gate end-to-end``) writes a probe node + test to a temp
dir and runs the full gate — compile, ruff, a SUBPROCESS that imports and
executes the node, and pytest. If it passes, every capability nodesmith relies
on (generate files, run code, run tests, verify) is functional on this machine.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from stargraph.skills.nodesmith import _ledger
from stargraph.skills.nodesmith.gate import verify_sources

_PROBE_NODE = """\
from stargraph.nodes.base import NodeBase


class Probe(NodeBase):
    async def execute(self, state, ctx):
        return {"ok": True}
"""
_PROBE_TEST = """\
import asyncio

from node import Probe


class _S:
    pass


class _Ctx:
    run_id = "t"


def test_probe():
    assert asyncio.run(Probe().execute(_S(), _Ctx())) == {"ok": True}
"""


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _python() -> Check:
    v = sys.version.split()[0]
    return Check("python", bool(sys.executable), f"{sys.executable} ({v})")


def _pytest() -> Check:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return Check("pytest", r.returncode == 0, (r.stdout + r.stderr).strip()[:80])
    except (OSError, subprocess.SubprocessError) as e:
        return Check("pytest", False, f"{type(e).__name__}: {e}")


def _ruff() -> Check:
    # ruff is best-effort in the gate (static F), so absence is a warning, not a fail.
    ruff = Path(sys.executable).with_name("ruff")
    found = ruff.exists()
    return Check("ruff", found, str(ruff) if found else "not found (static F skipped)")


def _dspy() -> Check:
    found = importlib.util.find_spec("dspy") is not None
    return Check("dspy (for `make`)", found, "importable" if found else "missing")


def _writable() -> Check:
    try:
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "probe.txt").write_text("ok", encoding="utf-8")
        home = _ledger.home()
        return Check("filesystem", True, f"temp + ledger home writable ({home})")
    except OSError as e:
        return Check("filesystem", False, f"{type(e).__name__}: {e}")


def _gate_e2e() -> Check:
    try:
        ok, results = verify_sources(_PROBE_NODE, _PROBE_TEST, reads=[], writes=["ok"], fixture={})
        if ok:
            return Check("gate end-to-end", True, "probe node generated, executed, and tested")
        failed = next((r for r in results if not r.passed), None)
        msg = failed.findings[0].get("msg", "") if failed and failed.findings else "unknown"
        kind = failed.kind if failed else "?"
        return Check("gate end-to-end", False, f"{kind}: {str(msg)[:120]}")
    except Exception as e:  # doctor must surface failures as a result, never crash
        return Check("gate end-to-end", False, f"{type(e).__name__}: {e}")


def run_doctor() -> list[Check]:
    """Run all checks. ``ruff``/``dspy`` are soft (warn); the rest are hard."""
    return [_python(), _writable(), _ruff(), _dspy(), _pytest(), _gate_e2e()]


_SOFT = {"ruff", "dspy (for `make`)"}


def healthy(checks: list[Check]) -> bool:
    """True iff every HARD check passed (soft checks may warn)."""
    return all(c.ok for c in checks if c.name not in _SOFT)
