# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.github`` -- GitHub REST tools (namespace ``github``).

| tool | capability | side effects |
|---|---|---|
| ``github.read_file@1`` | ``tools:github:read`` | read |
| ``github.list_issues@1`` | ``tools:github:read`` | read |
| ``github.create_issue@1`` | ``tools:github:write`` | write (dry-run by default) |

Plain HTTPS against ``https://api.github.com`` through the shared
:mod:`stargraph.tools._http` seam -- no SDK dependency. ``GITHUB_TOKEN``
is optional for reads (public repos work unauthenticated, rate-limited)
and required for writes. Writes follow the SaaS safety boundaries
(:mod:`stargraph.tools._saas`): dry-run unless ``STARGRAPH_GITHUB_LIVE``
is truthy, and real idempotency -- ``create_issue`` stamps
``stargraph-dedupe:<key>`` into the issue body as an HTML comment and
searches for it before creating, so a retried call returns the existing
issue instead of filing a duplicate.
"""

from __future__ import annotations

from stargraph.tools.github.create_issue import create_issue
from stargraph.tools.github.list_issues import list_issues
from stargraph.tools.github.read_file import read_file

__all__ = ["create_issue", "list_issues", "read_file"]
