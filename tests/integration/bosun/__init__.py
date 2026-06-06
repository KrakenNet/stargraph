# SPDX-License-Identifier: Apache-2.0
"""Integration tests for the four ``stargraph.bosun.*`` reference packs (Phase 4).

Each test file exercises the full pack-load → assert-facts → run-engine →
read-violations cycle for one pack. The shared helpers in :mod:`_helpers`
strip CLIPS comments + split the ``rules.clp`` file into individual
constructs (deftemplates + defrules) for raw-build via ``Engine._env.build``.

Why raw build instead of ``Engine.load_rules``: Fathom's ``load_rules`` is
YAML-driven (``ruleset``-shaped); these reference packs ship raw CLIPS
``.clp`` files because the rule authoring is direct (no module/hierarchy
indirection). The seam is supported by the engine — ``load_clips_function``
is its public entry point for arbitrary CLIPS source.
"""

from __future__ import annotations
