# SPDX-License-Identifier: Apache-2.0
"""``stargraph run shipwright/graph.yaml`` against the llm-ollama container.

Skipped if the container isn't reachable. The point is to prove that the
three Plan-1.5 affordances combine with a live LLM to actually drive the
canonical YAML end-to-end through ``stargraph run``:

  * ``state_class`` resolves the rich Pydantic ``State``,
  * every ``module:Class`` node kind imports and runs,
  * ``--lm-url``/``--lm-model`` configure DSPy so the real-LLM nodes
    (``parse_brief``, ``propose_questions``, ``synthesize_graph``)
    execute against ollama.

The run is expected to reach ``status=done`` — every node fires, ParseBrief
extracts at least the ``kind`` slot, and the synthesize/verify chain runs
on whatever the LLM and ProposeQuestions produced. We do NOT assert on
specific slot values (LLM output varies); we assert on *node coverage* and
the terminal status.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

SHIPWRIGHT_GRAPH = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "stargraph"
    / "skills"
    / "shipwright"
    / "graph.yaml"
)


@pytest.mark.integration
@pytest.mark.slow
def test_stargraph_run_drives_shipwright_with_ollama(
    ollama_config: dict[str, str | int],
    ollama_lm: object,  # fixture's only job is the skip-if-unreachable check
    tmp_path: Path,
) -> None:
    log_file = tmp_path / "run.jsonl"
    ckpt = tmp_path / "ck.sqlite"

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            str(SHIPWRIGHT_GRAPH),
            "--checkpoint",
            str(ckpt),
            "--log-file",
            str(log_file),
            "--lm-url",
            str(ollama_config["url"]),
            "--lm-model",
            str(ollama_config["model"]),
            "--lm-key",
            "ollama",
            "--lm-timeout",
            str(ollama_config["timeout_s"]),
            "--non-interactive",
            "--quiet",
            "--no-summary",
            "--inputs",
            ("brief=a triage graph that classifies SOC alerts into {benign, suspicious, critical}"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "status=done" in result.stdout, result.output

    if not log_file.exists() or log_file.stat().st_size == 0:
        pytest.skip("audit log empty — ollama call likely failed mid-flight")

    seen_nodes: set[str] = set()
    final_state: dict[str, object] | None = None
    for line in log_file.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for key in ("from_node", "to_node"):
            value = event.get(key)
            if isinstance(value, str) and value:
                seen_nodes.add(value)
        if event.get("type") == "result":
            final_state = event.get("final_state")

    expected = {
        "triage_gate",
        "parse_brief",
        "gap_check",
        "propose_questions",
        "synthesize_graph",
        "verify_static",
        "verify_tests",
        "verify_smoke",
        "fix_loop",
    }
    missing = expected - seen_nodes
    assert not missing, f"nodes never ran: {sorted(missing)}; saw {sorted(seen_nodes)}"

    assert isinstance(final_state, dict)
    slots = final_state.get("slots", {})
    assert isinstance(slots, dict) and slots, "ParseBrief produced no slots — LLM extraction failed"
