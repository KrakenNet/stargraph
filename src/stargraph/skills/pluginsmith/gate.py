# SPDX-License-Identifier: Apache-2.0
"""The plugin smith verify gate — the "always works" contract for plugins.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *plugin* contract: write the bundle (``plugin.py`` +
``test_plugin.py``), then in a subprocess register ``plugin.py`` on a FRESH,
ISOLATED pluggy ``PluginManager`` (the Stargraph hookspecs only — no entry-point
discovery, so no other installed plugin can shadow the verdict) and drive it FOR
REAL: assert ``register_tools`` advertises the declared ``(namespace, tool_name)``,
call the ``@tool`` callable and check its output against the fixture, fire
``authorize_action`` and assert it DENIES the deny-kind and abstains/allows the
allow-kind (Bosun first-deny semantics), and fire ``before/after_tool_call`` audit
hooks. Because the asserts run against a live plugin manager, a trivially-passing
generated unit test cannot land a plugin that doesn't register, compute, gate, or
audit.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` - tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox); the contract
tier additionally imports + registers + runs the generated plugin's hook + tool code.
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
    "PLUGIN_FILE",
    "TEST_FILE",
    "VerifierResult",
    "all_passed",
    "run_full_gate",
    "verify_sources",
]

PLUGIN_FILE = "plugin.py"
TEST_FILE = "test_plugin.py"


# Driver executed in a subprocess: register the generated plugin module on an
# isolated PluginManager and exercise its hooks + tool for real. The payload
# carries ``meta`` (the tool identity + the callable's attr name) and ``fixture``
# (tool args, expected output, and the deny/allow action kinds).
_CONTRACT_DRIVER = """\
import importlib, json, sys
from pathlib import Path

import pluggy

from stargraph.plugin import hookspecs
from stargraph.plugin.types import BosunAction, ToolCall
from stargraph.runtime.tool_exec import ToolResult


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


# plugin.py is imported by bare name, so the scratch dir must lead sys.path.
sys.path.insert(0, str(Path.cwd()))

contract = json.loads(Path("contract.json").read_text())
meta = contract.get("meta", {})
fixture = contract.get("fixture", {})
tool_name = str(meta.get("tool_name", ""))
namespace = str(meta.get("namespace", ""))
tool_attr = str(meta.get("tool_attr", ""))
tool_args = fixture.get("tool_args", {}) or {}
tool_expects = fixture.get("tool_expects")
deny_kind = str(fixture.get("deny_kind", ""))
allow_kind = str(fixture.get("allow_kind", ""))

try:
    plugin = importlib.import_module("plugin")
except Exception as e:
    _fail(f"plugin.py did not import: {type(e).__name__}: {e}")

# A bare, isolated manager: only the Stargraph hookspecs + this one plugin. No
# load_setuptools_entrypoints, so no installed plugin can shadow authorize_action.
try:
    pm = pluggy.PluginManager("stargraph")
    pm.add_hookspecs(hookspecs)
    pm.register(plugin, name="generated")
except Exception as e:
    _fail(f"plugin did not register on a PluginManager: {type(e).__name__}: {e}")

# register_tools must advertise the declared tool.
try:
    specs = [s for lst in pm.hook.register_tools() for s in (lst or [])]
except Exception as e:
    _fail(f"register_tools() raised: {type(e).__name__}: {e}")
match = [
    s for s in specs
    if getattr(s, "name", "") == tool_name and getattr(s, "namespace", "") == namespace
]
if not match:
    advertised = [(getattr(s, "namespace", ""), getattr(s, "name", "")) for s in specs]
    _fail(f"register_tools() did not advertise {namespace}.{tool_name} (got {advertised})")

# The @tool callable must exist, be decorated, and compute the expected output.
fn = getattr(plugin, tool_attr, None)
if fn is None:
    _fail(f"plugin.py has no callable named {tool_attr!r}")
if not hasattr(fn, "spec"):
    _fail(f"{tool_attr!r} is not @tool-decorated (no .spec attribute)")
try:
    got = fn(**tool_args)
except Exception as e:
    _fail(f"calling {tool_attr}(**{sorted(tool_args)}) raised: {type(e).__name__}: {e}")
if got != tool_expects:
    _fail(f"tool {tool_attr} returned {got!r}, expected {tool_expects!r}")

# authorize_action: first-deny semantics. The deny-kind MUST be denied (False);
# the allow-kind must NOT be denied (abstain None or allow True).
if deny_kind:
    target = f"{namespace}.{tool_name}"
    verdict = pm.hook.authorize_action(
        action=BosunAction(action_kind=deny_kind, target=target, payload={})
    )
    if verdict is not False:
        _fail(f"authorize_action did not DENY action_kind={deny_kind!r} (got {verdict!r})")
if allow_kind:
    target = f"{namespace}.{tool_name}"
    verdict = pm.hook.authorize_action(
        action=BosunAction(action_kind=allow_kind, target=target, payload={})
    )
    if verdict not in (None, True):
        _fail(
            f"authorize_action did not allow/abstain action_kind={allow_kind!r} "
            f"(got {verdict!r}; expected None or True)"
        )

# Audit hooks must fire without raising.
call = ToolCall(
    tool_name=tool_name, namespace=namespace, args=dict(tool_args), call_id="contract-1"
)
try:
    pm.hook.before_tool_call(call=call)
    pm.hook.after_tool_call(call=call, result=ToolResult(output={"ok": True}))
except Exception as e:
    _fail(f"audit hooks (before/after_tool_call) raised: {type(e).__name__}: {e}")

print(json.dumps({"ok": True, "tool": f"{namespace}.{tool_name}"}))
"""


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    meta: dict[str, str],
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    The contract tier registers the generated ``plugin.py`` on an isolated
    ``PluginManager`` and drives its hooks + tool against ``fixture``; see
    ``_CONTRACT_DRIVER``.
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(_CONTRACT_DRIVER, {"meta": meta, "fixture": fixture}),
        test_file=TEST_FILE,
    )


def verify_sources(
    *,
    namespace: str,
    tool_name: str,
    tool_attr: str,
    plugin_source: str,
    test_source: str,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on a raw plugin bundle in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a
    work dir — ``pluginsmith make``, the doctor preflight, and seed verification.
    Returns ``(passed, results)``.
    """
    files = {PLUGIN_FILE: plugin_source, TEST_FILE: test_source}
    meta = {"tool_name": tool_name, "namespace": namespace, "tool_attr": tool_attr}
    with tempfile.TemporaryDirectory(prefix="pluginsmith-verify-") as d:
        results = run_full_gate(Path(d), files, meta=meta, fixture=fixture)
    return all_passed(results), results
