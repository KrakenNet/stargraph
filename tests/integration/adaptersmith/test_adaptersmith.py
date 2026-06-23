# SPDX-License-Identifier: Apache-2.0
"""Adaptersmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real
in every test — these assert the "always works" contract end-to-end: a bogus
passing test cannot get an adapter that skips the capability gate, returns
off-schema, or fails to sanitize recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.adaptersmith import _ledger, gate
from stargraph.skills.adaptersmith.nodes.build import Build
from stargraph.skills.adaptersmith.nodes.recall import Recall
from stargraph.skills.adaptersmith.nodes.record import RecordBuild
from stargraph.skills.adaptersmith.nodes.triage import TriageGate
from stargraph.skills.adaptersmith.seeds import SEEDS
from stargraph.skills.adaptersmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

# The known-good adapter is the gate-verified MCP seam seed.
GOOD_ADAPTER = str(SEEDS[0]["adapter_source"])
GOOD_ADAPTER_TEST = str(SEEDS[0]["test_source"])
GOOD_GEN: dict[str, Any] = {
    "adapter_name": "mcp",
    "namespace": "mcp",
    "fixture": {},
    "adapter_source": GOOD_ADAPTER,
    "test_source": GOOD_ADAPTER_TEST,
}

# Anti-cheat: structurally valid (async bind + call_tool, translates, validates
# input, validates output, sanitizes) BUT invokes the session BEFORE the
# capability gate — so a refused call still touches the session. Its own trivial
# test passes; the contract tier's case (e) catches it (session.calls != []).
CHEAT_ADAPTER = """\
from __future__ import annotations

import html
import re
from typing import Any, Protocol, runtime_checkable

from jsonschema import Draft202012Validator

from stargraph.errors import CapabilityError, IRValidationError
from stargraph.ir import ToolSpec
from stargraph.security import Capabilities
from stargraph.tools.spec import ReplayPolicy, SideEffects

_SESSION_CAPS: dict[int, Capabilities] = {}
_CONTROL = re.compile(r"[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f-\\x9f]")
_PERMS: dict[str, list[str]] = {"read-secret": ["fs.read:/secrets/*"]}


@runtime_checkable
class _S(Protocol):
    async def initialize(self) -> Any: ...
    async def list_tools(self) -> Any: ...
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


def _san(v: object) -> object:
    if isinstance(v, str):
        return html.escape(_CONTROL.sub("", v), quote=True)
    if isinstance(v, dict):
        return {k: _san(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_san(x) for x in v]
    return v


def _attr(t: object, n: str) -> Any:
    return t.get(n) if isinstance(t, dict) else getattr(t, n, None)


def _translate(t: object) -> ToolSpec:
    name = _attr(t, "name")
    return ToolSpec(
        name=str(name),
        namespace="mcp",
        version="1",
        description=str(_attr(t, "description") or ""),
        input_schema={str(k): v for k, v in (_attr(t, "inputSchema") or {}).items()},
        output_schema={str(k): v for k, v in (_attr(t, "outputSchema") or {}).items()},
        side_effects=SideEffects.external,
        replay_policy=ReplayPolicy.must_stub,
        permissions=list(_PERMS.get(str(name), [])),
    )


def _val(payload: dict[str, Any], schema: dict[str, object], kind: str) -> None:
    errs = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: list(e.path))
    if errs:
        raise IRValidationError(f"{kind} schema validation failed: {errs[0].message}")


async def bind(server: object, *, capabilities: Capabilities) -> list[ToolSpec]:
    _SESSION_CAPS[id(server)] = capabilities
    await server.initialize()
    result = await server.list_tools()
    tools = getattr(result, "tools", [])
    return [_translate(t) for t in tools]


async def call_tool(
    session: _S,
    tool: ToolSpec,
    arguments: dict[str, Any],
    *,
    capabilities: Capabilities | None = None,
) -> dict[str, Any]:
    # CHEAT: validate input + invoke the session FIRST, gate AFTER.
    _val(arguments, tool.input_schema, "input")
    raw = await session.call_tool(tool.name, dict(arguments))
    caps = capabilities if capabilities is not None else _SESSION_CAPS.get(id(session))
    if caps is not None and not caps.check(tool):
        raise CapabilityError("not granted", tool_id=tool.name)
    payload = getattr(raw, "structuredContent", raw)
    if not isinstance(payload, dict):
        raise IRValidationError("non-object payload")
    _val(payload, tool.output_schema, "output")
    out = _san(payload)
    assert isinstance(out, dict)
    return out
"""
# The cheat's OWN test only checks the happy path — it never asserts the
# session was untouched on a refused call, so it passes trivially.
CHEAT_TEST = """\
import asyncio
from dataclasses import dataclass, field
from typing import Any

from adapter import bind, call_tool

from stargraph.security import Capabilities, CapabilityClaim


@dataclass(frozen=True)
class StubTool:
    name: str
    description: str
    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]


@dataclass
class _LR:
    tools: list = field(default_factory=list)


@dataclass
class _CR:
    structuredContent: dict[str, Any]


class Sess:
    def __init__(self, tools, script):
        self._t = list(tools)
        self._s = dict(script)
        self.initialized = False
        self.calls = []

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return _LR(tools=list(self._t))

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return _CR(structuredContent=dict(self._s[name]))


def _echo():
    return StubTool(
        name="echo",
        description="e",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        outputSchema={
            "type": "object",
            "properties": {"echoed": {"type": "string"}},
            "required": ["echoed"],
            "additionalProperties": False,
        },
    )


def test_happy_path():
    sess = Sess([_echo()], {"echo": {"echoed": "hi"}})
    caps = Capabilities(
        default_deny=False,
        granted={CapabilityClaim(name="fs.read", scope="/secrets/*")},
    )
    echo = asyncio.run(bind(sess, capabilities=caps))[0]
    out = asyncio.run(call_tool(sess, echo, {"text": "hi"}))
    assert out["echoed"] == "hi"
"""

# Syntax error → fails every tier fast; the repair loop can never fix a constant stub.
BAD_GEN: dict[str, Any] = {
    "adapter_name": "broken",
    "namespace": "mcp",
    "fixture": {},
    "adapter_source": "async def bind(:\n    pass\n",
    "test_source": "def test_x():\n    assert True\n",
}


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable floor)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_adapter(tmp_path: Path) -> None:
    files = {gate.ADAPTER_FILE: GOOD_ADAPTER, gate.TEST_FILE: GOOD_ADAPTER_TEST}
    results = gate.run_full_gate(tmp_path / "g", files)
    assert gate.all_passed(results)


async def test_gate_contract_catches_ungated_capability(tmp_path: Path) -> None:
    """The keystone: an adapter that touches the session before gating capability
    fails the contract tier even though its own test passes trivially."""
    files = {gate.ADAPTER_FILE: CHEAT_ADAPTER, gate.TEST_FILE: CHEAT_TEST}
    results = gate.run_full_gate(tmp_path / "g", files)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed  # a trivially-passing test did NOT save it
    assert any("cap-gate" in str(f.get("msg", "")) for f in contract.findings)
    assert not gate.all_passed(results)


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="mcp adapter"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["adapter_name"] == "mcp"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="mcp adapter", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["adapter_name"] == "mcp"
    assert pairs[0]["namespace"] == "mcp"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"
    assert final.landed_path
    assert (out_dir / "mcp.py").exists()
    assert (out_dir / "test_mcp.py").exists()


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
        brief="an mcp adapter that gates call_tool",
        failed_kind="contract",
        finding="capability gate fired after the session was invoked",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated cron trigger thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="mcp adapter call_tool gate"), CTX)
    assert out["recalled_lessons"]
    assert "capability gate" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
