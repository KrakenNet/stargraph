# SPDX-License-Identifier: Apache-2.0
"""Public ``stargraph.ir`` surface: models, Mirror marker, canonical dumps/loads."""

from __future__ import annotations

from ._backfill import backfill_rule_node_ids
from ._dumps import dumps, dumps_canonical, loads
from ._mirror import Lifecycle, Mirror, ResolvedMirror, mirrored_fields
from ._models import (
    Action,
    AssertAction,
    CheckpointBlock,
    FactTemplate,
    GotoAction,
    HaltAction,
    IRBase,
    IRDocument,
    MigrateBlock,
    NodeSpec,
    PackMount,
    PackRequires,
    ParallelAction,
    ParallelBlock,
    PluginManifest,
    RetractAction,
    RetryAction,
    RuleSpec,
    SkillRef,
    SkillSpec,
    SlotDef,
    StoreRef,
    StoreSpec,
    ToolRef,
    ToolSpec,
)
from ._validate import validate

__all__ = [
    "Action",
    "AssertAction",
    "CheckpointBlock",
    "FactTemplate",
    "GotoAction",
    "HaltAction",
    "IRBase",
    "IRDocument",
    "Lifecycle",
    "MigrateBlock",
    "Mirror",
    "NodeSpec",
    "PackMount",
    "PackRequires",
    "ParallelAction",
    "ParallelBlock",
    "PluginManifest",
    "ResolvedMirror",
    "RetractAction",
    "RetryAction",
    "RuleSpec",
    "SkillRef",
    "SkillSpec",
    "SlotDef",
    "StoreRef",
    "StoreSpec",
    "ToolRef",
    "ToolSpec",
    "backfill_rule_node_ids",
    "dumps",
    "dumps_canonical",
    "loads",
    "mirrored_fields",
    "validate",
]
