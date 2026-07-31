# SPDX-License-Identifier: Apache-2.0
"""Lazy psycopg connection factory for the ``postgres`` pack (mock seam)."""

from __future__ import annotations

import importlib
from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.tools import _saas

__all__ = ["connect"]


def connect(*, read_only: bool, application_name: str) -> Any:
    """Open a psycopg connection from ``STARGRAPH_POSTGRES_DSN``.

    ``read_only=True`` sets ``default_transaction_read_only=on`` so the
    server rejects writes regardless of the SQL text. psycopg ships
    stubs, but the seam stays ``Any`` so tests can hand back a fake.
    Tools call this via the module (``_conn.connect(...)``).
    """
    dsn = _saas.require_env("STARGRAPH_POSTGRES_DSN", tool="postgres tools")
    try:
        psycopg = importlib.import_module("psycopg")
    except ImportError as exc:
        raise StargraphRuntimeError(
            "postgres tools require psycopg; install it with: pip install stargraph[tools-saas]",
        ) from exc
    options = f"-c application_name={application_name[:60]}"
    if read_only:
        options += " -c default_transaction_read_only=on"
    return psycopg.connect(dsn, options=options, autocommit=False)
