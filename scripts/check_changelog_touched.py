# SPDX-License-Identifier: Apache-2.0
"""Enforce CHANGELOG.md updates when IR or schema files change.

Computes the diff between two git refs and fails if any file under
``src/stargraph/ir/`` or ``src/stargraph/schemas/`` is touched without a
corresponding entry in ``CHANGELOG.md``.

Used by the ``changelog-check`` CI job (NFR-2).
"""

from __future__ import annotations

import argparse
import subprocess
import sys

GUARDED_PREFIXES = ("src/stargraph/ir/", "src/stargraph/schemas/")
CHANGELOG = "CHANGELOG.md"


def changed_files(base_ref: str, head_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}..{head_ref}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="HEAD~1")
    parser.add_argument("--head-ref", default="HEAD")
    args = parser.parse_args()

    files = changed_files(args.base_ref, args.head_ref)
    touched_guarded = [f for f in files if f.startswith(GUARDED_PREFIXES)]
    changelog_touched = CHANGELOG in files

    if touched_guarded and not changelog_touched:
        sys.stderr.write(
            "ERROR: IR/schema files changed without a CHANGELOG.md entry (NFR-2).\nTouched files:\n"
        )
        for f in touched_guarded:
            sys.stderr.write(f"  - {f}\n")
        sys.stderr.write(f"Add an entry to {CHANGELOG} describing the change.\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
