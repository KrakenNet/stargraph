# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`SKILL_SPEC`): on
success log the (brief → skill) trainset pair + land the files; on failure log a
summary reflexion lesson.

Landing is fully driven by the spec: ``SKILL_SPEC.bundle_files`` tells the shared
``SmithRecord._land`` to write the five-file bundle under ``output_dir/<stem>/`` and
``entry_file`` makes it return the runnable ``manifest.yaml`` path (the skill's
registration entry point) — so no node-level override is needed; everything flows
through the shared lifecycle + ``SKILL_SPEC``.
"""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.skillsmith.nodes.build import SKILL_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=SKILL_SPEC)
