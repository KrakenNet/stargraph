# SPDX-License-Identifier: Apache-2.0
"""The adapter smith verify gate — the "always works" contract for adapters.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *adapter* contract: import the generated module, assert it
exposes module-level ``async`` ``bind`` + ``call_tool``, then EXERCISE both against
a self-contained in-memory MCP session stub and real ``stargraph.security``
literals — translation, input/output schema validation, output sanitization, and
the capability gate (which must fire BEFORE the session is touched). The fixed
artifact filenames are ``adapter.py`` + ``test_adapter.py``.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` - tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox). The
contract driver embeds its OWN stub + capability literals; it never trusts the
candidate's test or fixture, so a trivially-passing generated test cannot land an
adapter that skips the capability gate, returns off-schema, or fails to sanitize.
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
    "ADAPTER_FILE",
    "TEST_FILE",
    "VerifierResult",
    "all_passed",
    "run_full_gate",
    "verify_sources",
]

ADAPTER_FILE = "adapter.py"
TEST_FILE = "test_adapter.py"

# Driver executed in a subprocess: imports the candidate adapter module, asserts
# the two async functions exist, and exercises them through the full FR-25 call
# path against an EMBEDDED in-memory session stub + real Capabilities literals.
# Each behaviour is asserted observably so a trivially-passing generated test
# cannot land a broken adapter. Dependency-free beyond stargraph (always present
# wherever the gate runs). A FRESH stub session is used per case because the
# adapter keys per-session capabilities by ``id(session)``.
_CONTRACT_DRIVER = '''\
import asyncio, importlib.util, inspect, json, sys
from dataclasses import dataclass, field
from typing import Any

from stargraph.errors import CapabilityError
from stargraph.ir import ToolSpec
from stargraph.security import Capabilities, CapabilityClaim


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


# --- embedded MCP tool catalogue (real Draft-2020-12 schemas) ----------------
@dataclass(frozen=True)
class StubTool:
    name: str
    description: str
    inputSchema: dict[str, Any]
    outputSchema: dict[str, Any]


@dataclass
class _ListResult:
    tools: list = field(default_factory=list)


@dataclass
class _CallResult:
    structuredContent: dict[str, Any]


def _echo_tool():
    return StubTool(
        name="echo",
        description="Echo the input text back.",
        inputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        outputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"echoed": {"type": "string"}},
            "required": ["echoed"],
            "additionalProperties": False,
        },
    )


def _read_secret_tool():
    return StubTool(
        name="read-secret",
        description="Read a secret file.",
        inputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
        outputSchema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {"contents": {"type": "string"}},
            "required": ["contents"],
            "additionalProperties": False,
        },
    )


class StubSession:
    """In-memory ``ClientSession`` look-alike. ``script`` maps name -> response."""

    def __init__(self, tools, script):
        self._tools = list(tools)
        self._script = dict(script)
        self.initialized = False
        self.calls = []

    async def initialize(self):
        self.initialized = True

    async def list_tools(self):
        return _ListResult(tools=list(self._tools))

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if name not in self._script:
            raise KeyError(name)
        return _CallResult(structuredContent=dict(self._script[name]))


# real capability literals (the gate does not trust the candidate's test)
_OPEN_CAPS = Capabilities(
    default_deny=False,
    granted={CapabilityClaim(name="fs.read", scope="/secrets/*")},
)
_EMPTY_CAPS = Capabilities()


def _spec(specs, name):
    for s in specs:
        if s.name == name:
            return s
    _fail("translate: missing tool spec " + name)


# --- import the candidate module ---------------------------------------------
spec_mod = importlib.util.spec_from_file_location("candidate_adapter", "adapter.py")
mod = importlib.util.module_from_spec(spec_mod)
try:
    spec_mod.loader.exec_module(mod)
except Exception as e:
    _fail(f"import failed: {type(e).__name__}: {e}")

bind = getattr(mod, "bind", None)
call_tool = getattr(mod, "call_tool", None)
if not callable(bind) or not callable(call_tool):
    _fail("adapter.py must define module-level callables `bind` and `call_tool`")
if not inspect.iscoroutinefunction(bind):
    _fail("`bind` must be an async (coroutine) function")
if not inspect.iscoroutinefunction(call_tool):
    _fail("`call_tool` must be an async (coroutine) function")


# --- (a) TRANSLATE -----------------------------------------------------------
sess = StubSession([_echo_tool(), _read_secret_tool()], {"echo": {"echoed": "hi"}})
try:
    specs = asyncio.run(bind(sess, capabilities=_OPEN_CAPS))
except Exception as e:
    _fail(f"translate: bind raised: {type(e).__name__}: {e}")
if not isinstance(specs, list) or len(specs) != 2:
    _fail("translate: bind must return a list of exactly 2 ToolSpec")
if not all(isinstance(s, ToolSpec) for s in specs):
    _fail("translate: bind must return ToolSpec instances")
if {s.name for s in specs} != {"echo", "read-secret"}:
    _fail("translate: tool names must be {echo, read-secret}")
echo_spec = _spec(specs, "echo")
read_secret_spec = _spec(specs, "read-secret")
if echo_spec.namespace != "mcp":
    _fail("translate: echo namespace must be 'mcp'")
if echo_spec.input_schema.get("type") != "object":
    _fail("translate: echo input_schema must be an object schema")
_props = echo_spec.input_schema.get("properties")
if not isinstance(_props, dict) or "text" not in _props:
    _fail("translate: echo input_schema must carry a 'text' property")
if sess.initialized is not True:
    _fail("translate: session.initialize() must run before list_tools()")


# --- (b) INPUT-VALIDATE ------------------------------------------------------
sess = StubSession([_echo_tool()], {"echo": {"echoed": "ok"}})
asyncio.run(bind(sess, capabilities=_OPEN_CAPS))
try:
    asyncio.run(call_tool(sess, echo_spec, {"text": 123}))
    _fail("input-validate: call_tool must raise on input violating input_schema")
except Exception as e:
    if isinstance(e, SystemExit):
        raise
    pass


# --- (c) OUTPUT-VALIDATE -----------------------------------------------------
sess = StubSession([_echo_tool()], {"echo": {"wrong_key": "x"}})
asyncio.run(bind(sess, capabilities=_OPEN_CAPS))
try:
    asyncio.run(call_tool(sess, echo_spec, {"text": "hi"}))
    _fail("output-validate: call_tool must raise on output violating output_schema")
except Exception as e:
    if isinstance(e, SystemExit):
        raise
    pass


# --- (d) SANITIZE ------------------------------------------------------------
sess = StubSession(
    [_echo_tool()],
    {"echo": {"echoed": "<script>x</script>\\x07a\\x1b[31m"}},
)
asyncio.run(bind(sess, capabilities=_OPEN_CAPS))
try:
    r = asyncio.run(call_tool(sess, echo_spec, {"text": "hi"}))
except Exception as e:
    _fail(f"sanitize: call_tool raised on a valid call: {type(e).__name__}: {e}")
if not isinstance(r, dict) or "echoed" not in r:
    _fail("sanitize: call_tool must return the validated object")
cleaned = r["echoed"]
if "<script>" in cleaned:
    _fail("sanitize: raw <script> survived")
if not ("&lt;" in cleaned or "&#x3c;" in cleaned.lower()):
    _fail("sanitize: angle brackets were not HTML-escaped")
if chr(7) in cleaned:
    _fail("sanitize: BEL control char survived")
if chr(27) in cleaned:
    _fail("sanitize: ESC control char survived")


# --- (e) CAPABILITY GATE (anti-cheat keystone) -------------------------------
# Bind a fresh recording session under empty caps so id(session) keys it, then a
# refused call must raise BEFORE the session is ever invoked.
gate_sess = StubSession([_read_secret_tool()], {"read-secret": {"contents": "TOPSECRET"}})
asyncio.run(bind(gate_sess, capabilities=_EMPTY_CAPS))
try:
    asyncio.run(call_tool(gate_sess, read_secret_spec, {"path": "/secrets/k"}))
    _fail("cap-gate: call_tool must raise when the required permission is not granted")
except CapabilityError:
    pass
except SystemExit:
    raise
except Exception as e:
    _fail(f"cap-gate: expected CapabilityError, got {type(e).__name__}: {e}")
if gate_sess.calls != []:
    _fail("cap-gate: the capability gate must fire BEFORE the session is invoked")


print(json.dumps({"ok": True, "names": [s.name for s in specs]}))
'''


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    fixture: dict[str, Any] | None = None,
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    Shared verbatim by the build node and the offline optimizer's metric. The
    contract tier imports the adapter and exercises the full MCP call path against
    an embedded session stub + capability literals (see ``_CONTRACT_DRIVER``); the
    ``fixture`` is accepted for signature parity but is advisory only (the driver
    embeds its own payload).
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(_CONTRACT_DRIVER, {}),
        test_file=TEST_FILE,
    )


def verify_sources(
    adapter_source: str,
    test_source: str,
    *,
    fixture: dict[str, Any] | None = None,
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on raw source in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``adaptersmith make``, the doctor preflight, and seed verification.
    Returns ``(passed, results)``.
    """
    files = {ADAPTER_FILE: adapter_source, TEST_FILE: test_source}
    with tempfile.TemporaryDirectory(prefix="adaptersmith-verify-") as d:
        results = run_full_gate(Path(d), files, fixture=fixture)
    return all_passed(results), results
