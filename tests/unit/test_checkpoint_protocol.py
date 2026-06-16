# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``stargraph.checkpoint.protocol`` (FR-16).

Pins the Pydantic shape of :class:`Checkpoint` / :class:`RunSummary`
plus the structural contract of the :class:`Checkpointer` ``Protocol``
(5 required methods per design §3.2.1). Also exercises cf-prefix
detection -- counterfactual run_ids are minted as ``f"cf-{uuid4()}"``
in :mod:`stargraph.graph.run`, so any row whose ``run_id`` starts with
``"cf-"`` is a counterfactual child.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any, Protocol, get_type_hints

import pytest
from pydantic import ValidationError

from stargraph.checkpoint import Checkpoint, Checkpointer, RunSummary

# ---------------------------------------------------------------------------
# Checkpoint -- Pydantic shape, required fields, per-field type assertions
# ---------------------------------------------------------------------------

_CHECKPOINT_REQUIRED_FIELDS = {
    "run_id",
    "step",
    "branch_id",
    "parent_step_idx",
    "graph_hash",
    "runtime_hash",
    "state",
    "clips_facts",
    "last_node",
    "next_action",
    "timestamp",
    "parent_run_id",
    "side_effects_hash",
}


def _valid_checkpoint_kwargs() -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "step": 0,
        "branch_id": None,
        "parent_step_idx": None,
        "graph_hash": "a" * 64,
        "runtime_hash": "b" * 64,
        "state": {"k": "v"},
        "clips_facts": [],
        "last_node": "start",
        "next_action": None,
        "timestamp": datetime.now(UTC),
        "parent_run_id": None,
        "side_effects_hash": "c" * 64,
    }


def test_checkpoint_has_exactly_13_fields() -> None:
    # design §3.2.1 enumerates 13 fields (the 12-field summary in the
    # docstring counts (run_id, step) as a composite identity).
    assert set(Checkpoint.model_fields.keys()) == _CHECKPOINT_REQUIRED_FIELDS


def test_checkpoint_constructs_with_valid_payload() -> None:
    cp = Checkpoint(**_valid_checkpoint_kwargs())
    assert cp.run_id == "run-1"
    assert cp.step == 0
    assert cp.branch_id is None


@pytest.mark.parametrize("missing", sorted(_CHECKPOINT_REQUIRED_FIELDS))
def test_checkpoint_rejects_missing_required_field(missing: str) -> None:
    kwargs = _valid_checkpoint_kwargs()
    del kwargs[missing]
    with pytest.raises(ValidationError) as exc_info:
        Checkpoint(**kwargs)
    assert any(err["loc"] == (missing,) for err in exc_info.value.errors())


def test_checkpoint_field_types() -> None:
    hints = get_type_hints(Checkpoint)
    assert hints["run_id"] is str
    assert hints["step"] is int
    assert hints["branch_id"] == (str | None)
    assert hints["parent_step_idx"] == (int | None)
    assert hints["graph_hash"] is str
    assert hints["runtime_hash"] is str
    assert hints["state"] == dict[str, Any]
    assert hints["clips_facts"] == list[Any]
    assert hints["last_node"] is str
    assert hints["next_action"] == (dict[str, Any] | None)
    assert hints["timestamp"] is datetime
    assert hints["parent_run_id"] == (str | None)
    assert hints["side_effects_hash"] is str


def test_checkpoint_rejects_wrong_type_for_step() -> None:
    kwargs = _valid_checkpoint_kwargs()
    kwargs["step"] = "not-an-int"
    with pytest.raises(ValidationError):
        Checkpoint(**kwargs)


def test_checkpoint_rejects_wrong_type_for_state() -> None:
    kwargs = _valid_checkpoint_kwargs()
    kwargs["state"] = "not-a-dict"
    with pytest.raises(ValidationError):
        Checkpoint(**kwargs)


# ---------------------------------------------------------------------------
# cf-prefix detection -- run_id minted as ``f"cf-{uuid4()}"`` in
# stargraph.graph.run.GraphRun.counterfactual()
# ---------------------------------------------------------------------------


def test_cf_prefix_distinguishes_counterfactual_run() -> None:
    original = Checkpoint(**_valid_checkpoint_kwargs())
    cf_kwargs = _valid_checkpoint_kwargs()
    cf_kwargs["run_id"] = "cf-deadbeef-1234-1234-1234-deadbeefdead"
    cf_kwargs["parent_run_id"] = original.run_id
    cf = Checkpoint(**cf_kwargs)

    assert not original.run_id.startswith("cf-")
    assert cf.run_id.startswith("cf-")
    # Counterfactual checkpoints carry parent_run_id; originals do not.
    assert original.parent_run_id is None
    assert cf.parent_run_id == original.run_id


def test_cf_prefix_detection_on_run_summary() -> None:
    now = datetime.now(UTC)
    original = RunSummary(
        run_id="run-orig",
        graph_hash="a" * 64,
        started_at=now,
        last_step_at=now,
        status="done",
        parent_run_id=None,
    )
    cf = RunSummary(
        run_id="cf-feedface-0000-0000-0000-feedfacefeed",
        graph_hash="d" * 64,
        started_at=now,
        last_step_at=now,
        status="running",
        parent_run_id=original.run_id,
    )
    assert not original.run_id.startswith("cf-")
    assert cf.run_id.startswith("cf-")
    assert cf.parent_run_id == original.run_id


# ---------------------------------------------------------------------------
# RunSummary -- 6 core fields + 2 optional failure-diagnostic fields, status enum
# ---------------------------------------------------------------------------


def test_run_summary_has_expected_fields() -> None:
    # Six core inspect/CLI fields plus the two optional terminal-failure
    # diagnostics (#68): ``error_class`` / ``error_cause`` default to ``None``
    # so existing constructors and persisted rows stay valid.
    assert set(RunSummary.model_fields.keys()) == {
        "run_id",
        "graph_hash",
        "started_at",
        "last_step_at",
        "status",
        "parent_run_id",
        "error_class",
        "error_cause",
    }


def test_run_summary_failure_fields_default_to_none() -> None:
    now = datetime.now(UTC)
    rs = RunSummary(
        run_id="r",
        graph_hash="a" * 64,
        started_at=now,
        last_step_at=now,
        status="done",
        parent_run_id=None,
    )
    assert rs.error_class is None
    assert rs.error_cause is None


def test_run_summary_status_enum_rejects_unknown() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValidationError):
        RunSummary(
            run_id="r",
            graph_hash="a" * 64,
            started_at=now,
            last_step_at=now,
            status="bogus",  # type: ignore[arg-type]
            parent_run_id=None,
        )


@pytest.mark.parametrize("status", ["running", "done", "failed", "paused"])
def test_run_summary_accepts_each_status(status: str) -> None:
    now = datetime.now(UTC)
    rs = RunSummary(
        run_id="r",
        graph_hash="a" * 64,
        started_at=now,
        last_step_at=now,
        status=status,  # type: ignore[arg-type]
        parent_run_id=None,
    )
    assert rs.status == status


# ---------------------------------------------------------------------------
# Checkpointer Protocol -- enforces 5 methods (design §3.2.1)
# ---------------------------------------------------------------------------

_CHECKPOINTER_METHODS = {
    "bootstrap",
    "write",
    "read_latest",
    "read_at_step",
    "list_runs",
}


def test_checkpointer_is_a_protocol() -> None:
    # Protocol membership is detected by the ``_is_protocol`` marker the
    # ``typing`` module sets on Protocol subclasses.
    assert issubclass(Checkpointer, Protocol)
    assert getattr(Checkpointer, "_is_protocol", False) is True


def test_checkpointer_declares_exactly_five_methods() -> None:
    declared = {
        name
        for name, _member in inspect.getmembers(Checkpointer, inspect.isfunction)
        if not name.startswith("_")
    }
    assert declared == _CHECKPOINTER_METHODS


def test_checkpointer_methods_are_async() -> None:
    for name in _CHECKPOINTER_METHODS:
        member = getattr(Checkpointer, name)
        assert inspect.iscoroutinefunction(member), f"Checkpointer.{name} must be `async def`"


def test_checkpointer_method_signatures() -> None:
    sigs = {name: inspect.signature(getattr(Checkpointer, name)) for name in _CHECKPOINTER_METHODS}

    # bootstrap(self) -> None
    assert list(sigs["bootstrap"].parameters) == ["self"]

    # write(self, checkpoint: Checkpoint) -> None
    assert list(sigs["write"].parameters) == ["self", "checkpoint"]

    # read_latest(self, run_id: str) -> Checkpoint | None
    assert list(sigs["read_latest"].parameters) == ["self", "run_id"]

    # read_at_step(self, run_id: str, step: int) -> Checkpoint | None
    assert list(sigs["read_at_step"].parameters) == ["self", "run_id", "step"]

    # list_runs(self, *, since=None, limit=100) -> list[RunSummary]
    list_runs_params = sigs["list_runs"].parameters
    assert "since" in list_runs_params
    assert "limit" in list_runs_params
    assert list_runs_params["since"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list_runs_params["limit"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list_runs_params["limit"].default == 100


def test_minimal_concrete_implementation_satisfies_protocol() -> None:
    """A class implementing all 5 async methods is structurally a Checkpointer."""

    class _Impl:
        async def bootstrap(self) -> None:
            return None

        async def write(self, checkpoint: Checkpoint) -> None:
            return None

        async def read_latest(self, run_id: str) -> Checkpoint | None:
            return None

        async def read_at_step(self, run_id: str, step: int) -> Checkpoint | None:
            return None

        async def list_runs(
            self, *, since: datetime | None = None, limit: int = 100
        ) -> list[RunSummary]:
            return []

    impl = _Impl()
    # Static structural check: every Protocol method exists on impl and is async.
    for name in _CHECKPOINTER_METHODS:
        assert hasattr(impl, name), f"missing method: {name}"
        assert inspect.iscoroutinefunction(getattr(impl, name))


def test_incomplete_implementation_missing_methods_is_detectable() -> None:
    """A class missing one of the 5 methods can be detected as non-conforming."""

    class _Partial:
        async def bootstrap(self) -> None:
            return None

        async def write(self, checkpoint: Checkpoint) -> None:
            return None

        # Missing: read_latest, read_at_step, list_runs

    impl = _Partial()
    missing = {name for name in _CHECKPOINTER_METHODS if not hasattr(impl, name)}
    assert missing == {"read_latest", "read_at_step", "list_runs"}
