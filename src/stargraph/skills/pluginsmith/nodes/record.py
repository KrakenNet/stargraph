# SPDX-License-Identifier: Apache-2.0
"""RecordBuild — terminal node (shared logic, bound to :data:`PLUGIN_SPEC`): on
success log the (brief → plugin) trainset pair + land the files; on failure log a
summary reflexion lesson.

Landing is fully driven by the spec: ``PLUGIN_SPEC.bundle_files`` tells the shared
``SmithRecord._land`` to write ``plugin.py`` + ``test_plugin.py`` with their fixed
names under ``output_dir/<stem>/`` (so the test's ``from plugin import …`` resolves)
and ``entry_file`` makes it return the registerable ``plugin.py`` path — so no
node-level override is needed; everything flows through the shared lifecycle +
``PLUGIN_SPEC``.
"""

from __future__ import annotations

from stargraph.skills._smith.nodes import SmithRecord
from stargraph.skills.pluginsmith.nodes.build import PLUGIN_SPEC


class RecordBuild(SmithRecord):
    def __init__(self) -> None:
        super().__init__(spec=PLUGIN_SPEC)
