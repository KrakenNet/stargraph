# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.artifacts -- artifact-writing built-in nodes (FR-92, design §10.3).

Public surface: :class:`WriteArtifactNode` + :class:`WriteArtifactNodeConfig`.
The node persists state-resident bytes through a configured
:class:`stargraph.artifacts.ArtifactStore` and emits an
:class:`~stargraph.runtime.events.ArtifactWrittenEvent` on success.
"""

from __future__ import annotations

from stargraph.nodes.artifacts.write_artifact_node import (
    WriteArtifactContext,
    WriteArtifactNode,
    WriteArtifactNodeConfig,
)

__all__ = [
    "WriteArtifactContext",
    "WriteArtifactNode",
    "WriteArtifactNodeConfig",
]
