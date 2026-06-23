# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`ML_SPEC`): on success
log the (brief → trainer) trainset pair + land the files; on failure log a summary
reflexion lesson.

Landing is fully driven by the spec: ``ML_SPEC.bundle_files`` tells the shared
``SmithRecord._land`` to write ``trainer.py`` + ``test_trainer.py`` with their fixed
names under ``output_dir/<stem>/`` (so the test's ``from trainer import …`` resolves)
and ``entry_file`` makes it return the ``trainer.py`` path — so no node-level override
is needed; everything flows through the shared lifecycle + ``ML_SPEC``.
"""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.mlsmith.nodes.build import ML_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=ML_SPEC)
