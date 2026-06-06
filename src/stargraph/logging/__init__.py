# SPDX-License-Identifier: Apache-2.0
"""Stargraph logging — structlog with ContextVar correlation.

This package shadows the stdlib ``logging`` package within Stargraph's namespace.
Internal modules avoid ``import logging`` and use structlog directly.
"""

from ._config import get_logger
from ._context import run_context

__all__ = ["get_logger", "run_context"]
