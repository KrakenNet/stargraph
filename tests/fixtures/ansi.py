# SPDX-License-Identifier: Apache-2.0
"""Strip ANSI escape sequences from CLI output before asserting on it.

Under GitHub Actions, rich force-enables terminal rendering (it detects
the ``GITHUB_ACTIONS`` env var) and typer's option highlighter emits
style resets *inside* flag tokens -- ``--step`` renders as
``\\x1b[1;36m-\\x1b[0m\\x1b[1;36m-step\\x1b[0m`` -- so raw substring
assertions like ``"--step" in stdout`` fail in CI while passing locally.
``NO_COLOR`` is not sufficient: it suppresses color but rich still emits
bold/dim sequences that split the token.
"""

from __future__ import annotations

import re

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Return ``text`` with all ANSI SGR escape sequences removed."""
    return _ANSI_RE.sub("", text)
