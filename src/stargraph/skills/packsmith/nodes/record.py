# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`PACK_SPEC`): on success
log the (brief → rules.clp) trainset pair + land the files; on failure log a summary
reflexion lesson.

Landing is fully driven by the spec: ``PACK_SPEC.bundle_files`` tells the shared
``SmithRecord._land`` to write ``rules.clp`` + ``pack.yaml`` + ``manifest.yaml`` +
``test_pack.py`` with their fixed names under ``output_dir/<stem>/`` (so the test's
``Path(__file__).with_name("rules.clp")`` resolves) and ``entry_file`` makes it return
the ``pack.yaml`` path a deployer points at — so no node-level override is needed.
"""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.packsmith.nodes.build import PACK_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=PACK_SPEC)
