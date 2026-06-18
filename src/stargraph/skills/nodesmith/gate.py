# SPDX-License-Identifier: Apache-2.0
"""The nodesmith verify gate — the "always works" contract.

``run_full_gate`` runs three tiers in a scratch work dir, short-circuiting on
the first failure. It is shared verbatim by the build node and the offline
optimizer, so the optimization metric is *exactly* the gate that ships nodes:

1. static   — Python ``compile`` + ``ruff --select F`` (real errors).
2. contract — the un-cheatable floor: imports the generated node in a
   **subprocess**, finds the :class:`NodeBase` subclass, zero-arg constructs it,
   runs ``execute()`` against the declared fixture state, and asserts it returns
   a dict whose keys are a subset of the declared writes. A bogus passing test
   cannot satisfy this — the framework runs the node itself.
3. tests    — pytest the generated ``test_node.py``.

Generated artifacts are fixed-name: ``node.py`` (the node) + ``test_node.py``
(its test, importing ``from node import <ClassName>``).

TRUST BOUNDARY: tiers 2 and 3 EXECUTE LLM-generated code in a subprocess that
runs as the invoking user with full network and filesystem access — process
isolation only, not a sandbox. Do not run nodesmith as a privileged user or put
secrets in ``fixture`` values. Generation happens in a fresh per-run temp dir
(see ``nodes/build.py``); ``write_files`` refuses any path that escapes it.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .state import VerifierResult

NODE_FILE = "node.py"
TEST_FILE = "test_node.py"

# The running interpreter — its env has stargraph + pytest installed, so
# subprocesses resolve `import stargraph` regardless of the scratch cwd
# (unlike `uv run`, which needs to find the project from the working dir).
_PY = sys.executable


def _ruff_bin() -> Path | None:
    candidate = Path(_PY).with_name("ruff")
    return candidate if candidate.exists() else None


def write_files(work_dir: Path, files: dict[str, str]) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    base = work_dir.resolve()
    for relpath, content in files.items():
        target = (work_dir / relpath).resolve()
        if not target.is_relative_to(base):  # never let a key escape the scratch dir
            raise ValueError(f"refusing to write outside work dir: {relpath!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _run(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[int, str]:
    proc = subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout_s, check=False
    )
    return proc.returncode, (proc.stdout + proc.stderr)


def _check_syntax(path: Path) -> list[dict[str, Any]]:
    try:
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
        return []
    except SyntaxError as e:
        return [
            {"tier": "static", "msg": f"syntax error in {path.name}: {e.msg} (line {e.lineno})"}
        ]


def _static(work_dir: Path, files: dict[str, str]) -> VerifierResult:
    """Syntax + ``ruff --select F`` (undefined names, unused imports, real errors)."""
    t0 = time.monotonic()
    findings: list[dict[str, Any]] = []

    for relpath in files:
        if relpath.endswith(".py"):
            findings.extend(_check_syntax(work_dir / relpath))

    ruff = _ruff_bin()
    if not findings and ruff is not None:  # ruff is best-effort + only meaningful once it parses
        try:
            rc, out = _run([str(ruff), "check", "--select", "F", "."], work_dir, 30)
            if rc != 0:
                findings.append({"tier": "static", "msg": f"ruff F: {out.strip()[:600]}"})
        except subprocess.TimeoutExpired:
            findings.append({"tier": "static", "msg": "ruff timeout"})

    return VerifierResult(
        kind="static",
        passed=not findings,
        findings=findings,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


# Driver executed in a subprocess: imports the candidate node, runs execute()
# against the fixture, and prints a one-line JSON verdict. Kept dependency-free
# beyond stargraph + pydantic (both present wherever the gate runs).
_CONTRACT_DRIVER = """\
import asyncio, importlib.util, json, sys
from pathlib import Path
from typing import Any
from pydantic import create_model
from stargraph.nodes.base import NodeBase


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


contract = json.loads(Path("contract.json").read_text())
fixture = contract.get("fixture", {})
reads = contract.get("reads", [])
writes = contract.get("writes", [])

spec = importlib.util.spec_from_file_location("candidate_node", "node.py")
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as e:
    _fail(f"import failed: {type(e).__name__}: {e}")

classes = [
    c for c in vars(mod).values()
    if isinstance(c, type) and issubclass(c, NodeBase) and c is not NodeBase
    and c.__module__ == mod.__name__
]
if not classes:
    _fail("no NodeBase subclass defined in node.py")
if len(classes) > 1:
    _fail(f"expected one NodeBase subclass, found {[c.__name__ for c in classes]}")
cls = classes[0]

try:
    node = cls()
except Exception as e:
    _fail(f"node is not zero-arg constructible: {type(e).__name__}: {e}")

# Build a fixture state covering fixture values + every declared read/write
# (reads must be satisfied or execute() will AttributeError; writes default None).
field_keys = set(fixture) | set(reads) | set(writes)
field_defs = {k: (Any, fixture.get(k, None)) for k in field_keys}
StateModel = create_model("FixtureState", **field_defs)
state = StateModel(**{k: fixture.get(k, None) for k in field_keys})


class _Ctx:
    run_id = "nodesmith-contract"


try:
    out = asyncio.run(node.execute(state, _Ctx()))
except Exception as e:
    _fail(f"execute() raised: {type(e).__name__}: {e}")

if not isinstance(out, dict):
    _fail(f"execute() returned {type(out).__name__}, expected dict")
if not out:
    _fail("execute() returned an empty dict (no state contribution)")
if writes:
    surprise = sorted(set(out) - set(writes))
    if surprise:
        _fail(f"execute() wrote undeclared fields {surprise} (declared writes: {sorted(writes)})")

print(json.dumps({"ok": True, "keys": sorted(out.keys())}))
"""


def _contract(
    work_dir: Path,
    *,
    reads: list[str],
    writes: list[str],
    fixture: dict[str, Any],
    timeout_s: int = 30,
) -> VerifierResult:
    """Import → construct → run ``execute()`` on the fixture in a subprocess."""
    (work_dir / "_contract_driver.py").write_text(_CONTRACT_DRIVER, encoding="utf-8")
    (work_dir / "contract.json").write_text(
        json.dumps({"fixture": fixture, "reads": reads, "writes": writes}), encoding="utf-8"
    )

    t0 = time.monotonic()
    findings: list[dict[str, Any]] = []
    try:
        rc, out = _run([_PY, "_contract_driver.py"], work_dir, timeout_s)
        verdict: dict[str, Any] = {}
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    verdict = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        if rc != 0 and not verdict:
            findings.append(
                {"tier": "contract", "msg": f"contract driver crashed: {out.strip()[:600]}"}
            )
        elif not verdict.get("ok"):
            findings.append({"tier": "contract", "msg": verdict.get("msg", "contract failed")})
    except subprocess.TimeoutExpired:
        findings.append({"tier": "contract", "msg": f"contract timeout after {timeout_s}s"})

    return VerifierResult(
        kind="contract",
        passed=not findings,
        findings=findings,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def _tests(work_dir: Path, *, timeout_s: int = 60) -> VerifierResult:
    """Run pytest against the generated ``test_node.py``."""
    t0 = time.monotonic()
    findings: list[dict[str, Any]] = []
    passed = False
    try:
        rc, out = _run(
            [
                _PY,
                "-m",
                "pytest",
                TEST_FILE,
                "-q",
                "--no-header",
                "--no-cov",
                "--override-ini=addopts=",
            ],
            work_dir,
            timeout_s,
        )
        passed = rc == 0
        if not passed:
            findings.append({"tier": "tests", "msg": out.strip()[:1500]})
    except subprocess.TimeoutExpired:
        findings.append({"tier": "tests", "msg": f"pytest timeout after {timeout_s}s"})

    return VerifierResult(
        kind="tests",
        passed=passed,
        findings=findings,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    reads: list[str],
    writes: list[str],
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """Write the artifacts once, then run the tiers, short-circuiting on the
    first failure.

    The first failing tier's findings are enough to drive the next repair
    attempt, so skipping the remaining tiers just saves a subprocess or two.
    Shared verbatim by the build node and the offline optimizer's metric.
    """
    write_files(work_dir, files)
    static = _static(work_dir, files)
    if not static.passed:
        return [static]
    contract = _contract(work_dir, reads=reads, writes=writes, fixture=fixture)
    if not contract.passed:
        return [static, contract]
    return [static, contract, _tests(work_dir)]


def all_passed(results: list[VerifierResult]) -> bool:
    by_kind = {r.kind: r for r in results}
    needed = {"static", "contract", "tests"}
    return needed.issubset(by_kind) and all(by_kind[k].passed for k in needed)
