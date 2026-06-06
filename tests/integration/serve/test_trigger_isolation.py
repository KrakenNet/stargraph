# SPDX-License-Identifier: Apache-2.0
"""Phase-3 integration test (task 3.15): per-plugin try/except trigger isolation.

Verifies the locked design §6.3 contract: when one trigger plugin
raises in :func:`~stargraph.plugin.hookspecs.trigger_init` (or any of
the four lifecycle hooks), the other registered triggers MUST still
initialise / start / stop / contribute routes. Pluggy's default
behaviour is to halt iteration on the first exception; the per-plugin
isolation lives in
:func:`stargraph.plugin.triggers_dispatcher.dispatch_trigger_lifecycle`
which iterates the plugin list directly with a per-plugin
``try/except`` (loader-side defence in depth, FR-2, AC-12.2).

This test exercises the dispatcher against a synthetic
:class:`pluggy.PluginManager` populated with:

* One **failing** plugin: ``trigger_init`` raises a synthetic
  :class:`RuntimeError`.
* Three working plugins: stand-ins for the
  :class:`~stargraph.triggers.manual.ManualTrigger`,
  :class:`~stargraph.triggers.cron.CronTrigger`, and
  :class:`~stargraph.triggers.webhook.WebhookTrigger` lifecycle surfaces.
  We use simple stand-in plugin objects (each with a ``trigger_init``
  attribute) rather than importing the real plugin modules because
  the real cron/webhook plugins require a running asyncio loop and
  scheduler reference; the isolation contract is plugin-agnostic, so
  the substitutes preserve the assertion shape.

The test asserts:

1. :func:`dispatch_trigger_lifecycle` does NOT propagate the failing
   plugin's exception (it returns a :class:`DispatchResult` list with
   ``success=False`` for the failing plugin and ``success=True`` for
   the three working stand-ins).
2. The structlog ``stargraph.plugin.triggers`` logger emitted a
   ``trigger_lifecycle_failed`` event for the failing plugin's failure
   (captured via :func:`structlog.testing.capture_logs`).
3. ``trigger_routes`` collection is similarly isolated: a synthetic
   plugin whose ``trigger_routes`` raises does not block the other
   plugins' route lists.

Refs: tasks.md §3.15; design §16.2 + §6.3; FR-2, AC-12.2, AC-12.3.
"""

from __future__ import annotations

from typing import Any

import pluggy
import pytest
from structlog.testing import capture_logs

from stargraph.plugin._markers import PROJECT
from stargraph.plugin.triggers_dispatcher import (
    collect_trigger_routes,
    dispatch_trigger_lifecycle,
)

pytestmark = [pytest.mark.serve, pytest.mark.trigger, pytest.mark.integration]


# --------------------------------------------------------------------------- #
# Synthetic plugin shapes                                                     #
# --------------------------------------------------------------------------- #


class _FailingTriggerPlugin:
    """Trigger plugin whose ``trigger_init`` always raises.

    Mirrors the shape :func:`stargraph.plugin.triggers_dispatcher.dispatch_trigger_lifecycle`
    iterates: any object exposing a ``trigger_init`` callable counts.
    The structural-typing match means we do NOT need pluggy hookimpl
    decorators here; the dispatcher reads the attribute via
    :func:`getattr` and calls it directly.

    The exception payload is a fixed sentinel so the test can verify
    the dispatcher captured *this* exception in the result list.
    """

    trigger_id = "synthetic-failing-plugin"

    def trigger_init(self, deps: dict[str, Any]) -> None:
        del deps
        raise RuntimeError("synthetic plugin init failure (FR-2 isolation probe)")

    def trigger_routes(self) -> list[Any]:
        raise RuntimeError("synthetic plugin routes failure (FR-2 isolation probe)")


class _WorkingTriggerPlugin:
    """Trigger plugin stand-in whose lifecycle hooks always succeed.

    Records every ``trigger_init`` invocation on ``self.calls`` so the
    test can assert "this plugin was actually visited despite the
    failing sibling". Matches the shape of the real built-in triggers
    (``trigger_id`` + ``trigger_init`` callable) without pulling in
    the scheduler / asyncio dependencies.
    """

    def __init__(self, plugin_id: str) -> None:
        self.trigger_id = plugin_id
        self.init_calls: list[dict[str, Any]] = []
        self.route_calls: int = 0

    def trigger_init(self, deps: dict[str, Any]) -> None:
        self.init_calls.append(dict(deps))

    def trigger_routes(self) -> list[Any]:
        self.route_calls += 1
        # Return one synthetic "route" sentinel per call; the
        # dispatcher does not introspect the contents.
        return [f"route:{self.trigger_id}"]


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _build_manager_with_plugins(
    plugins: list[tuple[str, Any]],
) -> pluggy.PluginManager:
    """Construct a fresh :class:`pluggy.PluginManager` with ``plugins`` registered.

    Each tuple is ``(register_name, plugin_obj)`` -- pluggy uses the
    name to track plugin identity (``pm.get_name(plugin)``). The
    dispatcher iterates ``pm.get_plugins()`` and reads
    ``pm.get_name(...)`` for the failure log line, so a stable
    register name keeps the assertion deterministic.
    """
    pm = pluggy.PluginManager(PROJECT)
    for name, plugin in plugins:
        pm.register(plugin, name=name)
    return pm


# --------------------------------------------------------------------------- #
# Test 1: failing plugin doesn't break startup                                #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.trigger
def test_failing_trigger_init_does_not_block_other_triggers() -> None:
    """One plugin's ``trigger_init`` raise does not stop the other 3 from running.

    Asserts:

    1. ``dispatch_trigger_lifecycle`` returns 4 results (one per
       plugin attempted).
    2. The failing plugin's :class:`DispatchResult` has
       ``success=False`` and ``error`` is the synthetic
       :class:`RuntimeError`.
    3. The 3 working plugin stand-ins each have ``success=True`` and
       were *actually visited* (their ``init_calls`` list grew).
    4. The structlog ``stargraph.plugin.triggers`` logger emitted a
       ``trigger_lifecycle_failed`` log line for the failing plugin
       (captured via :func:`structlog.testing.capture_logs`).
    """
    manual = _WorkingTriggerPlugin("manual")
    cron = _WorkingTriggerPlugin("cron")
    webhook = _WorkingTriggerPlugin("webhook")
    failing = _FailingTriggerPlugin()
    pm = _build_manager_with_plugins(
        [
            ("plugin:manual", manual),
            ("plugin:failing", failing),
            ("plugin:cron", cron),
            ("plugin:webhook", webhook),
        ]
    )

    deps: dict[str, Any] = {"scheduler": object()}

    with capture_logs() as captured:
        results = dispatch_trigger_lifecycle(pm, "trigger_init", deps)

    # ---- Assertion 1: 4 plugin results -----------------------------------
    assert len(results) == 4, (
        f"expected 4 DispatchResult rows (one per plugin); got "
        f"{len(results)}: {[r.plugin_name for r in results]!r}"
    )
    by_name = {r.plugin_name: r for r in results}
    expected_names = {
        "plugin:manual",
        "plugin:failing",
        "plugin:cron",
        "plugin:webhook",
    }
    assert set(by_name.keys()) == expected_names, (
        f"plugin name set mismatch: got {sorted(by_name)!r}, expected {sorted(expected_names)!r}"
    )

    # ---- Assertion 2: failing plugin captured success=False --------------
    failing_result = by_name["plugin:failing"]
    assert failing_result.success is False
    assert isinstance(failing_result.error, RuntimeError)
    assert "synthetic plugin init failure" in str(failing_result.error)

    # ---- Assertion 3: 3 working plugins each visited successfully --------
    for name in ("plugin:manual", "plugin:cron", "plugin:webhook"):
        result = by_name[name]
        assert result.success is True, (
            f"{name} should have succeeded but reported "
            f"success={result.success!r} error={result.error!r}"
        )
        assert result.error is None
    # Each working plugin's hook was actually invoked (defence against a
    # dispatcher that swallowed the call short-circuit somehow).
    assert manual.init_calls == [deps]
    assert cron.init_calls == [deps]
    assert webhook.init_calls == [deps]

    # ---- Assertion 4: structlog captured the failure ---------------------
    failed_logs = [rec for rec in captured if rec.get("event") == "trigger_lifecycle_failed"]
    assert len(failed_logs) == 1, (
        f"expected exactly 1 trigger_lifecycle_failed log line; got "
        f"{[r.get('event') for r in captured]!r}"
    )
    log_rec = failed_logs[0]
    assert log_rec.get("plugin") == "plugin:failing"
    assert log_rec.get("hook") == "trigger_init"
    assert log_rec.get("error_type") == "RuntimeError"
    assert "synthetic plugin init failure" in str(log_rec.get("error", ""))


# --------------------------------------------------------------------------- #
# Test 2: dispatcher returns cleanly (no propagation)                         #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.trigger
def test_failing_trigger_does_not_propagate_exception() -> None:
    """``dispatch_trigger_lifecycle`` returns normally even when a plugin raises.

    The contract: failing plugin's exception is caught at the
    plugin-loader boundary and recorded in the result list, never
    re-raised. A test that simply called the dispatcher inside a
    ``try/except RuntimeError: pytest.fail(...)`` would assert the
    same thing, but this expansion documents the contract more
    explicitly: ``dispatch_trigger_lifecycle`` is a pure function
    that returns a list and never raises.
    """
    failing = _FailingTriggerPlugin()
    working = _WorkingTriggerPlugin("manual")
    pm = _build_manager_with_plugins(
        [
            ("plugin:failing", failing),
            ("plugin:manual", working),
        ]
    )

    # Drive every lifecycle hook the dispatcher supports. None of the
    # three should raise even though the failing plugin always raises
    # in trigger_init (the dispatcher walks per-plugin try/except).
    init_results = dispatch_trigger_lifecycle(pm, "trigger_init", {})
    start_results = dispatch_trigger_lifecycle(pm, "trigger_start", {})
    stop_results = dispatch_trigger_lifecycle(pm, "trigger_stop", {})

    # ``trigger_start`` and ``trigger_stop`` are not implemented on the
    # synthetic plugins -- the dispatcher silently skips plugins that
    # don't expose the named hook (loader.py:80-81). So the lists are
    # empty rather than ``[success=True, ...]``.
    assert init_results, "trigger_init should attempt the failing plugin"
    assert start_results == [], (
        f"synthetic plugins don't implement trigger_start; got {start_results!r}"
    )
    assert stop_results == [], (
        f"synthetic plugins don't implement trigger_stop; got {stop_results!r}"
    )

    # The init dispatch returned both rows, even though one was a
    # failing init.
    assert {r.plugin_name for r in init_results} == {
        "plugin:failing",
        "plugin:manual",
    }


# --------------------------------------------------------------------------- #
# Test 3: collect_trigger_routes isolation (parity with init/start/stop)      #
# --------------------------------------------------------------------------- #


@pytest.mark.serve
@pytest.mark.trigger
def test_failing_trigger_routes_does_not_block_others() -> None:
    """``collect_trigger_routes`` isolates per-plugin failures.

    Mirrors the init/start/stop isolation: a plugin whose
    ``trigger_routes`` raises does not block the other plugins'
    route contributions. The serve app's lifespan flattens the
    successful results into a single mount-list (per the dispatcher
    docstring); this test exercises just the dispatcher seam.
    """
    failing = _FailingTriggerPlugin()
    manual = _WorkingTriggerPlugin("manual")
    webhook = _WorkingTriggerPlugin("webhook")
    pm = _build_manager_with_plugins(
        [
            ("plugin:manual", manual),
            ("plugin:failing", failing),
            ("plugin:webhook", webhook),
        ]
    )

    with capture_logs() as captured:
        results = collect_trigger_routes(pm)

    assert len(results) == 3, (
        f"expected 3 route-collection results; got {len(results)}: "
        f"{[r.plugin_name for r in results]!r}"
    )
    by_name = {r.plugin_name: r for r in results}
    assert by_name["plugin:failing"].success is False
    assert isinstance(by_name["plugin:failing"].error, RuntimeError)

    # The two working plugins each contributed their synthetic route.
    assert by_name["plugin:manual"].success is True
    assert by_name["plugin:manual"].result == ["route:manual"]
    assert by_name["plugin:webhook"].success is True
    assert by_name["plugin:webhook"].result == ["route:webhook"]

    # Failure logged at structlog with hook=trigger_routes.
    failed_logs = [rec for rec in captured if rec.get("event") == "trigger_routes_failed"]
    assert len(failed_logs) == 1, (
        f"expected exactly 1 trigger_routes_failed log; got {[r.get('event') for r in captured]!r}"
    )
    assert failed_logs[0].get("plugin") == "plugin:failing"
