# SPDX-License-Identifier: Apache-2.0
"""Stargraph's Fathom adapter surface.

Re-exports the public adapter (:class:`FathomAdapter`), provenance bundle
type, action dataclasses, and the ``stargraph_action`` template registrar.
"""

from ._action import (
    Action,
    AssertAction,
    GotoAction,
    HaltAction,
    ParallelAction,
    RetractAction,
    RetryAction,
    extract_actions,
)
from ._adapter import FathomAdapter
from ._provenance import ProvenanceBundle
from ._template import STARGRAPH_ACTION_DEFTEMPLATE, register_stargraph_action_template

__all__ = [
    "STARGRAPH_ACTION_DEFTEMPLATE",
    "Action",
    "AssertAction",
    "FathomAdapter",
    "GotoAction",
    "HaltAction",
    "ParallelAction",
    "ProvenanceBundle",
    "RetractAction",
    "RetryAction",
    "extract_actions",
    "register_stargraph_action_template",
]
