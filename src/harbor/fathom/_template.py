# SPDX-License-Identifier: Apache-2.0
"""``harbor_action`` deftemplate definition and idempotent registration (FR-3, AC-7.1).

The deftemplate slots mirror the design.md ``harbor.fathom`` contract: ``kind``
constrains the action vocabulary to Harbor's six verbs, with structural slots
(``target``, ``targets``, ``join``, ``strategy``, ``backoff_ms``) and reflective
slots (``fact``, ``slots``, ``pattern``) covering every Action variant. Native
Fathom decisions (allow/deny/escalate/scope/route) are translated by the
adapter; this template is the Harbor-side surface only.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import fathom

__all__ = ["HARBOR_ACTION_DEFTEMPLATE", "register_harbor_action_template"]


HARBOR_ACTION_DEFTEMPLATE: str = """\
(deftemplate harbor_action
  (slot kind (type SYMBOL) (allowed-symbols goto parallel halt retry assert retract interrupt))
  (slot target (type STRING) (default ""))
  (slot reason (type STRING) (default ""))
  (slot rule_id (type STRING) (default ""))
  (slot step (type INTEGER) (default 0))
  (multislot targets (type STRING))
  (slot join (type STRING) (default ""))
  (slot strategy (type SYMBOL) (allowed-symbols all any race quorum) (default all))
  (slot backoff_ms (type INTEGER) (default 0))
  (slot fact (type STRING) (default ""))
  (slot slots (type STRING) (default ""))
  (slot pattern (type STRING) (default ""))
  (slot prompt (type STRING) (default ""))
  (slot interrupt_payload (type STRING) (default ""))
  (slot requested_capability (type STRING) (default ""))
  (slot timeout (type STRING) (default ""))
  (slot on_timeout (type STRING) (default "halt")))
"""


_registered_engines: weakref.WeakSet[fathom.Engine] = weakref.WeakSet()


def register_harbor_action_template(engine: fathom.Engine) -> None:
    """Register ``harbor_action`` on ``engine`` exactly once per engine identity.

    Idempotent: subsequent calls with the same engine are no-ops. Registration
    state is tracked in a module-level :class:`weakref.WeakSet`, so engines are
    not pinned in memory beyond their natural lifetime.
    """
    if engine in _registered_engines:
        return
    engine.load_clips_function(HARBOR_ACTION_DEFTEMPLATE)
    _registered_engines.add(engine)
