# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import TYPE_CHECKING

from pydantic import BaseModel
from rich.console import Console

from stargraph.checkpoint.protocol import RunSummary
from stargraph.cli._progress import ProgressStats
from stargraph.cli._summary import SummaryRenderer

if TYPE_CHECKING:
    from pathlib import Path


class _VerifierResult(BaseModel):
    kind: str
    passed: bool
    duration_ms: int = 0
    findings: list[dict[str, object]] = []


class _State(BaseModel):
    artifact_files: dict[str, str] = {}
    verifier_results: list[_VerifierResult] = []
    misc: int = 0
    flag: bool = False


def _summary(status: str = "done", duration_ms: int = 1234) -> RunSummary:
    started = datetime.now(UTC)
    return RunSummary(
        run_id="r-test",
        graph_hash="hash-abc",
        started_at=started,
        last_step_at=started + timedelta(milliseconds=duration_ms),
        status=status,  # type: ignore[arg-type]
        parent_run_id=None,
    )


def _stats() -> ProgressStats:
    return ProgressStats(
        step_count=3,
        llm_call_count=1,
        total_tool_tokens=42,
        node_durations_ms={"a": 12, "b": 4200},
    )


def test_human_summary_writes_artifacts_to_disk(tmp_path: Path) -> None:
    out = StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    renderer = SummaryRenderer(console)
    state = _State(
        artifact_files={"a.py": "x = 1\n", "tests/t.py": "pass\n"},
        misc=42,
    )
    renderer.render(
        summary=_summary(),
        final_state=state,
        stats=_stats(),
        artifacts_dir=tmp_path,
        run_id="r-test",
        checkpoint=tmp_path / "ck.sqlite",
    )

    assert (tmp_path / "a.py").read_text() == "x = 1\n"
    assert (tmp_path / "tests" / "t.py").read_text() == "pass\n"
    text = out.getvalue()
    assert "done" in text
    assert "a.py" in text
    assert "1.2s" in text or "1234ms" in text
    assert "misc" in text  # state field listed


def test_verifier_results_rendered(tmp_path: Path) -> None:
    out = StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    renderer = SummaryRenderer(console)
    state = _State(
        verifier_results=[
            _VerifierResult(kind="static", passed=True, duration_ms=53),
            _VerifierResult(kind="tests", passed=True, duration_ms=3300),
            _VerifierResult(
                kind="smoke", passed=False, duration_ms=2400, findings=[{"msg": "fixtures missing"}]
            ),
        ],
    )
    renderer.render(
        summary=_summary(),
        final_state=state,
        stats=_stats(),
        artifacts_dir=tmp_path,
        run_id="r-test",
        checkpoint=tmp_path / "ck.sqlite",
    )
    text = out.getvalue()
    assert "static" in text and "tests" in text and "smoke" in text
    assert "✓" in text and "✗" in text


def test_json_mode_emits_machine_readable(tmp_path: Path) -> None:
    out = StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    renderer = SummaryRenderer(console, json_mode=True)
    state = _State(
        artifact_files={"a.py": "x = 1\n"},
        verifier_results=[_VerifierResult(kind="static", passed=True)],
        misc=42,
    )
    renderer.render(
        summary=_summary(duration_ms=500),
        final_state=state,
        stats=_stats(),
        artifacts_dir=tmp_path,
        run_id="r-test",
        checkpoint=tmp_path / "ck.sqlite",
    )

    payload = json.loads(out.getvalue())
    assert payload["status"] == "done"
    assert payload["run_id"] == "r-test"
    assert payload["duration_ms"] == 500
    assert payload["step_count"] == 3
    assert payload["llm_call_count"] == 1
    assert payload["artifacts"] == ["a.py"]
    assert payload["verifier_results"] == [
        {"kind": "static", "passed": True, "duration_ms": 0, "findings": []}
    ]
    # state_summary holds non-artifact, non-verifier fields with non-default values
    assert payload["state_summary"]["misc"] == 42
    # Also writes artifacts to disk in JSON mode
    assert (tmp_path / "a.py").read_text() == "x = 1\n"


def test_suppress_writes_artifacts_no_console_output(tmp_path: Path) -> None:
    out = StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    renderer = SummaryRenderer(console, suppress=True)
    state = _State(artifact_files={"a.py": "1\n"})
    renderer.render(
        summary=_summary(),
        final_state=state,
        stats=_stats(),
        artifacts_dir=tmp_path,
        run_id="r-test",
        checkpoint=tmp_path / "ck.sqlite",
    )
    assert out.getvalue() == ""
    assert (tmp_path / "a.py").read_text() == "1\n"


def test_failed_status_renders_with_findings(tmp_path: Path) -> None:
    out = StringIO()
    console = Console(file=out, force_terminal=False, width=120)
    renderer = SummaryRenderer(console)
    state = _State()
    renderer.render(
        summary=_summary(status="failed"),
        final_state=state,
        stats=_stats(),
        artifacts_dir=tmp_path,
        run_id="r-test",
        checkpoint=tmp_path / "ck.sqlite",
    )
    text = out.getvalue()
    assert "failed" in text.lower()
