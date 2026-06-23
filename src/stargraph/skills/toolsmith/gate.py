# SPDX-License-Identifier: Apache-2.0
"""The tool smith verify gate — the "always works" contract for tools.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *tool* contract: import the generated module, find the
single ``@tool``-decorated callable, assert its bound ``ToolSpec`` carries valid
JSON Schemas, validate the fixture against ``input_schema``, run the tool on the
fixture, and assert its return validates against ``output_schema``. The fixed
artifact filenames are ``tool.py`` + ``test_tool.py``.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` - tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from stargraph.skills._smith.gate import (
    VerifierResult,
    all_passed,
    make_contract_tier,
    run_tiered_gate,
)

__all__ = [
    "TEST_FILE",
    "TOOL_FILE",
    "VerifierResult",
    "all_passed",
    "run_full_gate",
    "verify_sources",
]

TOOL_FILE = "tool.py"
TEST_FILE = "test_tool.py"

# Driver executed in a subprocess: imports the candidate tool, validates its
# spec + schemas, runs it on the fixture, and prints a one-line JSON verdict.
# Dependency-free beyond stargraph + jsonschema (both present wherever the gate
# runs).
_CONTRACT_DRIVER = """\
import asyncio, importlib.util, inspect, json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
from stargraph.ir._models import ToolSpec


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


contract = json.loads(Path("contract.json").read_text())
fixture = contract.get("fixture", {})

spec_mod = importlib.util.spec_from_file_location("candidate_tool", "tool.py")
mod = importlib.util.module_from_spec(spec_mod)
try:
    spec_mod.loader.exec_module(mod)
except Exception as e:
    _fail(f"import failed: {type(e).__name__}: {e}")

tools = [
    v for v in vars(mod).values()
    if callable(v) and isinstance(getattr(v, "spec", None), ToolSpec)
    and getattr(v, "__module__", None) == mod.__name__
]
if not tools:
    _fail("no @tool-decorated callable defined in tool.py")
if len(tools) > 1:
    _fail(f"expected one tool, found {[t.spec.name for t in tools]}")
fn = tools[0]
spec = fn.spec

for label in ("input_schema", "output_schema"):
    schema = getattr(spec, label)
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as e:
        _fail(f"{label} is not valid JSON Schema: {type(e).__name__}: {e}")

try:
    Draft202012Validator(spec.input_schema).validate(fixture)
except Exception as e:
    _fail(f"fixture does not satisfy input_schema: {e}")

try:
    result = fn(**fixture)
    if inspect.iscoroutine(result):
        result = asyncio.run(result)
except Exception as e:
    _fail(f"tool raised on the fixture: {type(e).__name__}: {e}")

try:
    Draft202012Validator(spec.output_schema).validate(result)
except Exception as e:
    _fail(f"output does not satisfy output_schema: {type(e).__name__}: {e}")

print(json.dumps({"ok": True, "tool": spec.name, "namespace": spec.namespace}))
"""


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    Shared verbatim by the build node and the offline optimizer's metric. The
    contract tier imports the tool, validates its spec/schemas, and runs it on the
    fixture in a subprocess (see ``_CONTRACT_DRIVER``).
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(_CONTRACT_DRIVER, {"fixture": fixture}),
        test_file=TEST_FILE,
    )


def verify_sources(
    tool_source: str,
    test_source: str,
    *,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on raw source in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``toolsmith make``, the doctor preflight, and seed verification.
    Returns ``(passed, results)``.
    """
    files = {TOOL_FILE: tool_source, TEST_FILE: test_source}
    with tempfile.TemporaryDirectory(prefix="toolsmith-verify-") as d:
        results = run_full_gate(Path(d), files, fixture=fixture)
    return all_passed(results), results
