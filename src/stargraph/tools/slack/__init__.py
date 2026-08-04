# SPDX-License-Identifier: Apache-2.0
"""``stargraph.tools.slack`` -- Slack Web API tools (namespace ``slack``).

| tool | capability | side effects |
|---|---|---|
| ``slack.post_message@1`` | ``tools:slack:write`` | write (dry-run by default) |
| ``slack.read_messages@1`` | ``tools:slack:read`` | read |

Plain HTTPS against ``https://slack.com/api`` through the shared
:mod:`stargraph.tools._http` client seam -- no ``slack_sdk`` dependency.
Auth is a bot token in ``SLACK_BOT_TOKEN``. Writes follow the SaaS
safety boundaries (:mod:`stargraph.tools._saas`): dry-run unless
``STARGRAPH_SLACK_LIVE`` is truthy, and a required ``dedupe_key``.
Slack has no server-side idempotency, so the key is stamped into the
message ``metadata`` (``event_type: stargraph_dedupe``) for audit and
duplicate detection by consumers.
"""

from __future__ import annotations

from stargraph.tools.slack.post_message import post_message
from stargraph.tools.slack.read_messages import read_messages

__all__ = ["post_message", "read_messages"]
