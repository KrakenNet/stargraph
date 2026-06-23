# SPDX-License-Identifier: Apache-2.0
"""Gate machinery — domain-agnostic verifier tiers + subprocess isolation.

Every smith's gate has the same three-tier shape — **static** (compile + ``ruff
--select F``), **contract** (the un-cheatable floor: run the artifact itself in a
subprocess), **tests** (pytest the generated test) — short-circuiting on the
first failure. The static and tests tiers are identical across smiths and live
here; the contract tier differs per artifact (a node runs ``execute()``, a tool
calls the function, …) so each smith supplies that one driver.

TRUST BOUNDARY: the contract and tests tiers EXECUTE LLM-generated code in a
subprocess that runs as the invoking user with full network + filesystem access
— process isolation, not a sandbox. Callers run every tier in a fresh throwaway
temp dir; ``write_files`` refuses any path that escapes it. Don't run a smith as
a privileged user, and don't put secrets in fixture/input values.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable

# The running interpreter — its env has stargraph + pytest installed, so
# subprocesses resolve `import stargraph` regardless of the scratch cwd.
PYTHON = sys.executable

REQUIRED_TIERS = ("static", "contract", "tests")


class VerifierResult(BaseModel):
    """One gate tier result. ``kind`` ∈ {static, contract, tests}."""

    kind: str
    passed: bool
    findings: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    duration_ms: int = 0


def ruff_bin() -> Path | None:
    candidate = Path(PYTHON).with_name("ruff")
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


def run_cmd(cmd: list[str], cwd: Path, timeout_s: int) -> tuple[int, str]:
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


def static_tier(work_dir: Path, files: dict[str, str]) -> VerifierResult:
    """Syntax + ``ruff --select F`` (undefined names, unused imports, real errors)."""
    t0 = time.monotonic()
    findings: list[dict[str, Any]] = []

    for relpath in files:
        if relpath.endswith(".py"):
            findings.extend(_check_syntax(work_dir / relpath))

    ruff = ruff_bin()
    if not findings and ruff is not None:  # ruff is best-effort + only meaningful once it parses
        try:
            rc, out = run_cmd([str(ruff), "check", "--select", "F", "."], work_dir, 30)
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


def pytest_tier(work_dir: Path, test_file: str, *, timeout_s: int = 60) -> VerifierResult:
    """Run pytest against the generated test file."""
    t0 = time.monotonic()
    findings: list[dict[str, Any]] = []
    passed = False
    try:
        rc, out = run_cmd(
            [
                PYTHON,
                "-m",
                "pytest",
                test_file,
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


def _parse_verdict(out: str, marker: str | None) -> dict[str, Any] | None:
    """Last JSON object printed by a driver — bare ``{...}`` or ``<marker>{...}``."""
    for line in reversed(out.splitlines()):
        line = line.strip()
        if marker:
            if line.startswith(marker):
                try:
                    return json.loads(line[len(marker) :])
                except json.JSONDecodeError:
                    continue
        elif line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    return None


def run_driver(
    work_dir: Path,
    *,
    driver_src: str,
    driver_name: str,
    payload: dict[str, Any],
    payload_name: str,
    marker: str | None = None,
    timeout_s: int = 30,
) -> tuple[int, dict[str, Any] | None, str]:
    """Write a self-contained driver + its JSON payload into the work dir, run it
    in a subprocess, and return ``(returncode, parsed_verdict_or_None, raw_output)``.

    Raises ``subprocess.TimeoutExpired`` on timeout (callers decide how to report it).
    The driver communicates by printing one JSON verdict line (optionally prefixed
    with ``marker`` so it survives noisy stdout).
    """
    (work_dir / driver_name).write_text(driver_src, encoding="utf-8")
    (work_dir / payload_name).write_text(json.dumps(payload), encoding="utf-8")
    rc, out = run_cmd([PYTHON, driver_name], work_dir, timeout_s)
    return rc, _parse_verdict(out, marker), out


def contract_from_verdict(
    rc: int, verdict: dict[str, Any] | None, out: str, *, t0: float
) -> VerifierResult:
    """Map a contract driver's ``(rc, verdict, out)`` into a ``contract`` result."""
    findings: list[dict[str, Any]] = []
    if rc != 0 and not verdict:
        findings.append(
            {"tier": "contract", "msg": f"contract driver crashed: {out.strip()[:600]}"}
        )
    elif not (verdict or {}).get("ok"):
        findings.append({"tier": "contract", "msg": (verdict or {}).get("msg", "contract failed")})
    return VerifierResult(
        kind="contract",
        passed=not findings,
        findings=findings,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


def all_passed(results: list[VerifierResult], required: tuple[str, ...] = REQUIRED_TIERS) -> bool:
    by_kind = {r.kind: r for r in results}
    return set(required).issubset(by_kind) and all(by_kind[k].passed for k in required)


def make_contract_tier(
    driver_src: str,
    payload: dict[str, Any],
    *,
    timeout_s: int = 30,
) -> Callable[[Path], VerifierResult]:
    """Bind a smith's self-contained contract driver + its JSON payload into a
    contract tier for :func:`run_tiered_gate`.

    The wrapper — run the driver in a subprocess, turn a ``TimeoutExpired`` into a
    failed ``contract`` result, else map the verdict line via
    :func:`contract_from_verdict` — is identical for every smith; only the driver
    string + payload differ. A smith builds the payload (its own fixture shape) at
    call time and supplies its driver here.
    """

    def _tier(work_dir: Path) -> VerifierResult:
        t0 = time.monotonic()
        try:
            rc, verdict, out = run_driver(
                work_dir,
                driver_src=driver_src,
                driver_name="_contract_driver.py",
                payload=payload,
                payload_name="contract.json",
                timeout_s=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return VerifierResult(
                kind="contract",
                passed=False,
                findings=[{"tier": "contract", "msg": f"contract timeout after {timeout_s}s"}],
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
        return contract_from_verdict(rc, verdict, out, t0=t0)

    return _tier


def run_tiered_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    contract_tier: Callable[[Path], VerifierResult],
    test_file: str,
) -> list[VerifierResult]:
    """Write the artifacts once, then run static → contract → tests, short-circuiting
    on the first failure. The contract tier is supplied by the smith (its driver
    is the only domain-specific part); static and tests are identical everywhere.
    """
    write_files(work_dir, files)
    static = static_tier(work_dir, files)
    if not static.passed:
        return [static]
    contract = contract_tier(work_dir)
    if not contract.passed:
        return [static, contract]
    return [static, contract, pytest_tier(work_dir, test_file)]


# The shared contract-driver prelude for any smith whose artifact is a runnable
# graph bundle (``state.py`` + ``nodes.py`` + an auto-wired ``graph.yaml``): load the
# IR into a real ``Graph``, build the node registry, run it to a terminal
# ``ResultEvent``, and assert it reached ``status="done"`` with the fixture's
# ``expects`` produced. Mirrors the headless run path of ``stargraph run``. A
# composite concatenates its own verdict/extension code AFTER this (it must print
# the final ``{"ok": true, ...}`` line) and passes ``meta={"run_id", "noun"}`` in the
# contract payload (``run_id`` namespaces the run; ``noun`` flavors error messages,
# e.g. "graph" vs "subgraph"). ``g``, ``ir``, ``final``, and ``expects`` are left in
# scope for the extension code. Dependency-free beyond stargraph + anyio + pyyaml.
RUN_GRAPH_PRELUDE = """\
import asyncio, json, sys, tempfile
from pathlib import Path

import anyio
import yaml

from stargraph.checkpoint.sqlite import SQLiteCheckpointer
from stargraph.cli.run import _build_node_registry
from stargraph.graph import Graph, GraphRun
from stargraph.ir import IRDocument
from stargraph.runtime.events import ResultEvent


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


# The bundle modules (state.py, nodes.py) import by bare name, so the scratch dir
# must lead sys.path (Graph() imports state_class; the registry imports each kind).
sys.path.insert(0, str(Path.cwd()))

contract = json.loads(Path("contract.json").read_text())
_meta = contract.get("meta", {})
_run_id = str(_meta.get("run_id", "smith-contract"))
_noun = str(_meta.get("noun", "graph"))
fixture = contract.get("fixture", {})
inputs = fixture.get("inputs", {}) or {}
expects = fixture.get("expects", {}) or {}

try:
    ir = IRDocument.model_validate(yaml.safe_load(Path("graph.yaml").read_text()))
except Exception as e:
    _fail(f"graph.yaml is not valid IR: {type(e).__name__}: {e}")

try:
    g = Graph(ir)
except Exception as e:
    _fail(f"Graph construction failed (state.py / state_class?): {type(e).__name__}: {e}")

try:
    node_registry = _build_node_registry(ir.nodes, ir_dir=Path.cwd())
except Exception as e:
    _fail(f"a node kind did not resolve to a NodeBase: {type(e).__name__}: {e}")

try:
    initial_state = g.state_schema(**inputs)
except Exception as e:
    _fail(f"initial state did not construct from inputs {sorted(inputs)}: {type(e).__name__}: {e}")


async def _run():
    with tempfile.TemporaryDirectory() as d:
        cp = SQLiteCheckpointer(Path(d) / "run.sqlite")
        await cp.bootstrap()
        captured = {}
        try:
            run = GraphRun(
                run_id=_run_id,
                graph=g,
                initial_state=initial_state,
                node_registry=node_registry,
                checkpointer=cp,
            )

            async def _drive():
                captured["summary"] = await run.start()

            async def _drain():
                with anyio.fail_after(30):
                    while True:
                        ev = await run.bus.receive()
                        if isinstance(ev, ResultEvent):
                            captured["event"] = ev
                            return

            async with anyio.create_task_group() as tg:
                tg.start_soon(_drive)
                tg.start_soon(_drain)
        finally:
            await cp.close()
        return captured


try:
    captured = asyncio.run(_run())
except Exception as e:
    _fail(f"{_noun} run raised: {type(e).__name__}: {e}")

ev = captured.get("event")
if ev is None:
    _fail(f"{_noun} run produced no terminal ResultEvent")
if ev.status != "done":
    _fail(f"{_noun} did not run to completion: terminal status={ev.status!r}")

final = ev.final_state if isinstance(ev.final_state, dict) else {}
for field, want in expects.items():
    if field not in final:
        _fail(f"expected output field {field!r} absent from final state {sorted(final)}")
    got = final[field]
    if want is None:
        if got in (None, "", [], {}, 0, False):
            _fail(f"expected field {field!r} to be populated by the {_noun}, got {got!r}")
    elif got != want:
        _fail(f"final {field!r}={got!r}, expected {want!r} (nodes did not wire end-to-end)")
"""
