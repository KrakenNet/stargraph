# SPDX-License-Identifier: Apache-2.0
"""Stargraph artifacts namespace (FR-90 .. FR-97, design §10).

Public surface: :class:`ArtifactStore` Protocol + :class:`ArtifactRef`
Pydantic record. Concrete providers (``FilesystemArtifactStore``) land
in :mod:`stargraph.artifacts.fs` in task 1.13; provider discovery reuses
the existing ``stargraph.stores`` plugin entry-point group (FR-97, design
§10.5).
"""

from __future__ import annotations

from stargraph.artifacts.base import ArtifactRef, ArtifactStore

__all__ = ["ArtifactRef", "ArtifactStore"]
