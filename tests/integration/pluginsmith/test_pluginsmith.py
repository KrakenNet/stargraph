# SPDX-License-Identifier: Apache-2.0
"""Pluginsmith integration tests.

The DSPy generator is stubbed for determinism, but the verify gate runs for real in
every test — these assert the composite "always works" contract end-to-end: the
contract tier REGISTERS the generated plugin on an isolated ``PluginManager`` and
drives its hooks + tool, so a plugin that doesn't advertise its tool, computes the
wrong output, or never denies its guarded action cannot land even when its own unit
test passes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from tests.fixtures.smith_testkit import CTX, drive, stub_build

from stargraph.skills.pluginsmith import _ledger, gate
from stargraph.skills.pluginsmith.nodes.build import Build
from stargraph.skills.pluginsmith.nodes.recall import Recall
from stargraph.skills.pluginsmith.nodes.record import RecordBuild
from stargraph.skills.pluginsmith.nodes.triage import TriageGate
from stargraph.skills.pluginsmith.seeds import (
    _REDACT_FIXTURE,  # pyright: ignore[reportPrivateUsage]
    _REDACT_PLUGIN,  # pyright: ignore[reportPrivateUsage]
    _REDACT_TEST,  # pyright: ignore[reportPrivateUsage]
)
from stargraph.skills.pluginsmith.state import State

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.integration

# The good plugin (seed 1: redact emails, deny external_send).
GOOD_PLUGIN = _REDACT_PLUGIN
GOOD_TEST = _REDACT_TEST
GOOD_FIXTURE = _REDACT_FIXTURE
GOOD_META = {"tool_name": "redact_email", "namespace": "privacy", "tool_attr": "redact_email"}
GOOD_GEN: dict[str, Any] = {
    "plugin_name": "email-redactor",
    "namespace": "privacy",
    "tool_name": "redact_email",
    "tool_attr": "redact_email",
    "plugin_source": GOOD_PLUGIN,
    "fixture": GOOD_FIXTURE,
    "test_source": GOOD_TEST,
}

TRIVIAL_TEST = "def test_x() -> None:\n    assert True\n"

# Adversarial: registers + denies fine, but the tool never redacts → wrong output.
WRONG_OUTPUT_PLUGIN = """\
from __future__ import annotations

from stargraph.plugin import hookimpl
from stargraph.tools import SideEffects, tool


@tool(name="redact_email", namespace="privacy", version="1.0.0", side_effects=SideEffects.none)
def redact_email(text: str) -> str:
    return text  # never actually redacts


@hookimpl
def register_tools():
    return [redact_email.spec]


@hookimpl
def authorize_action(action):
    if action.action_kind == "external_send":
        return False
    return None


@hookimpl
def before_tool_call(call):
    return None


@hookimpl
def after_tool_call(call, result):
    return None
"""

# Adversarial: tool + registration fine, but authorize_action never denies anything.
NON_DENYING_PLUGIN = """\
from __future__ import annotations

import re

from stargraph.plugin import hookimpl
from stargraph.tools import SideEffects, tool

_EMAIL = re.compile(r"[\\w.+-]+@[\\w-]+\\.[\\w.-]+")


@tool(name="redact_email", namespace="privacy", version="1.0.0", side_effects=SideEffects.none)
def redact_email(text: str) -> str:
    return _EMAIL.sub("[redacted]", text)


@hookimpl
def register_tools():
    return [redact_email.spec]


@hookimpl
def authorize_action(action):
    return None  # abstains on everything — never enforces a deny


@hookimpl
def before_tool_call(call):
    return None


@hookimpl
def after_tool_call(call, result):
    return None
"""

# Adversarial: a valid @tool that the plugin never advertises via register_tools.
UNREGISTERED_PLUGIN = """\
from __future__ import annotations

from stargraph.plugin import hookimpl
from stargraph.tools import SideEffects, tool


@tool(name="redact_email", namespace="privacy", version="1.0.0", side_effects=SideEffects.none)
def redact_email(text: str) -> str:
    return text


@hookimpl
def register_tools():
    return []  # tool exists but is never advertised


@hookimpl
def authorize_action(action):
    if action.action_kind == "external_send":
        return False
    return None


@hookimpl
def before_tool_call(call):
    return None


@hookimpl
def after_tool_call(call, result):
    return None
"""

# Syntax error → fails the static tier fast; the repair loop can never fix a stub.
BAD_GEN: dict[str, Any] = {
    "plugin_name": "broken",
    "namespace": "x",
    "tool_name": "y",
    "tool_attr": "y",
    "plugin_source": "def y(:\n    pass\n",
    "fixture": {},
    "test_source": TRIVIAL_TEST,
}


def _files(*, plugin: str, test: str) -> dict[str, str]:
    return {gate.PLUGIN_FILE: plugin, gate.TEST_FILE: test}


# --------------------------------------------------------------------------- #
# Gate (the un-cheatable composite floor: plugin registers + computes + gates)
# --------------------------------------------------------------------------- #
async def test_gate_passes_a_real_plugin(tmp_path: Path) -> None:
    files = _files(plugin=GOOD_PLUGIN, test=GOOD_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=GOOD_FIXTURE)
    assert gate.all_passed(results), [r.findings for r in results if not r.passed]


async def test_gate_contract_catches_wrong_tool_output(tmp_path: Path) -> None:
    files = _files(plugin=WRONG_OUTPUT_PLUGIN, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "expected" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_non_denying_authorize(tmp_path: Path) -> None:
    files = _files(plugin=NON_DENYING_PLUGIN, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "deny" in contract.findings[0]["msg"].lower()


async def test_gate_contract_catches_unadvertised_tool(tmp_path: Path) -> None:
    files = _files(plugin=UNREGISTERED_PLUGIN, test=TRIVIAL_TEST)
    results = gate.run_full_gate(tmp_path / "g", files, meta=GOOD_META, fixture=GOOD_FIXTURE)
    contract = next(r for r in results if r.kind == "contract")
    assert not contract.passed
    assert "advertise" in contract.findings[0]["msg"].lower()


# --------------------------------------------------------------------------- #
# Build loop + record
# --------------------------------------------------------------------------- #
async def test_build_succeeds_first_try() -> None:
    out = await stub_build(Build, GOOD_GEN).execute(State(brief="email redactor plugin"), CTX)
    assert out["succeeded"] is True
    assert out["fix_attempts"] == 1
    assert out["plugin_name"] == "email-redactor"
    assert out["namespace"] == "privacy"


async def test_build_exhausts_retries_on_persistent_failure() -> None:
    out = await stub_build(Build, BAD_GEN).execute(State(brief="broken"), CTX)
    assert out["succeeded"] is False
    assert out["fix_attempts"] == 3  # bounded, never infinite


async def test_success_records_trainset_pair_and_lands(tmp_path: Path) -> None:
    out_dir = tmp_path / "generated"
    state = State(brief="email redactor plugin", model_id="stub-model", output_dir=str(out_dir))
    final = await drive([stub_build(Build, GOOD_GEN), RecordBuild()], state)

    pairs = _ledger.load_trainset()
    assert len(pairs) == 1
    assert pairs[0]["plugin_name"] == "email-redactor"
    assert pairs[0]["namespace"] == "privacy"
    assert pairs[0]["passed"] is True
    assert pairs[0]["model_id"] == "stub-model"

    bundle = out_dir / "email_redactor"
    assert final.landed_path == str(bundle / "plugin.py")
    for name in ("plugin.py", "test_plugin.py"):
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
        brief="a plugin that redacts emails and denies external sending",
        failed_kind="contract",
        finding="authorize_action abstained instead of denying external_send",
        attempts=1,
    )
    _ledger.append_lesson(
        brief="totally unrelated sqlite store thing",
        failed_kind="tests",
        finding="irrelevant",
        attempts=1,
    )
    out = await Recall().execute(State(brief="redact emails and deny external send plugin"), CTX)
    assert out["recalled_lessons"]
    assert "external_send" in out["recalled_lessons"][0]


def test_drift_rate_tracks_first_try_ratio() -> None:
    for attempts in (1, 1, 1, 3):  # 3 of 4 nailed on first try
        _ledger.append_trainset({"brief": "x", "attempts": attempts, "passed": True})
    assert _ledger.drift_rate(window=10) == pytest.approx(0.75)  # pyright: ignore[reportUnknownMemberType]


async def test_triage_rejects_empty_brief() -> None:
    with pytest.raises(ValueError, match="brief is required"):
        await TriageGate().execute(State(brief="  "), CTX)
