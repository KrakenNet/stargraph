# SPDX-License-Identifier: Apache-2.0
"""Structlog configuration with ContextVar-based correlation injection.

Note: this module lives under ``stargraph.logging`` which shadows the stdlib
``logging`` package within Stargraph's namespace. Avoid ``import logging`` here —
use :mod:`structlog` directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from ._context import get_node_id, get_run_id, get_step

if TYPE_CHECKING:
    from collections.abc import MutableMapping

_configured = False


def _inject_context(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """Read correlation ContextVars and add them to every log event."""
    run_id = get_run_id()
    if run_id is not None:
        event_dict.setdefault("run_id", run_id)
    step = get_step()
    if step is not None:
        event_dict.setdefault("step", step)
    node_id = get_node_id()
    if node_id is not None:
        event_dict.setdefault("node_id", node_id)
    return event_dict


def _configure() -> None:
    global _configured
    if _configured:
        return
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            _inject_context,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(0),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> Any:
    """Return a configured structlog logger.

    Configures structlog once on first call (idempotent).
    """
    _configure()
    return structlog.get_logger(name) if name is not None else structlog.get_logger()
