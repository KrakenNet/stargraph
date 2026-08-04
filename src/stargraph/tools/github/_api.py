# SPDX-License-Identifier: Apache-2.0
"""Shared request plumbing for the ``github`` pack."""

from __future__ import annotations

import os
import re

from stargraph.errors import StargraphRuntimeError

__all__ = ["API_BASE", "check_repo", "headers"]

API_BASE = "https://api.github.com"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")


def check_repo(repo: str) -> str:
    """Validate the ``owner/name`` shape before it is interpolated into a URL."""
    if not _REPO_RE.match(repo):
        raise StargraphRuntimeError(
            f"repo must be 'owner/name', got {repo!r}",
            repo=repo,
        )
    return repo


def headers(*, require_token: bool, tool: str) -> dict[str, str]:
    """GitHub REST headers; bearer auth when ``GITHUB_TOKEN`` is set.

    Reads work unauthenticated on public repos (rate-limited); writes
    pass ``require_token=True`` and fail loudly without one.
    """
    out = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        out["Authorization"] = f"Bearer {token}"
    elif require_token:
        raise StargraphRuntimeError(
            f"{tool} requires the GITHUB_TOKEN environment variable",
            hint="export GITHUB_TOKEN=... (a fine-grained PAT with issues:write)",
        )
    return out
