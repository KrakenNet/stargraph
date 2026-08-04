# SPDX-License-Identifier: Apache-2.0
"""``python -m stargraph.cli`` entry point -- mirrors the ``stargraph`` console script.

Lets callers invoke the CLI through a known interpreter without depending on the
console script being on ``PATH`` (used by ``ovarp verify --replay`` to drive
``stargraph ovarp-reproduce`` via ``sys.executable``).
"""

from __future__ import annotations

from stargraph.cli import main

if __name__ == "__main__":
    main()
