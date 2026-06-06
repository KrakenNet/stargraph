# SPDX-License-Identifier: Apache-2.0
"""Unit tests for :mod:`stargraph.logging` (AC-3.3, AC-3.4, NFR-6).

Covers:

* :func:`get_logger` returns a usable structlog bound logger.
* :func:`run_context` sets ``run_id`` / ``step`` / ``node_id`` ContextVars.
* The structlog processor injects those correlation IDs into emitted JSON.
* Nested :func:`run_context` blocks restore the outer values on exit.
* Accessor functions return ``None`` outside any context.
* Accessor functions return the active values inside a context.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from stargraph.logging import get_logger, run_context
from stargraph.logging._context import get_node_id, get_run_id, get_step


def _last_json_line(captured: str) -> dict[str, Any]:
    """Return the parsed JSON payload from the final non-empty stdout line."""
    lines = [line for line in captured.splitlines() if line.strip()]
    assert lines, "expected at least one log line on stdout"
    payload: dict[str, Any] = json.loads(lines[-1])
    return payload


@pytest.mark.unit
def test_get_logger_returns_logger_with_event_method() -> None:
    """:func:`get_logger` returns a logger exposing the standard level methods."""
    log = get_logger("stargraph.test")

    # Structlog bound loggers expose info/warning/error/debug callables.
    for method in ("info", "warning", "error", "debug"):
        assert callable(getattr(log, method)), f"logger missing .{method}()"


@pytest.mark.unit
def test_log_emits_json_with_correlation_fields(capsys: pytest.CaptureFixture[str]) -> None:
    """Inside :func:`run_context`, every event includes run_id / step / node_id."""
    log = get_logger("stargraph.test")

    with run_context("run-abc", 5, "node-xyz"):
        log.info("greeting", who="stargraph")

    payload = _last_json_line(capsys.readouterr().out)

    assert payload["event"] == "greeting"
    assert payload["who"] == "stargraph"
    assert payload["run_id"] == "run-abc"
    assert payload["step"] == 5
    assert payload["node_id"] == "node-xyz"
    assert payload["level"] == "info"
    # ISO-8601 timestamp from structlog's TimeStamper(fmt="iso", utc=True).
    assert "timestamp" in payload


@pytest.mark.unit
def test_log_outside_run_context_omits_correlation_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With no active run_context, correlation keys are absent from the payload."""
    log = get_logger("stargraph.test")

    log.info("naked")

    payload = _last_json_line(capsys.readouterr().out)

    assert payload["event"] == "naked"
    assert "run_id" not in payload
    assert "step" not in payload
    assert "node_id" not in payload


@pytest.mark.unit
def test_run_context_sets_and_resets_contextvars() -> None:
    """ContextVar accessors return ``None`` before/after, values during the block."""
    assert get_run_id() is None
    assert get_step() is None
    assert get_node_id() is None

    with run_context("r-1", 0, "n-1"):
        assert get_run_id() == "r-1"
        assert get_step() == 0
        assert get_node_id() == "n-1"

    # ContextVars are reset on exit (via tokens) — no leakage.
    assert get_run_id() is None
    assert get_step() is None
    assert get_node_id() is None


@pytest.mark.unit
def test_run_context_node_id_defaults_to_none() -> None:
    """Omitting ``node_id`` leaves the ContextVar at ``None`` while run/step are set."""
    with run_context("r-9", 7):
        assert get_run_id() == "r-9"
        assert get_step() == 7
        assert get_node_id() is None


@pytest.mark.unit
def test_nested_run_context_restores_outer_values() -> None:
    """A nested block overrides values, then the outer values come back on exit."""
    with run_context("outer-run", 1, "outer-node"):
        assert (get_run_id(), get_step(), get_node_id()) == ("outer-run", 1, "outer-node")

        with run_context("inner-run", 99, "inner-node"):
            assert (get_run_id(), get_step(), get_node_id()) == (
                "inner-run",
                99,
                "inner-node",
            )

        # Outer values must be restored exactly.
        assert (get_run_id(), get_step(), get_node_id()) == ("outer-run", 1, "outer-node")

    # And cleared on the outermost exit.
    assert get_run_id() is None
    assert get_step() is None
    assert get_node_id() is None


@pytest.mark.unit
def test_nested_run_context_emits_inner_then_outer_in_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Two log lines from nested blocks each carry the right correlation IDs."""
    log = get_logger("stargraph.test")

    with run_context("outer", 1, "n-out"):
        with run_context("inner", 2, "n-in"):
            log.info("inside")
        log.info("outside")

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) >= 2

    inside = json.loads(lines[-2])
    outside = json.loads(lines[-1])

    assert (inside["run_id"], inside["step"], inside["node_id"]) == ("inner", 2, "n-in")
    assert (outside["run_id"], outside["step"], outside["node_id"]) == ("outer", 1, "n-out")


@pytest.mark.unit
def test_run_context_resets_on_exception() -> None:
    """If the body raises, the ``finally`` clause still resets the ContextVars."""

    class _BoomError(Exception):
        pass

    with pytest.raises(_BoomError), run_context("r-err", 4, "n-err"):
        assert get_run_id() == "r-err"
        raise _BoomError

    assert get_run_id() is None
    assert get_step() is None
    assert get_node_id() is None
