# SPDX-License-Identifier: Apache-2.0
"""Golden tests for the runnable graphs under examples/.

Every example must run end-to-end via `stargraph run` and reach
status=done. This is what keeps the examples (and the getting-started
docs that reference them) from rotting: if an example breaks, CI fails.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from typer.testing import CliRunner

from stargraph.cli import app

EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples"

# Examples that demonstrate LLM nodes: excluded from the no-LM golden run and
# driven below under a scripted DummyLM instead (CliRunner shares the process,
# so a dspy.context around invoke() is the stub seam). Live runs pass
# --lm-url/--lm-model.
_LM_EXAMPLES = {"research-bot.yaml", "sglang-qa.yaml"}
EXAMPLE_GRAPHS = sorted(p for p in EXAMPLES_DIR.glob("*.yaml") if p.name not in _LM_EXAMPLES)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_examples_dir_is_not_empty() -> None:
    assert EXAMPLE_GRAPHS, f"no example graphs found under {EXAMPLES_DIR}"


@pytest.mark.integration
@pytest.mark.parametrize("graph", EXAMPLE_GRAPHS, ids=lambda p: p.name)
def test_example_runs_to_done(graph: Path, runner: CliRunner, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "run",
            str(graph),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--inputs",
            "message=hello",
            "--quiet",
            "--summary-json",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON summary in output for {graph.name}: {result.stdout!r}"
    payload = json.loads(lines[-1])
    assert payload["status"] == "done", f"{graph.name} did not reach done: {payload}"


@pytest.mark.integration
def test_research_bot_loop_with_scripted_lm(runner: CliRunner, tmp_path: Path) -> None:
    """research-bot.yaml (authoring format) runs one fail->refine->pass loop.

    The DummyLM script finishes the ReAct step without a tool call (no
    network in CI); the judge fails the first draft, the brief template
    re-injects the rationale, and the second draft passes -- proving the
    authored verdict routes fire live through ``stargraph run``.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]
    from dspy.utils import DummyLM  # pyright: ignore[reportMissingTypeStubs]

    lm = DummyLM(
        [
            # round 1: react finishes immediately, judge fails the draft
            {"next_thought": "I know this.", "next_tool_name": "finish", "next_tool_args": {}},
            {"reasoning": "first try", "answer": "draft-1"},
            {"reasoning": "too vague, name the engine", "verdict": "fail", "score": "0.2"},
            # round 2: refined draft passes
            {"next_thought": "Refined.", "next_tool_name": "finish", "next_tool_args": {}},
            {"reasoning": "second try", "answer": "draft-2 names CLIPS"},
            {"reasoning": "specific now", "verdict": "pass", "score": "0.9"},
        ]
    )
    with dspy.context(lm=lm):  # pyright: ignore[reportUnknownMemberType]
        result = runner.invoke(
            app,
            [
                "run",
                str(EXAMPLES_DIR / "research-bot.yaml"),
                "--checkpoint",
                str(tmp_path / "ck.sqlite"),
                "--inputs",
                "question=what engine routes stargraph?",
                "--quiet",
                "--summary-json",
            ],
        )

    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON summary in output: {result.stdout!r}"
    payload = json.loads(lines[-1])
    assert payload["status"] == "done"
    state = payload["state_summary"]
    assert state["verdict"] == "pass"
    assert state["answer"] == "draft-2 names CLIPS"  # second round's answer won
    # The judge's round-1 rationale was re-injected into the round-2 brief.
    assert "too vague, name the engine" in state["brief"]


# --------------------------------------------------------------------------- #
# sglang-qa.yaml -- the graph carries its own endpoint via the `lm:` block     #
# --------------------------------------------------------------------------- #

_OPENAI_STUB = textwrap.dedent(
    '''
    """OpenAI-compatible stub: GET /v1/models + POST /v1/chat/completions.

    Speaks just enough for the attach probe to recognise the model and for
    DSPy's chat adapter to parse one field back out.
    """
    import json
    import sys
    from http.server import BaseHTTPRequestHandler, HTTPServer

    MODEL, PORT, ANSWER, HITS = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]


    class Handler(BaseHTTPRequestHandler):
        def _send(self, payload):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._send({"data": [{"id": MODEL}]})

        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("content-length", 0) or 0))
            with open(HITS, "a", encoding="utf-8") as handle:
                handle.write(self.path + "\\n")
            content = f"[[ ## answer ## ]]\\n{ANSWER}\\n\\n[[ ## completed ## ]]"
            self._send(
                {
                    "id": "chatcmpl-stub",
                    "object": "chat.completion",
                    "model": MODEL,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": content},
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )

        def log_message(self, *_args):  # keep the captured log quiet
            return


    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
    '''
)

_STUB_ANSWER = "Fathom, a CLIPS rules engine, decides every transition."


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _declared_model(graph: Path) -> str:
    """The ``lm.model`` the example declares -- the stub must report exactly it.

    Read from the YAML rather than duplicated here so a rename of the model in
    the example cannot leave this test attaching to something else.
    """
    for line in graph.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("model:"):
            return stripped.split(":", 1)[1].split("#")[0].strip()
    raise AssertionError(f"no lm.model declared in {graph}")


@pytest.fixture
def openai_stub(tmp_path: Path) -> object:
    """Serve one model id on a free loopback port; yield ``(port, model, hits)``.

    SGLang is GPU-only, so the example's *spawn* path cannot run in CI. Its
    *attach* path can: a server already serving the requested model is left
    alone and used as-is, which is the production branch taken here -- no
    launch argv is stubbed and no engine code is monkeypatched.

    ``hits`` is the file the stub appends to on every completion request. It
    exists because DSPy caches responses on disk across processes, keyed by
    model id + prompt + params: without proof the stub was reached, this test
    passes on a cache entry written by an earlier run (or writes one that a
    later *real* run against the same model and question serves instead of
    calling the GPU). The cache is disabled here for the same reason.
    """
    import dspy  # pyright: ignore[reportMissingTypeStubs]

    dspy.configure_cache(  # pyright: ignore[reportUnknownMemberType]
        enable_disk_cache=False, enable_memory_cache=False
    )
    graph = EXAMPLES_DIR / "sglang-qa.yaml"
    model = _declared_model(graph)
    port = _free_port()
    hits = tmp_path / "stub-hits.txt"
    script = tmp_path / "openai_stub.py"
    script.write_text(_OPENAI_STUB, encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(script), model, str(port), _STUB_ANSWER, str(hits)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/v1/models", timeout=1
                ) as response:
                    if response.status == 200:
                        break
            except (urllib.error.URLError, OSError, TimeoutError):
                time.sleep(0.05)
        else:
            raise AssertionError("stub server never became ready")
        yield port, model, hits
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.mark.integration
def test_sglang_qa_attaches_to_a_running_server(
    openai_stub: tuple[int, str, Path], runner: CliRunner, tmp_path: Path
) -> None:
    """sglang-qa.yaml binds its LM from the graph, not from --lm-url/--lm-model.

    ``--sglang-port`` re-points the declared block field-by-field, which is how
    an operator aims the example at a server they already have. Reaching
    ``status=done`` with the stub's answer in state proves the whole chain:
    the block lowered to an ``SGLangServer``, the endpoint resolved by
    attaching, and the derived base URL + model configured the DSPy LM that
    the ``ask`` node ran against.
    """
    port, _model, hits = openai_stub
    result = runner.invoke(
        app,
        [
            "run",
            str(EXAMPLES_DIR / "sglang-qa.yaml"),
            "--sglang-port",
            str(port),
            "--checkpoint",
            str(tmp_path / "ck.sqlite"),
            "--inputs",
            "question=what routes stargraph?",
            "--quiet",
            "--summary-json",
        ],
    )

    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.stdout.splitlines() if ln.strip().startswith("{")]
    assert lines, f"no JSON summary in output: {result.stdout!r}"
    payload = json.loads(lines[-1])
    assert payload["status"] == "done"
    assert payload["state_summary"]["answer"] == _STUB_ANSWER
    # The answer proves nothing on its own -- DSPy would serve it from disk
    # cache with no server involved at all.
    assert hits.exists() and hits.read_text().strip(), "the stub was never called"
    # ... and the summary must say the same thing the stub's log does.
    assert payload["llm_call_count"] == 1
    assert payload["llm_cache_hits"] == 0
