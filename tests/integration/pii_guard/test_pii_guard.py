# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the ``pii_guard`` reference plugin.

Three concerns, mirroring the plugin's three surfaces:

* the ``redact_pii`` tool masks emails / phones / cards and counts them;
* ``authorize_action`` default-denies a PII-sensitive write while abstaining
  on unrelated actions — exercised on a *fresh* :class:`pluggy.PluginManager`
  so the global plugin manager (and other tests) are untouched;
* the ``before_tool_call`` / ``after_tool_call`` hooks record an audit entry.

Isolation pattern (copied from ``tests/integration/test_mcp_stdio_adapter.py``
and ``tests/integration/serve/test_trigger_isolation.py``): build a bare
``pluggy.PluginManager(PROJECT)``, ``add_hookspecs(hookspecs)``, then
``register(hooks_module, name=...)`` — never ``build_plugin_manager`` and never
an entry point, so ``authorize_action`` stays scoped to this manager.
"""

from __future__ import annotations

import pluggy
import pytest

from stargraph.plugin import hookspecs
from stargraph.plugin._markers import PROJECT
from stargraph.plugin.types import BosunAction, ToolCall
from stargraph.plugins.pii_guard import hooks
from stargraph.plugins.pii_guard.redact import redact_pii
from stargraph.runtime.tool_exec import ToolResult

pytestmark = pytest.mark.integration


def _isolated_pm() -> pluggy.PluginManager:
    """Fresh manager with only the hookspecs + the pii_guard hooks module."""
    pm = pluggy.PluginManager(PROJECT)
    pm.add_hookspecs(hookspecs)
    pm.register(hooks, name="pii-guard-hooks")
    return pm


async def test_redact_pii_masks_and_counts() -> None:
    """Emails, phones, and card-like runs are masked with the right counts."""
    text = (
        "Contact alice@example.com or bob.smith@corp.co.uk. "
        "Call +1 (415) 555-0199 or 020 7946 0958. "
        "Card 4111 1111 1111 1111 on file."
    )
    result = await redact_pii(text=text)

    redacted = result["redacted"]
    assert "alice@example.com" not in redacted
    assert "bob.smith@corp.co.uk" not in redacted
    assert "4111 1111 1111 1111" not in redacted
    assert "555-0199" not in redacted
    assert "[EMAIL]" in redacted
    assert "[PHONE]" in redacted
    assert "[CARD]" in redacted

    counts = result["counts"]
    assert counts["email"] == 2
    assert counts["card"] == 1
    assert counts["phone"] == 2
    assert result["total"] == 5


async def test_redact_pii_clean_text_is_unchanged() -> None:
    """Text with no PII passes through with a zero count."""
    result = await redact_pii(text="the quick brown fox jumps over the lazy dog")
    assert result["redacted"] == "the quick brown fox jumps over the lazy dog"
    assert result["total"] == 0
    assert result["counts"] == {"email": 0, "card": 0, "phone": 0}


def test_authorize_action_denies_pii_write() -> None:
    """A write against a PII-sensitive target is denied (``False``)."""
    pm = _isolated_pm()
    action = BosunAction(
        action_kind="write",
        target="store:pii:write",
        payload={"ssn": "123-45-6789"},
    )
    assert pm.hook.authorize_action(action=action) is False


def test_authorize_action_allows_pii_read() -> None:
    """A read against a PII target abstains (``None``) — reads are safe."""
    pm = _isolated_pm()
    action = BosunAction(
        action_kind="read",
        target="store:pii:read",
        payload={},
    )
    assert pm.hook.authorize_action(action=action) is None


def test_authorize_action_abstains_on_unrelated_action() -> None:
    """A non-PII action abstains (``None``) so other plugins decide."""
    pm = _isolated_pm()
    action = BosunAction(
        action_kind="write",
        target="servicenow:incident:create",
        payload={"short_description": "disk full"},
    )
    assert pm.hook.authorize_action(action=action) is None


def test_audit_hooks_record_a_tool_call() -> None:
    """``before_tool_call`` / ``after_tool_call`` append safe audit records."""
    pm = _isolated_pm()
    hooks.auditor.clear()

    call = ToolCall(
        tool_name="redact_pii",
        namespace="pii_guard",
        args={"text": "alice@example.com"},
        call_id="c-1",
    )
    result = ToolResult(output={"redacted": "[EMAIL]", "counts": {}, "total": 1})

    pm.hook.before_tool_call(call=call)
    pm.hook.after_tool_call(call=call, result=result)

    records = hooks.auditor.records
    assert len(records) == 2
    assert all(r.tool_id == "pii_guard.redact_pii" for r in records)
    assert records[0].note.startswith("before:")
    assert records[1].note.startswith("after:")
    # The audit note must not leak the raw argument value.
    assert "alice@example.com" not in records[0].note
