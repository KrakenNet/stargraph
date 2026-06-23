# SPDX-License-Identifier: Apache-2.0
"""Mlsmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real in
every test — these assert the "always works" contract end-to-end: the contract tier
RUNS the trainer, pins the model sha256, constructs a live ``MLNode`` against it, and
runs ``execute()`` on the fixture, so a trainer whose model doesn't serialize, load,
or predict the expected value cannot land even when its own unit test passes. Both
shipped runtimes (sklearn + onnx) are exercised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.mlsmith import _ledger, gate
from stargraph.skills.mlsmith.nodes.build import Build
from stargraph.skills.mlsmith.nodes.recall import Recall
from stargraph.skills.mlsmith.nodes.record import RecordBuild
from stargraph.skills.mlsmith.nodes.triage import TriageGate
from stargraph.skills.mlsmith.seeds import (
    _ONNX_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _ONNX_TEST,  # pyright: ignore[reportPrivateUsage]
    _ONNX_TRAINER,  # pyright: ignore[reportPrivateUsage]
    _SKLEARN_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _SKLEARN_TEST,  # pyright: ignore[reportPrivateUsage]
    _SKLEARN_TRAINER,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.mlsmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

# The good trainer (seed 1: sklearn threshold classifier).
GOOD_TRAINER = _SKLEARN_TRAINER
GOOD_TEST = _SKLEARN_TEST
GOOD_FIXTURE = _SKLEARN_FIXTURE
GOOD_META = {"runtime": "sklearn", "input_field": "x", "output_field": "y"}
GOOD_GEN: dict[str, Any] = {
    "model_name": "threshold-classifier-sklearn",
    "runtime": "sklearn",
    "input_field": "x",
    "output_field": "y",
    "trainer_source": GOOD_TRAINER,
    "fixture": GOOD_FIXTURE,
    "test_source": GOOD_TEST,
}

TRIVIAL_TEST = "def test_x() -> None:\n    assert True\n"

# Adversarial: build_model runs but never writes a model file.
NO_WRITE_TRAINER = """\
from __future__ import annotations


def build_model(path: str) -> None:
    return None  # never serializes a model
"""

# Adversarial: writes a file, but it is not a loadable model — MLNode load must fail.
UNLOADABLE_TRAINER = """\
from __future__ import annotations

from pathlib import Path


def build_model(path: str) -> None:
    Path(path).write_text("not a real model")
"""

# Syntax error → fails the static tier fast; the repair loop can never fix a stub.
BAD_GEN: dict[str, Any] = {
    "model_name": "broken",
    "runtime": "sklearn",
    "input_field": "x",
    "output_field": "y",
    "trainer_source": "def build_model(:\n    pass\n",
    "fixture": {},
    "test_source": TRIVIAL_TEST,
}


def _files(*, trainer: str, test: str) -> dict[str, str]:
    return {gate.TRAINER_FILE: trainer, gate.TEST_FILE: test}


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable floor: the model trains + loads sha-pinned + predicts)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_sklearn_trainer(tmp_path: Path) -> None:
    files = _files(trainer=GOOD_TRAINER, test=GOOD_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=GOOD_FIXTURE)
    assert gate.all_passed(results), [r.findings for r in results if not r.passed]


async def test_gate_passes_a_real_onnx_trainer(tmp_path: Path) -> None:
    files = _files(trainer=_ONNX_TRAINER, test=_ONNX_TEST)
    meta = {"runtime": "onnx", "input_field": "x", "output_field": "y"}
    results = gate.run_full_gate(tmp_path / "g", files, meta=meta, fixture=_ONNX_FIXTURE)
    assert gate.all_passed(results), [r.findings for r in results if not r.passed]


async def test_gate_contract_catches_wrong_prediction(tmp_path: Path) -> None:
    # A model that really trains + loads, but the fixture claims the wrong label.
    files = _files(trainer=GOOD_TRAINER, test=TRIVIAL_TEST)
    bad_fixture = {"input": [[1.0]], "expects": [0]}  # input 1.0 actually predicts 1
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=bad_fixture)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "mismatch" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_model_that_is_never_written(tmp_path: Path) -> None:
    files = _files(trainer=NO_WRITE_TRAINER, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "did not write" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_unloadable_model(tmp_path: Path) -> None:
    files = _files(trainer=UNLOADABLE_TRAINER, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "load" in contract.findings[0]["msg"].lower()


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="threshold classifier"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["model_name"] == "threshold-classifier-sklearn"
    assert out["runtime"] == "sklearn"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="threshold classifier", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["model_name"] == "threshold-classifier-sklearn"
    assert pairs[0]["runtime"] == "sklearn"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"

    bundle = out_dir / "threshold_classifier_sklearn"
    assert final.landed_path == str(bundle / "trainer.py")
    for name in ("trainer.py", "test_trainer.py"):
        assert (bundle / name).exists()


async def test_failure_logs_lesson_and_records_no_pair() -> None:
    state = State(brief="broken thing")
    await drive([stub_build(Build, BAD_GEN), RecordBuild()], state)

    assert _ledger.load_trainset() == []  # no false-positive training data
    lessons = _ledger._read_jsonl(_ledger.home() / _ledger.LESSONS_FILE)  # pyright: ignore[reportPrivateUsage]
    assert any(le["failed_kind"] == "escalate" for le in lessons)


# --------------------------------------------------------------------------- #
# Reflexion recall + drift (idea 1 / idea 2 substrate)
# --------------------------------------------------------------------------- #
async def test_recall_surfaces_relevant_lesson() -> None:
    _ledger.append_lesson(
        brief="a model that scores severity from a feature vector with onnx",
        failed_kind="contract",
        finding="exported onnx model produced the wrong label shape",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated sqlite store thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="score severity from a feature vector, onnx"), CTX)
    assert out["recalled_lessons"]
    assert "onnx" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
