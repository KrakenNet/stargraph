# SPDX-License-Identifier: Apache-2.0
"""Assert version strings are synchronised across release-critical files.

Reads ``__version__`` from ``src/stargraph/__init__.py``, ``project.version``
from ``pyproject.toml``, and the latest ``## [X.Y.Z]`` heading in
``CHANGELOG.md``. Exits non-zero if the three values disagree.

Used by the release pipeline to satisfy AC-2.6.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INIT_PATH = REPO_ROOT / "src" / "stargraph" / "__init__.py"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)
_CHANGELOG_RE = re.compile(r"^##\s*\[([^\]]+)\]", re.MULTILINE)


def read_init_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if match is None:
        raise RuntimeError(f"could not find __version__ in {path}")
    return match.group(1)


def read_pyproject_version(path: Path) -> str:
    with path.open("rb") as f:
        data = tomllib.load(f)
    project = data.get("project")
    if not isinstance(project, dict):
        raise RuntimeError(f"missing [project] table in {path}")
    version = project.get("version")
    if not isinstance(version, str):
        raise RuntimeError(f"missing project.version in {path}")
    return version


def read_changelog_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = _CHANGELOG_RE.search(text)
    if match is None:
        raise RuntimeError(f"no '## [X.Y.Z]' heading found in {path}")
    return match.group(1)


def main() -> int:
    init_version = read_init_version(INIT_PATH)
    pyproject_version = read_pyproject_version(PYPROJECT_PATH)
    changelog_version = read_changelog_version(CHANGELOG_PATH)

    versions = {
        "src/stargraph/__init__.py": init_version,
        "pyproject.toml": pyproject_version,
        "CHANGELOG.md": changelog_version,
    }

    if len(set(versions.values())) != 1:
        sys.stderr.write("ERROR: version strings are out of sync (AC-2.6).\n")
        for source, value in versions.items():
            sys.stderr.write(f"  {source}: {value}\n")
        return 1

    sys.stdout.write(f"version sync OK: {init_version}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
