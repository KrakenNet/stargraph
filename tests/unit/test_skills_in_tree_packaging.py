# SPDX-License-Identifier: Apache-2.0
"""In-tree reference-skill packaging contract (FR-36, AC-7.4).

Pins three guarantees the design relies on:

1. The three reference-skill modules (``rag``, ``autoresearch``, ``wiki``)
   are importable under ``stargraph.skills.refs.*`` -- this is the FR-32/33/34
   public surface.
2. The package directory is locatable via :mod:`importlib.resources` so
   wheel-installed users (not just editable installs) can introspect it.
3. The three subgraph-IR fixture YAMLs ship at the
   ``tests/fixtures/skills/<name>/example.yaml`` paths the skill
   docstrings reference (design §3.10/§3.11/§3.12).
"""

from __future__ import annotations

import importlib
from importlib.resources import files
from pathlib import Path

import pytest

pytestmark = [pytest.mark.knowledge, pytest.mark.unit]


_REFS_MODULES = (
    "stargraph.skills.refs.rag",
    "stargraph.skills.refs.autoresearch",
    "stargraph.skills.refs.wiki",
)

_FIXTURE_NAMES = ("rag", "autoresearch", "wiki")


@pytest.mark.parametrize("module_name", _REFS_MODULES)
def test_reference_skill_modules_importable(module_name: str) -> None:
    """Each reference-skill module imports cleanly from the in-tree package."""
    module = importlib.import_module(module_name)
    assert module.__name__ == module_name


def test_refs_package_resource_root_accessible() -> None:
    """``stargraph.skills.refs`` resolves via :mod:`importlib.resources`.

    Pins the wheel-install path: the package must be reachable through
    the resources API, not just by editable-install filesystem lookup.
    """
    root = files("stargraph.skills.refs")
    init_resource = root.joinpath("__init__.py")
    assert init_resource.is_file()

    expected_files = {"rag.py", "autoresearch.py", "wiki.py", "__init__.py"}
    present = {entry.name for entry in root.iterdir() if entry.is_file()}
    missing = expected_files - present
    assert not missing, f"missing reference-skill modules: {sorted(missing)}"


def test_fixture_yamls_present() -> None:
    """The three subgraph-IR fixture YAMLs exist at their documented paths."""
    repo_root = Path(__file__).resolve().parents[2]
    fixtures_root = repo_root / "tests" / "fixtures" / "skills"
    for name in _FIXTURE_NAMES:
        fixture = fixtures_root / name / "example.yaml"
        assert fixture.is_file(), f"missing fixture YAML: {fixture}"
