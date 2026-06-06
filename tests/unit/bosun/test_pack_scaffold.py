# SPDX-License-Identifier: Apache-2.0
"""Unit: in-tree Bosun pack scaffold (task 2.39).

Per FR-34 / FR-35 / FR-36 / FR-37 / AC-3.1 / AC-3.2 / design §3.1, §7.1,
§14.1: ships 4 reference Bosun packs in-tree as structural scaffold
(manifests + minimal CLIPS only). Phase 4 fills the actual rule logic
+ production-key signing.

These tests verify the scaffold:

* Each pack directory has the canonical 4 files (``__init__.py``,
  ``manifest.yaml``, ``rules.clp``, ``manifest.jwt``).
* Each ``manifest.yaml`` parses via ``yaml.safe_load`` and contains
  the expected ``id``, ``version``, and ``requires`` keys.
* The 4 packs are discoverable via filesystem traversal of
  ``src/stargraph/bosun/`` (the canonical path until Phase 4 wires
  pluggy-based discovery).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BOSUN_DIR = _REPO_ROOT / "src" / "stargraph" / "bosun"

_PACK_NAMES = ("budgets", "audit", "safety_pii", "retries")


@pytest.mark.unit
@pytest.mark.parametrize("pack_name", _PACK_NAMES)
def test_pack_dir_has_required_files(pack_name: str) -> None:
    """Each Bosun pack scaffold dir contains the canonical 4 files."""
    pack_dir = _BOSUN_DIR / pack_name
    assert pack_dir.is_dir(), f"missing pack dir: {pack_dir}"
    assert (pack_dir / "__init__.py").is_file()
    assert (pack_dir / "manifest.yaml").is_file()
    assert (pack_dir / "rules.clp").is_file()
    assert (pack_dir / "manifest.jwt").is_file()


@pytest.mark.unit
@pytest.mark.parametrize("pack_name", _PACK_NAMES)
def test_pack_manifest_parses_with_expected_shape(pack_name: str) -> None:
    """Each ``manifest.yaml`` parses + has the locked-shape keys."""
    manifest_path = _BOSUN_DIR / pack_name / "manifest.yaml"
    with manifest_path.open("rb") as fh:
        data = yaml.safe_load(fh)
    assert isinstance(data, dict)
    assert data["id"] == f"stargraph.bosun.{pack_name}"
    assert data["version"] == "1.0"
    assert isinstance(data["requires"], dict)
    assert data["requires"]["stargraph_facts_version"] == "1.0"
    assert data["requires"]["api_version"] == "1"


@pytest.mark.unit
def test_pack_discovery_finds_four_packs() -> None:
    """Filesystem-level discovery finds exactly the 4 in-tree packs.

    Walks ``src/stargraph/bosun/`` for any subdir containing ``manifest.yaml``
    -- the contract for "this is a Bosun pack scaffold". Phase 4 wires
    real pluggy-based discovery; for this task, file-existence is the
    contract.
    """
    discovered = sorted(
        p.name for p in _BOSUN_DIR.iterdir() if p.is_dir() and (p / "manifest.yaml").is_file()
    )
    assert set(discovered) == set(_PACK_NAMES), discovered
