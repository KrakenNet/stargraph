# SPDX-License-Identifier: Apache-2.0
r"""Network-FS prefix detector for SQLite WAL safety (design §3.2.3).

WAL on network filesystems (NFS / SMB / AFP) is documented-unsafe by upstream
SQLite: the locking primitives the WAL implementation relies on are not
honored across network mounts, leading to silent corruption. Stargraph refuses
to bootstrap a SQLite checkpointer on a path matching any of the known
network-FS prefixes; the user must use a local-FS path or switch to the
Postgres driver.

Prefixes matched (per design §3.2.3, FR-17 verbatim amendment 4):

* ``^/mnt/``                -- common Linux NFS / SMB mount root
* ``^//``                   -- POSIX UNC-style network path
* ``\\\\``                  -- Windows UNC path (``\\host\share``)
* ``^/Volumes/.+(SMB|AFP)`` -- macOS network mount whose share name carries
  the protocol marker
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["is_network_fs"]


# Compiled at import time -- the regex set is small and the detector is
# called once per ``bootstrap()``.
_NETWORK_FS_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/mnt/"),
    re.compile(r"^//"),
    re.compile(r"^\\\\"),
    re.compile(r"^/Volumes/.+(SMB|AFP)"),
)


def is_network_fs(path: Path) -> bool:
    """Return ``True`` if ``path`` matches any known network-FS prefix.

    The check operates on ``str(path)`` so callers can pass a
    :class:`pathlib.Path` constructed from either POSIX or Windows-style
    input strings -- ``Path('\\\\\\\\host\\\\share\\\\db')`` round-trips to
    ``\\\\host\\share\\db`` on POSIX and is matched by the UNC pattern.
    """
    s = str(path)
    return any(p.search(s) for p in _NETWORK_FS_PATTERNS)
