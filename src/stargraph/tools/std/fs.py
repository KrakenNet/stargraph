# SPDX-License-Identifier: Apache-2.0
"""``std.file_read`` / ``std.file_write`` / ``std.file_list`` -- jailed FS access.

All three resolve paths through :func:`stargraph.tools.std._jail.resolve_jailed`:
relative paths land under ``$STARGRAPH_TOOLS_FS_ROOT`` (default: the process
working directory) and anything escaping the jail -- ``..`` traversal,
absolute paths outside it, symlinks pointing out -- is a loud
:class:`~stargraph.errors.StargraphRuntimeError`.

``file_write`` is ``side_effects=write`` so its replay policy defaults to
``must-stub`` (FR-21); the jail, not a capability, is its guard -- writing
inside the graph's own workspace is the batteries-included default.
"""

from __future__ import annotations

from typing import Any

from stargraph.errors import StargraphRuntimeError
from stargraph.tools.decorator import tool
from stargraph.tools.spec import SideEffects
from stargraph.tools.std._jail import fs_root, resolve_jailed

__all__ = ["file_list", "file_read", "file_write"]

_MAX_READ_BYTES = 1_000_000
_MAX_LIST_ENTRIES = 500


@tool(
    name="file_read",
    namespace="std",
    version="1",
    side_effects=SideEffects.read,
    description=(
        "Read a UTF-8 text file inside the tools filesystem jail "
        "(STARGRAPH_TOOLS_FS_ROOT, default: working directory)."
    ),
)
def file_read(path: str, max_bytes: int = _MAX_READ_BYTES) -> dict[str, Any]:
    """Return ``{path, content, truncated}`` for a file inside the jail."""
    target = resolve_jailed(path)
    if not target.is_file():
        raise StargraphRuntimeError(f"file {path!r} does not exist", path=path)
    max_bytes = max(1, min(max_bytes, _MAX_READ_BYTES))
    raw = target.read_bytes()
    truncated = len(raw) > max_bytes
    content = raw[:max_bytes].decode("utf-8", errors="replace")
    return {"path": str(target), "content": content, "truncated": truncated}


@tool(
    name="file_write",
    namespace="std",
    version="1",
    side_effects=SideEffects.write,
    description=(
        "Write (or append) UTF-8 text to a file inside the tools filesystem "
        "jail; parent directories are created as needed."
    ),
)
def file_write(path: str, content: str, append: bool = False) -> dict[str, Any]:
    """Write ``content`` to a jailed path; return ``{path, bytes_written}``."""
    target = resolve_jailed(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    with target.open("ab" if append else "wb") as fh:
        fh.write(data)
    return {"path": str(target), "bytes_written": len(data)}


@tool(
    name="file_list",
    namespace="std",
    version="1",
    side_effects=SideEffects.read,
    description=("List directory entries (non-recursive glob) inside the tools filesystem jail."),
)
def file_list(path: str = ".", pattern: str = "*") -> dict[str, Any]:
    """Return ``{path, entries, truncated}`` for a jailed directory."""
    target = resolve_jailed(path)
    if not target.is_dir():
        raise StargraphRuntimeError(f"directory {path!r} does not exist", path=path)
    root = fs_root()
    entries: list[dict[str, Any]] = []
    truncated = False
    for child in sorted(target.glob(pattern)):
        # A glob like ``../*`` could match outside the jail; skip escapees.
        if child != root and not child.resolve().is_relative_to(root):
            continue
        if len(entries) >= _MAX_LIST_ENTRIES:
            truncated = True
            break
        entries.append(
            {
                "name": child.name,
                "is_dir": child.is_dir(),
                "size": child.stat().st_size if child.is_file() else 0,
            }
        )
    return {"path": str(target), "entries": entries, "truncated": truncated}
