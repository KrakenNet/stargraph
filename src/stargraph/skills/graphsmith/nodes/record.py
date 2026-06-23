# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`GRAPH_SPEC`): on
success log the (brief → bundle) trainset pair + land the files; on failure log a
summary reflexion lesson.

Landing is fully driven by the spec: ``GRAPH_SPEC.bundle_files`` tells the shared
``SmithRecord._land`` to write the four-file bundle under ``output_dir/<stem>/`` and
``entry_file`` makes it return the runnable ``graph.yaml`` path — so no node-level
override is needed; everything flows through the shared lifecycle + ``GRAPH_SPEC``.
"""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.graphsmith.nodes.build import GRAPH_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=GRAPH_SPEC)
