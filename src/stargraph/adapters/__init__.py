# SPDX-License-Identifier: Apache-2.0
"""stargraph.adapters -- adapter seams for external runtimes (FR-5, FR-6, FR-25).

Phase-2/3 ships the DSPy seam (force-loud per design §3.3.1, FR-6) and the
MCP stdio seam (FR-25 v1). Both are deliberately thin -- the goal is to make
silent fallbacks impossible at the seam, not to abstract over runtimes.
"""

from __future__ import annotations

__all__: list[str] = []
