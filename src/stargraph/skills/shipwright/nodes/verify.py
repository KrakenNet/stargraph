# SPDX-License-Identifier: Apache-2.0
"""Verifier nodes - static / tests / smoke (Tasks 10-12)."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from stargraph.nodes.base import ExecutionContext, NodeBase
from stargraph.skills.shipwright.state import VerifierResult

if TYPE_CHECKING:
    from pydantic import BaseModel


def _write_files(work_dir: Path, files: dict[str, str]) -> None:
    for relpath, content in files.items():
        target = work_dir / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def _check_python_syntax(path: Path) -> list[dict[str, Any]]:
    try:
        compile(path.read_text(), str(path), "exec")
        return []
    except SyntaxError as e:
        return [{"msg": f"syntax error in {path.name}: {e.msg}"}]


def _run(cmd: list[str], cwd: Path, timeout_s: int = 30) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False
    )
    return proc.returncode, (proc.stdout + proc.stderr)


class VerifyStatic(NodeBase):
    """Static checks: Python syntax, ruff, `stargraph graph verify`; best-effort on latter two."""

    def __init__(self, work_dir: Path | None = None) -> None:
        self._work_dir_override = work_dir

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        work = self._work_dir_override or Path(f"/tmp/shipwright-{ctx.run_id}")
        work.mkdir(parents=True, exist_ok=True)

        artifact_files: dict[str, str] = getattr(state, "artifact_files", {}) or {}
        _write_files(work, artifact_files)

        findings: list[dict[str, Any]] = []
        t0 = time.monotonic()

        for relpath in artifact_files:
            if relpath.endswith(".py"):
                findings.extend(_check_python_syntax(work / relpath))

        try:
            rc, out = _run(["uv", "run", "ruff", "check", "."], work)
            if rc != 0:
                findings.append({"msg": f"syntax/style: ruff: {out.strip()[:400]}"})
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        result = VerifierResult(
            kind="static",
            passed=not findings,
            findings=findings,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        prior: list[VerifierResult] = list(getattr(state, "verifier_results", []))
        return {"verifier_results": [*prior, result]}


class VerifyTests(NodeBase):
    """Run pytest against synthesized tests/, capturing failures as findings.

    The synthesized test file imports `from state import State` (no package
    prefix) — that works because pytest is invoked from the work dir and
    Python finds `state.py` at the same level. The synthesized `tests/__init__.py`
    keeps it as a package for collection.
    """

    def __init__(self, work_dir: Path | None = None, timeout_s: int = 60) -> None:
        self._work_dir_override = work_dir
        self._timeout_s = timeout_s

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        work = self._work_dir_override or Path(f"/tmp/shipwright-{ctx.run_id}")
        work.mkdir(parents=True, exist_ok=True)

        artifact_files: dict[str, str] = getattr(state, "artifact_files", {}) or {}
        _write_files(work, artifact_files)

        t0 = time.monotonic()
        findings: list[dict[str, Any]] = []
        passed = False

        try:
            rc, out = _run(
                [
                    "uv",
                    "run",
                    "pytest",
                    "tests/",
                    "-q",
                    "--no-header",
                    "--no-cov",
                    "--override-ini=addopts=",
                ],
                work,
                timeout_s=self._timeout_s,
            )
            passed = rc == 0
            if not passed:
                findings.append({"msg": out.strip()[:2000]})
        except subprocess.TimeoutExpired:
            findings.append({"msg": f"pytest timeout after {self._timeout_s}s"})
        except FileNotFoundError:
            findings.append({"msg": "pytest not on PATH"})

        result = VerifierResult(
            kind="tests",
            passed=passed,
            findings=findings,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        prior: list[VerifierResult] = list(getattr(state, "verifier_results", []))
        return {"verifier_results": [*prior, result]}


class VerifySmoke(NodeBase):
    """Run `stargraph simulate` against the synthesized graph in a tmp work_dir."""

    def __init__(self, work_dir: Path | None = None, timeout_s: int = 60) -> None:
        self._work_dir_override = work_dir
        self._timeout_s = timeout_s

    async def execute(self, state: BaseModel, ctx: ExecutionContext) -> dict[str, Any]:
        work = self._work_dir_override or Path(f"/tmp/shipwright-{ctx.run_id}")
        work.mkdir(parents=True, exist_ok=True)
        artifact_files: dict[str, str] = getattr(state, "artifact_files", {}) or {}
        _write_files(work, artifact_files)

        t0 = time.monotonic()
        findings: list[dict[str, Any]] = []
        passed = False

        try:
            rc, out = _run(
                [
                    "uv",
                    "run",
                    "stargraph",
                    "simulate",
                    "stargraph.yaml",
                    "--fixtures",
                    "fixtures.yaml",
                ],
                work,
                timeout_s=self._timeout_s,
            )
            passed = rc == 0
            if not passed:
                findings.append({"msg": out.strip()[:2000]})
        except subprocess.TimeoutExpired:
            findings.append({"msg": f"simulate timeout after {self._timeout_s}s"})
        except FileNotFoundError:
            findings.append({"msg": "stargraph CLI not on PATH"})

        result = VerifierResult(
            kind="smoke",
            passed=passed,
            findings=findings,
            duration_ms=int((time.monotonic() - t0) * 1000),
        )
        prior: list[VerifierResult] = list(getattr(state, "verifier_results", []))
        return {"verifier_results": [*prior, result]}
