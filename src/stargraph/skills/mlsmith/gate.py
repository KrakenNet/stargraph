# SPDX-License-Identifier: Apache-2.0
"""The ML smith verify gate — the "always works" contract for model nodes.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *model node* contract: write the bundle (``trainer.py`` +
``test_trainer.py``), then in a subprocess RUN the trainer to produce a real model
file, pin its sha256, construct a real :class:`stargraph.nodes.ml.MLNode` against it
(under the declared ``runtime`` — ``sklearn`` joblib or ``onnx`` session — with the
sklearn pickle gate opted in and the sha verified before any unpickling), and RUN
``execute()`` on the fixture's input — asserting the output matches the fixture's
expected prediction. Because the assert runs against a live MLNode's prediction, a
trivially-passing generated unit test cannot land a trainer whose model doesn't
serialize, load (sha-pinned), or predict.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` — tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox). The contract
tier additionally RUNS the generated trainer AND loads the model file it produces via
MLNode with ``allow_unsafe_pickle=True`` for the sklearn runtime — i.e. it unpickles
a file produced by the same untrusted trainer in that subprocess. This is no wider
than already running the trainer; treat a generated trainer + its model as untrusted
code and never run a smith privileged.
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
    "TRAINER_FILE",
    "VerifierResult",
    "all_passed",
    "run_full_gate",
    "verify_sources",
]

TRAINER_FILE = "trainer.py"
TEST_FILE = "test_trainer.py"


# Driver executed in a subprocess: run the generated trainer to produce a real model
# file, pin its sha256, construct a live MLNode against it, and run execute() on the
# fixture input. The payload carries ``meta`` (runtime + the input/output field names)
# and ``fixture`` (the input vector + the expected prediction).
_CONTRACT_DRIVER = """\
import asyncio, hashlib, importlib, json, sys
from pathlib import Path
from types import SimpleNamespace

from stargraph.nodes.ml import MLNode


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


# trainer.py is imported by bare name, so the scratch dir must lead sys.path.
sys.path.insert(0, str(Path.cwd()))

contract = json.loads(Path("contract.json").read_text())
meta = contract.get("meta", {})
fixture = contract.get("fixture", {})
runtime = str(meta.get("runtime", ""))
input_field = str(meta.get("input_field", "x")) or "x"
output_field = str(meta.get("output_field", "y")) or "y"
model_inputs = fixture.get("input")
expects = fixture.get("expects")

if runtime not in ("sklearn", "onnx"):
    _fail(f"runtime must be 'sklearn' or 'onnx', got {runtime!r}")

try:
    trainer = importlib.import_module("trainer")
except Exception as e:
    _fail(f"trainer.py did not import: {type(e).__name__}: {e}")
build = getattr(trainer, "build_model", None)
if build is None or not callable(build):
    _fail("trainer.py must define a callable build_model(path: str) -> None")

model_path = str(Path.cwd() / ("model.onnx" if runtime == "onnx" else "model.pkl"))
try:
    build(model_path)
except Exception as e:
    _fail(f"build_model(path) raised: {type(e).__name__}: {e}")
if not Path(model_path).exists():
    _fail("build_model(path) did not write a model file at the given path")

sha = hashlib.sha256(Path(model_path).read_bytes()).hexdigest()

# Construct a live MLNode against the just-trained file: sha-pinned, pickle gate
# opted in for sklearn. A model that does not load surfaces here.
try:
    node = MLNode(
        model_id="mlsmith-contract",
        version="1",
        runtime=runtime,
        file_uri=f"file://{model_path}",
        allow_unsafe_pickle=(runtime == "sklearn"),
        expected_sha256=sha,
        input_field=input_field,
        output_field=output_field,
    )
except Exception as e:
    _fail(f"MLNode failed to load the model (runtime/sha/format?): {type(e).__name__}: {e}")

state = SimpleNamespace(**{input_field: model_inputs})
ctx = SimpleNamespace(run_id="mlsmith-contract")
try:
    out = asyncio.run(node.execute(state, ctx))
except Exception as e:
    _fail(f"MLNode.execute raised: {type(e).__name__}: {e}")

if output_field not in out:
    _fail(f"execute did not write the output field {output_field!r} (got keys {sorted(out)})")
got = out[output_field]
if hasattr(got, "tolist"):
    got = got.tolist()  # numpy array (sklearn predict) -> JSON-comparable list

if expects is None:
    if got in (None, "", [], {}):
        _fail(f"expected a non-empty prediction in {output_field!r}, got {got!r}")
elif got != expects:
    _fail(f"model prediction {got!r} != expected {expects!r} (model/fixture mismatch)")

print(json.dumps({"ok": True, "runtime": runtime, "sha256": sha[:12]}))
"""


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    meta: dict[str, str],
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    The contract tier runs the generated ``trainer.py`` to produce a model file, then
    constructs a live :class:`MLNode` against it and runs ``execute()`` on
    ``fixture``; see ``_CONTRACT_DRIVER``.
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(_CONTRACT_DRIVER, {"meta": meta, "fixture": fixture}),
        test_file=TEST_FILE,
    )


def verify_sources(
    *,
    runtime: str,
    input_field: str,
    output_field: str,
    trainer_source: str,
    test_source: str,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on a raw trainer bundle in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``mlsmith make``, the doctor preflight, and seed verification. Returns
    ``(passed, results)``.
    """
    files = {TRAINER_FILE: trainer_source, TEST_FILE: test_source}
    meta = {"runtime": runtime, "input_field": input_field, "output_field": output_field}
    with tempfile.TemporaryDirectory(prefix="mlsmith-verify-") as d:
        results = run_full_gate(Path(d), files, meta=meta, fixture=fixture)
    return all_passed(results), results
