# SPDX-License-Identifier: Apache-2.0
"""Hand-authored, gate-verified seed plugins — the trainset cold start.

Each entry is a verified ``(brief → plugin)`` pair: a single ``plugin.py`` carrying a
``@tool``-decorated callable plus the pluggy ``@hookimpl`` functions
(``register_tools`` advertising the tool, ``authorize_action`` denying one action
kind / abstaining otherwise, and the ``before/after_tool_call`` audit hooks), its
``test_plugin.py``, and the ``fixture`` the contract tier drives the registered
plugin against. Seed 1 redacts emails and denies ``external_send``; seed 2 masks a
secret token and denies ``exfiltrate``. Both register on an isolated
``PluginManager``, compute the fixture's expected output, deny their guarded action
kind, and abstain on a different one — so the contract tier only passes if the plugin
actually works. ``id`` is a fixed literal so ``seed_trainset`` is idempotent.

``tests/integration/pluginsmith/test_seeds.py`` runs every pair through
``gate.verify_sources`` — if a seed stops passing, that test fails.
"""

from __future__ import annotations

from typing import Any

# --- Seed 1: redact emails, deny external_send ------------------------------- #
_REDACT_PLUGIN = '''\
from __future__ import annotations

import re

from stargraph.plugin import hookimpl
from stargraph.tools import SideEffects, tool

_EMAIL = re.compile(r"[\\w.+-]+@[\\w-]+\\.[\\w.-]+")
_AUDIT: list[str] = []


@tool(name="redact_email", namespace="privacy", version="1.0.0", side_effects=SideEffects.none)
def redact_email(text: str) -> str:
    """Replace any email address in the text with a redaction marker."""
    return _EMAIL.sub("[redacted]", text)


@hookimpl
def register_tools():
    return [redact_email.spec]


@hookimpl
def authorize_action(action):
    # First-deny: block exfiltration of redacted data, abstain on everything else.
    if action.action_kind == "external_send":
        return False
    return None


@hookimpl
def before_tool_call(call):
    _AUDIT.append(f"before:{call.namespace}.{call.tool_name}")


@hookimpl
def after_tool_call(call, result):
    _AUDIT.append(f"after:{call.namespace}.{call.tool_name}")
'''

_REDACT_TEST = """\
from plugin import redact_email


def test_redacts_email() -> None:
    assert redact_email(text="ping bob@acme.com now") == "ping [redacted] now"


def test_passes_through_plain_text() -> None:
    assert redact_email(text="no address here") == "no address here"
"""

_REDACT_FIXTURE: dict[str, Any] = {
    "tool_args": {"text": "ping me at bob@acme.com please"},
    "tool_expects": "ping me at [redacted] please",
    "deny_kind": "external_send",
    "allow_kind": "read",
}

# --- Seed 2: mask a secret token, deny exfiltrate ---------------------------- #
_MASK_PLUGIN = '''\
from __future__ import annotations

from stargraph.plugin import hookimpl
from stargraph.tools import SideEffects, tool

_AUDIT: list[str] = []


@tool(name="mask_token", namespace="security", version="1.0.0", side_effects=SideEffects.none)
def mask_token(token: str) -> str:
    """Mask all but the last four characters of a secret token."""
    if len(token) <= 4:
        return token
    return "*" * (len(token) - 4) + token[-4:]


@hookimpl
def register_tools():
    return [mask_token.spec]


@hookimpl
def authorize_action(action):
    # First-deny: block exfiltration of the secret, abstain on everything else.
    if action.action_kind == "exfiltrate":
        return False
    return None


@hookimpl
def before_tool_call(call):
    _AUDIT.append(call.call_id)


@hookimpl
def after_tool_call(call, result):
    _AUDIT.append(call.call_id)
'''

_MASK_TEST = """\
from plugin import mask_token


def test_masks_all_but_last_four() -> None:
    assert mask_token(token="sk-ABCD1234") == "*******1234"


def test_short_token_unchanged() -> None:
    assert mask_token(token="ab") == "ab"
"""

_MASK_FIXTURE: dict[str, Any] = {
    "tool_args": {"token": "sk-ABCD1234"},
    "tool_expects": "*******1234",
    "deny_kind": "exfiltrate",
    "allow_kind": "read",
}


def _pair(
    seed_id: str,
    brief: str,
    plugin_name: str,
    namespace: str,
    tool_name: str,
    tool_attr: str,
    plugin_source: str,
    test_source: str,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": seed_id,
        "brief": brief,
        "plugin_name": plugin_name,
        "namespace": namespace,
        "tool_name": tool_name,
        "tool_attr": tool_attr,
        "plugin_source": plugin_source,
        "test_source": test_source,
        "fixture": fixture,
        "attempts": 1,
        "passed": True,
        "verdict": "accept",
    }


SEEDS: list[dict[str, Any]] = [
    _pair(
        "b0010000001",
        "a plugin whose tool redacts email addresses from text and denies external_send",
        "email-redactor",
        "privacy",
        "redact_email",
        "redact_email",
        _REDACT_PLUGIN,
        _REDACT_TEST,
        _REDACT_FIXTURE,
    ),
    _pair(
        "b0010000002",
        "a plugin whose tool masks all but the last four chars of a secret and denies exfiltrate",
        "secret-masker",
        "security",
        "mask_token",
        "mask_token",
        _MASK_PLUGIN,
        _MASK_TEST,
        _MASK_FIXTURE,
    ),
]
