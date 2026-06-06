# SPDX-License-Identifier: Apache-2.0
"""Mirror lifecycle scheduler -- engine-side enforcer of ``Mirror.lifecycle`` (FR-2).

Foundation's :class:`stargraph.ir.Mirror` annotation tags state fields with
``lifecycle: Literal["run", "step", "pinned"]`` (FR-13, FR-14). Foundation only
declares the marker and resolves field-name templates via
:func:`stargraph.ir.mirrored_fields`; **the engine is responsible for enforcing
lifecycle semantics at runtime boundaries** (requirements glossary
``lifecycle (Mirror)``).

This module provides :class:`MirrorScheduler`, the engine's lifecycle bucket:

* ``schedule(specs, lifecycle=...)`` -- record :class:`fathom.AssertSpec`
  instances in the named lifecycle bucket. Called from the execution loop
  (design §3.1.2 step 3) after :meth:`stargraph.fathom.FathomAdapter.mirror_state`
  produces specs from the post-merge state.
* ``retract_step()`` -- clear the ``step`` bucket. Called at the node boundary
  (design §3.1.2 step 8) so step-scoped mirrors do not bleed across nodes.
* ``persist_pinned()`` -- flush the ``pinned`` bucket to the FactStore. Phase 3
  fills the FactStore call (knowledge spec) -- v1 ships a stub that records
  intent but does not persist.

The ``run`` bucket is held for the lifetime of the GraphRun and is cleared
neither at node boundary nor at run end (the engine is expected to drop the
scheduler instance with the run). v1 is in-memory only -- no checkpoint
restore is wired here; checkpoint integration is a Phase 3 concern.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import rfc8785

from stargraph.stores.fact import Fact

if TYPE_CHECKING:
    import fathom

    from stargraph.ir._mirror import Lifecycle

__all__ = ["MirrorScheduler"]


def _assert_spec_to_fact(
    spec: Any,
    *,
    run_id: str,
    step: int,
    user: str = "stargraph",
    agent: str = "engine",
) -> Fact:
    """Translate a ``fathom.AssertSpec`` into a :class:`stargraph.stores.fact.Fact`.

    Builds a deterministic ``id`` from JCS-canonical ``(template, slots)``,
    ``payload`` from slots, single-row ``lineage`` carrying
    ``(kind="mirror", run_id, step, template)``, ``confidence=1.0``,
    ``pinned_at=datetime.now(UTC)``.
    """
    canonical_input: dict[str, Any] = {"template": spec.template, "slots": spec.slots}
    fact_id = hashlib.sha256(rfc8785.dumps(canonical_input)).hexdigest()
    return Fact(
        id=fact_id,
        user=user,
        agent=agent,
        payload={"template": spec.template, **spec.slots},
        lineage=[{"kind": "mirror", "run_id": run_id, "step": step, "template": spec.template}],
        confidence=1.0,
        pinned_at=datetime.now(UTC),
    )


class MirrorScheduler:
    """In-memory lifecycle bucket for mirrored :class:`fathom.AssertSpec` instances.

    One instance per :class:`stargraph.GraphRun`. Buckets are append-only within a
    lifecycle (no de-duplication in v1 -- duplicate templates are the engine's
    responsibility to handle at assert time, since Fathom's deftemplate semantics
    treat each ``assert_fact`` as a fresh fact). The scheduler is the
    engine-side authority on **when** to retract or persist; it does not itself
    call into :class:`~stargraph.fathom.FathomAdapter` (the loop owns that
    interaction so back-pressure and ``asyncio.to_thread`` semantics stay
    visible at the call site).
    """

    __slots__ = ("_pinned", "_run", "_step")

    def __init__(self) -> None:
        self._run: list[fathom.AssertSpec] = []
        self._step: list[fathom.AssertSpec] = []
        self._pinned: list[fathom.AssertSpec] = []

    def schedule(self, specs: list[fathom.AssertSpec], lifecycle: Lifecycle) -> None:
        """Record ``specs`` in the bucket named by ``lifecycle``.

        ``lifecycle`` is the boundary scope at which these specs apply. The
        :class:`stargraph.ir.ResolvedMirror.lifecycle` field on each Mirror
        annotation determines which bucket the loop should pass for each
        field (resolution happens upstream in
        :func:`stargraph.ir.mirrored_fields`); this method is the bucket sink.
        """
        if lifecycle == "run":
            self._run.extend(specs)
        elif lifecycle == "step":
            self._step.extend(specs)
        else:  # "pinned"
            self._pinned.extend(specs)

    def retract_step(self) -> None:
        """Clear the ``step`` bucket -- called at every node boundary.

        Per requirements glossary ``lifecycle (Mirror)``: step-scoped mirrors
        must be retracted at the node boundary so they do not bleed across
        nodes. The execution loop (design §3.1.2 step 8) calls this after the
        transition event is emitted and the checkpoint write completes.
        """
        self._step.clear()

    async def persist_pinned(
        self,
        fact_store: Any,
        *,
        run_id: str,
        step: int,
    ) -> None:
        """Flush the ``pinned`` bucket to the FactStore.

        Calls :meth:`~stargraph.stores.fact.FactStore.pin` once per pinned spec.
        Raises :class:`stargraph.errors.StargraphRuntimeError` (FR-6 force-loud) if
        ``self._pinned`` is non-empty and ``fact_store`` is ``None``. Errors
        from ``pin`` propagate -- no swallowing.

        When ``self._pinned`` is empty this method returns immediately.
        """
        if not self._pinned:
            return
        if fact_store is None:
            from stargraph.errors import StargraphRuntimeError

            raise StargraphRuntimeError(
                "fact_store required when pinned specs are present",
                pinned_count=len(self._pinned),
            )
        for spec in self._pinned:
            fact = _assert_spec_to_fact(spec, run_id=run_id, step=step)
            await fact_store.pin(fact)
