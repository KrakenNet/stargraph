# SPDX-License-Identifier: Apache-2.0
"""Filesystem root jail shared by the ``std`` filesystem-touching tools.

``std.file_read`` / ``std.file_write`` / ``std.file_list`` /
``std.sql_query`` resolve every user-supplied path against a single jail
root and refuse anything that escapes it (``..`` traversal and symlink
hops included -- paths are fully resolved before the containment check).

The root comes from ``STARGRAPH_TOOLS_FS_ROOT``; unset means the process
working directory. There is no "unjailed" mode.
"""

from __future__ import annotations

import os
from pathlib import Path

from stargraph.errors import StargraphRuntimeError

__all__ = ["fs_root", "resolve_jailed"]

_ROOT_ENV = "STARGRAPH_TOOLS_FS_ROOT"


def fs_root() -> Path:
    """The jail root: ``$STARGRAPH_TOOLS_FS_ROOT`` or the current directory."""
    raw = os.environ.get(_ROOT_ENV, "").strip()
    return (Path(raw) if raw else Path.cwd()).resolve()


def resolve_jailed(path: str) -> Path:
    """Resolve ``path`` inside the jail; raise if it escapes.

    Relative paths resolve against the jail root; absolute paths are
    accepted only when they already live inside it. Symlinks are followed
    *before* the containment check, so a link pointing outside the jail is
    rejected too.
    """
    root = fs_root()
    raw = Path(path)
    candidate = (raw if raw.is_absolute() else root / raw).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise StargraphRuntimeError(
            f"path {path!r} escapes the tools filesystem jail",
            path=path,
            root=str(root),
            hint=f"set {_ROOT_ENV} to widen the jail",
        )
    return candidate
