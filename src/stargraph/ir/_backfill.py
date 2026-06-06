# SPDX-License-Identifier: Apache-2.0
"""Backward-compatibility helpers for the IR.

Currently exposes :func:`backfill_rule_node_ids`, which infers
``RuleSpec.node_id`` (added in IR 1.1.0) from the canonical
``(node-id (id <NAME>))`` pattern in a rule's ``when`` clause for
documents that omit the field. Older 1.0.0 documents and many
hand-authored 1.1.0 rules don't declare ``node_id`` directly; this
helper lets downstream tools (the StarGraph topology endpoint,
``stargraph inspect``) treat ``node_id`` as effectively non-null.

Lives outside :mod:`stargraph.ir._models` because the IR models forbid
``computed_field`` / ``model_validator`` decorators (FR-7, AC-13.1 --
the IR is code-as-data and must not carry hidden Python behavior).
This module is opt-in: callers explicitly invoke the function on a
loaded :class:`~stargraph.ir.IRDocument`.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._models import IRDocument

__all__ = ["NODE_ID_PATTERN", "backfill_rule_node_ids"]


# Match ``(node-id (id NAME))`` capturing NAME. Tolerant of whitespace
# and the binding prefix ``?var <-``. NAME is the Stargraph stable-id
# grammar (alnum, underscore, hyphen, dot).
NODE_ID_PATTERN = re.compile(r"\(node-id\s+\(id\s+([A-Za-z0-9_.\-]+)\s*\)\s*\)")


def backfill_rule_node_ids(doc: IRDocument) -> IRDocument:
    """Fill ``RuleSpec.node_id`` from the ``when`` pattern for unset rules.

    Mutates ``doc`` in-place and returns it for chaining. Only rules with
    ``node_id is None`` are touched; explicit declarations are preserved.
    Rules whose ``when`` does not match :data:`NODE_ID_PATTERN`, or whose
    extracted name is not a node id in the document, keep ``None`` --
    downstream tools treat that as "ownership unknown".
    """
    node_ids = {n.id for n in doc.nodes}
    for rule in doc.rules:
        if rule.node_id is not None:
            continue
        m = NODE_ID_PATTERN.search(rule.when)
        if m and m.group(1) in node_ids:
            rule.node_id = m.group(1)
    return doc
