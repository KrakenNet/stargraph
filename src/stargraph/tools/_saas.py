# SPDX-License-Identifier: Apache-2.0
"""Shared safety helpers for the SaaS tool packs (slack/github/s3/email/postgres).

Every SaaS write tool follows the ServiceNow safety boundaries
(:mod:`stargraph.tools.servicenow`):

1. **Dry-run by default** -- :func:`live_enabled` reads the pack's
   ``STARGRAPH_<NS>_LIVE`` env var; unless truthy the tool returns a
   synthetic envelope and never touches the network.
2. **Caller-supplied idempotency** -- :func:`require_dedupe_key`
   rejects empty keys; each pack documents how the key dedupes (or, where
   the provider has no server-side dedupe, how it is stamped for audit).
3. **Capability gates** -- every SaaS tool (reads included: they touch
   private data) requires ``tools:<ns>:read`` / ``tools:<ns>:write``.
"""

from __future__ import annotations

import os

from stargraph.errors import StargraphRuntimeError

__all__ = ["dry_run_envelope", "live_enabled", "require_dedupe_key", "require_env"]

_TRUTHY = ("1", "true", "yes", "on")


def live_enabled(namespace: str) -> bool:
    """``True`` iff ``STARGRAPH_<NAMESPACE>_LIVE`` is set to a truthy string."""
    return os.environ.get(f"STARGRAPH_{namespace.upper()}_LIVE", "").strip().lower() in _TRUTHY


def require_dedupe_key(value: str, tool: str) -> str:
    """Return the stripped dedupe key; reject empty/whitespace loudly."""
    key = value.strip()
    if not key:
        raise StargraphRuntimeError(
            f"{tool} requires a non-empty dedupe_key for idempotent retries",
        )
    return key


def require_env(name: str, *, tool: str) -> str:
    """Read a required env var, raising with the tool name when unset."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise StargraphRuntimeError(
            f"{tool} requires the {name} environment variable",
            hint=f"export {name}=... before running live",
        )
    return value


def dry_run_envelope(namespace: str, dedupe_key: str, request_body: object) -> dict[str, object]:
    """The synthetic result every gated write returns when not live."""
    return {
        "status": "dry-run",
        "request_body": request_body,
        "__stargraph_provenance__": {
            "origin": "tool",
            "source": namespace,
            "external_id": f"dry-run:{dedupe_key}",
        },
    }
