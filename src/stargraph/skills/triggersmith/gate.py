# SPDX-License-Identifier: Apache-2.0
"""The trigger smith verify gate — the "always works" contract for triggers.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *trigger* contract: import the generated module, find the
single trigger class (one defining the full ``init``/``start``/``stop``/``routes``
lifecycle plus ``enqueue``), construct it zero-arg, assert ``init`` guards a
missing scheduler, wire a recording scheduler, emit one run through ``enqueue``,
and assert the trigger truly DELEGATED (one recorded call with the right payload)
and RETURNED the scheduler's run_id. The fixed artifact filenames are
``trigger.py`` + ``test_trigger.py``.

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
    "TRIGGER_FILE",
    "VerifierResult",
    "all_passed",
    "run_full_gate",
    "verify_sources",
]

TRIGGER_FILE = "trigger.py"
TEST_FILE = "test_trigger.py"

# Driver executed in a subprocess: imports the candidate trigger, finds the single
# trigger class, then EXERCISES it against a recording stub scheduler defined here
# (the candidate cannot override it). The enqueue-delegation + returned-run_id
# asserts are the un-cheatable floor: a no-op init or a faked enqueue that does not
# call the scheduler records zero calls / a wrong run_id and fails. Dependency-free
# beyond stargraph (present wherever the gate runs).
_CONTRACT_DRIVER = """\
import importlib.util, json, sys
from pathlib import Path

from stargraph.errors import StargraphRuntimeError


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


class _Handle:
    def __init__(self, run_id):
        self.run_id = run_id


class _RecScheduler:
    def __init__(self):
        self.calls = []

    def enqueue(self, graph_id, params, idempotency_key=None, *, trigger_source="manual"):
        self.calls.append(
            {"graph_id": graph_id, "params": params, "idempotency_key": idempotency_key}
        )
        return _Handle("run-FIXED-123")


contract = json.loads(Path("contract.json").read_text())
graph_id = contract.get("graph_id", "")
params = contract.get("params", {})

spec_mod = importlib.util.spec_from_file_location("candidate_trigger", "trigger.py")
mod = importlib.util.module_from_spec(spec_mod)
try:
    spec_mod.loader.exec_module(mod)
except Exception as e:
    _fail(f"import failed: {type(e).__name__}: {e}")

_LIFECYCLE = ("init", "start", "stop", "routes", "enqueue")
classes = [
    v for v in vars(mod).values()
    if isinstance(v, type)
    and getattr(v, "__module__", None) == mod.__name__
    and all(callable(getattr(v, m, None)) for m in _LIFECYCLE)
]
if not classes:
    _fail("no trigger class defining init/start/stop/routes/enqueue in trigger.py")
if len(classes) > 1:
    _fail(f"expected one trigger class, found {[c.__name__ for c in classes]}")
cls = classes[0]

try:
    t = cls()
except Exception as e:
    _fail(f"trigger class is not zero-arg constructible: {type(e).__name__}: {e}")

# init guard: a fresh instance with no scheduler in deps must raise (proves init
# is real, not a pass-stub).
t2 = cls()
try:
    t2.init({})
except StargraphRuntimeError:
    pass
except Exception as e:
    _fail(f"init({{}}) raised {type(e).__name__}, expected StargraphRuntimeError: {e}")
else:
    _fail("init({}) did not raise StargraphRuntimeError for a missing scheduler")

# wire + emit: init with a recording scheduler, then enqueue once.
rec = _RecScheduler()
try:
    t.init({"scheduler": rec})
except Exception as e:
    _fail(f"init with scheduler raised: {type(e).__name__}: {e}")

try:
    run_id = t.enqueue(graph_id, params)
except Exception as e:
    _fail(f"enqueue raised on the fixture: {type(e).__name__}: {e}")

if run_id != "run-FIXED-123":
    _fail(f"enqueue returned {run_id!r}, expected the scheduler's run_id 'run-FIXED-123'")
if len(rec.calls) != 1:
    _fail(f"expected exactly one scheduler.enqueue call, recorded {len(rec.calls)}")
call = rec.calls[0]
if call["graph_id"] != graph_id:
    _fail(f"scheduler received graph_id {call['graph_id']!r}, expected {graph_id!r}")
if call["params"] != params:
    _fail(f"scheduler received params {call['params']!r}, expected {params!r}")

try:
    routes = t.routes()
except Exception as e:
    _fail(f"routes() raised: {type(e).__name__}: {e}")
if not (isinstance(routes, list) and len(routes) == 0):
    _fail(f"routes() returned {routes!r}, expected [] for a manual trigger")

try:
    t.start()
    t.start()
    t.stop()
    t.stop()
except Exception as e:
    _fail(f"start/stop are not idempotent no-ops: {type(e).__name__}: {e}")

print(json.dumps({"ok": True, "class": cls.__name__}))
"""


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    Shared verbatim by the build node and the offline optimizer's metric. The
    ``fixture`` carries ``graph_id`` (str) + ``params`` (dict) the contract tier
    enqueues against a recording scheduler (see ``_CONTRACT_DRIVER``).
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(
            _CONTRACT_DRIVER,
            {
                "graph_id": str(fixture.get("graph_id", "")),
                "params": dict(fixture.get("params", {})),
            },
        ),
        test_file=TEST_FILE,
    )


def verify_sources(
    trigger_source: str,
    test_source: str,
    *,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on raw source in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``triggersmith make``, the doctor preflight, and seed verification.
    Returns ``(passed, results)``.
    """
    files = {TRIGGER_FILE: trigger_source, TEST_FILE: test_source}
    with tempfile.TemporaryDirectory(prefix="triggersmith-verify-") as d:
        results = run_full_gate(Path(d), files, fixture=fixture)
    return all_passed(results), results
