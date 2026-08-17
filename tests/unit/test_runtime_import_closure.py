# SPDX-License-Identifier: Apache-2.0
"""``import stargraph`` must not reach for a test-only distribution.

Every test job installs the ``dev`` dependency group, so a module-level
``import vcr`` (or any other dev-group package) on a runtime code path is
invisible to the entire suite: it resolves fine here and raises
``ModuleNotFoundError`` for anyone who installed the published wheel. That is
exactly how ``stargraph.replay.determinism`` -- pulled in by
``stargraph.runtime.dispatch`` on the core run path -- shipped a hard
``import vcr`` and made ``import stargraph`` itself unimportable outside a
development checkout.

The guard below is deliberately narrow. It does not assert that every imported
distribution is a declared runtime dependency: a dev checkout also has the
optional extras installed, and their transitive packages (``h2``, ``PIL``,
``brotli``) legitimately appear in the closure without being required. It
asserts only the thing that broke -- that nothing in the closure belongs to a
distribution declared *exclusively* in ``[dependency-groups]``.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from importlib import metadata
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _canonical(requirement: str) -> str:
    """PEP 503 name of ``requirement``, dropping any version spec/extra/marker."""
    return re.split(r"[<>=!~\[; ]", requirement.strip())[0].lower().replace("_", "-")


def _dev_only_distributions() -> set[str]:
    """Distributions declared in a dependency group and nowhere else.

    A package that is *also* a runtime dependency or an extra (``duckdb`` and
    ``readability-lxml`` are both) is legitimately importable and excluded.
    """
    pyproject = tomllib.loads(_PYPROJECT.read_text())
    project = pyproject["project"]
    runtime = {_canonical(r) for r in project.get("dependencies", [])}
    extras = {
        _canonical(r)
        for requirements in project.get("optional-dependencies", {}).values()
        for r in requirements
    }
    groups = {
        _canonical(r)
        for requirements in pyproject.get("dependency-groups", {}).values()
        for r in requirements
    }
    return groups - runtime - extras


def _import_closure() -> list[str]:
    """Top-level module names loaded by a bare ``import stargraph``.

    Runs in a subprocess so the closure is stargraph's own, not one polluted by
    pytest and its plugins.
    """
    code = (
        "import stargraph, sys, json; "
        "print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    modules: list[str] = json.loads(completed.stdout)
    return modules


def test_importing_stargraph_pulls_no_dev_only_distribution() -> None:
    """A dev-group package on the import path means the wheel is broken."""
    dev_only = _dev_only_distributions()
    packages = metadata.packages_distributions()
    offenders = {
        module: distribution
        for module in _import_closure()
        for distribution in packages.get(module, [])
        if _canonical(distribution) in dev_only
    }
    assert not offenders, (
        f"`import stargraph` loads {offenders!r}; each is declared only in "
        "[dependency-groups], so it is absent from the published wheel. Move the "
        "import inside the function that uses it."
    )


def test_the_guard_can_see_a_dev_only_distribution() -> None:
    """Anti-vacuity: the classifier really does flag a dev-group package.

    Without this, a bug in :func:`_dev_only_distributions` (an empty set, a
    normalization slip) would leave the guard above passing on every input.
    """
    dev_only = _dev_only_distributions()
    assert "vcrpy" in dev_only
    assert "pytest" in dev_only
    assert "duckdb" not in dev_only  # dev group *and* the ``tools`` extra
