# SPDX-License-Identifier: Apache-2.0
"""The node smith verify gate — the "always works" contract for nodes.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *node* specifics: the contract/run drivers (import the
generated module, find the :class:`NodeBase` subclass, zero-arg construct it, run
``execute()`` against the declared fixture) and the fixed artifact filenames
``node.py`` + ``test_node.py``. ``run_full_gate`` is shared verbatim by the build
node and the offline optimizer, so the optimization metric == the ship criterion.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` - tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox).
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from stargraph.skills._smith.gate import (
    VerifierResult,
    all_passed,
    make_contract_tier,
    run_driver,
    run_tiered_gate,
)

__all__ = [
    "NODE_FILE",
    "TEST_FILE",
    "VerifierResult",
    "all_passed",
    "run_full_gate",
    "run_node",
    "verify_sources",
]

NODE_FILE = "node.py"
TEST_FILE = "test_node.py"

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


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    reads: list[str],
    writes: list[str],
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    Shared verbatim by the build node and the offline optimizer's metric. The
    contract tier imports the node, constructs it, and runs ``execute()`` against a
    fixture state covering the declared reads/writes (see ``_CONTRACT_DRIVER``).
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(
            _CONTRACT_DRIVER,
            {"fixture": fixture, "reads": reads, "writes": writes},
        ),
        test_file=TEST_FILE,
    )


def verify_sources(
    node_source: str,
    test_source: str,
    *,
    reads: list[str],
    writes: list[str],
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on raw source in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — edit-to-gold, ``nodesmith make``, the doctor preflight, and seed
    verification. Returns ``(passed, results)``.
    """
    files = {NODE_FILE: node_source, TEST_FILE: test_source}
    with tempfile.TemporaryDirectory(prefix="nodesmith-verify-") as d:
        results = run_full_gate(Path(d), files, reads=reads, writes=writes, fixture=fixture)
    return all_passed(results), results


# Driver for an ad-hoc run: the same import → construct → execute path as the
# contract tier, but it seeds the state from caller-supplied inputs and reports
# the actual output VALUES (JSON, str-coerced) so a caller can show what the
# node produced and how it lines up with the declared writes.
_RUN_DRIVER = """\
import asyncio, importlib.util, json, sys
from pathlib import Path
from typing import Any
from pydantic import create_model
from stargraph.nodes.base import NodeBase


def _emit(obj):
    print("@@RESULT@@" + json.dumps(obj, default=str))
    sys.exit(0)


def _fail(msg):
    _emit({"ok": False, "msg": msg})


spec_data = json.loads(Path("run.json").read_text())
inputs = spec_data.get("inputs", {})
reads = spec_data.get("reads", [])
writes = spec_data.get("writes", [])

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
cls = classes[0]

try:
    node = cls()
except Exception as e:
    _fail(f"node is not zero-arg constructible: {type(e).__name__}: {e}")

field_keys = set(inputs) | set(reads) | set(writes)
field_defs = {k: (Any, None) for k in field_keys}
StateModel = create_model("RunState", **field_defs)
state = StateModel(**{k: inputs.get(k, None) for k in field_keys})


class _Ctx:
    run_id = "nodesmith-run"


try:
    out = asyncio.run(node.execute(state, _Ctx()))
except Exception as e:
    _fail(f"execute() raised: {type(e).__name__}: {e}")

if not isinstance(out, dict):
    _fail(f"execute() returned {type(out).__name__}, expected dict")

declared = set(writes)
actual = set(out)
_emit({
    "ok": True,
    "output": out,
    "missing_writes": sorted(declared - actual) if declared else [],
    "undeclared": sorted(actual - declared) if declared else [],
})
"""


def run_node(
    node_source: str,
    *,
    inputs: dict[str, Any],
    reads: list[str],
    writes: list[str],
    timeout_s: int = 30,
) -> dict[str, Any]:
    """Execute the node once on caller-supplied ``inputs``, in a throwaway temp dir
    under the same subprocess isolation as the gate's contract tier.

    Returns a dict: ``ok`` (bool); ``msg`` (str, present on failure); ``output``
    (written channel → value); ``missing_writes`` (declared writes not produced);
    ``undeclared`` (keys written but never declared).
    """
    with tempfile.TemporaryDirectory(prefix="nodesmith-run-") as d:
        work = Path(d)
        (work / NODE_FILE).write_text(node_source, encoding="utf-8")
        try:
            _rc, verdict, out = run_driver(
                work,
                driver_src=_RUN_DRIVER,
                driver_name="_run_driver.py",
                payload={"inputs": inputs, "reads": reads, "writes": writes},
                payload_name="run.json",
                marker="@@RESULT@@",
                timeout_s=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "msg": f"node run timed out after {timeout_s}s", "output": {}}

    if verdict is None:
        return {
            "ok": False,
            "msg": f"node run produced no result: {out.strip()[:600]}",
            "output": {},
        }
    verdict.setdefault("output", {})
    verdict.setdefault("missing_writes", [])
    verdict.setdefault("undeclared", [])
    return verdict
