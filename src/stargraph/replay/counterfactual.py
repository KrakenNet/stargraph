# SPDX-License-Identifier: Apache-2.0
"""Counterfactual mutation builder + cf-derived graph hash (FR-27, FR-56).

Per design §3.8.2-§3.8.3, §4.5, and FR-27 amendment 6.

* :class:`CounterfactualMutation` -- typed Pydantic builder for the six
  fields a caller may override at the cf fork point: ``state_overrides``,
  ``facts_assert``, ``facts_retract``, ``rule_pack_version``,
  ``node_output_overrides``, and ``respond_payloads`` (the HITL
  counterfactual lever per FR-56 / design §4.5 / locked Decision #2).
  ``model_config = ConfigDict(extra="forbid")`` rejects unknown keys
  at construction time so typos are loud.

* :func:`derived_graph_hash` -- domain-separated hash of the *original*
  ``graph_hash`` plus the JCS-canonical mutation. Per design §3.8.3
  Learning E, the byte sequence is::

      sha256(
          b"stargraph-cf-v1"                     # 12-byte domain tag
          + b"\\x00"                          # 1-byte separator
          + original_hash.encode("ascii")     # 64 bytes hex digest
          + b"\\x00"                          # 1-byte separator
          + rfc8785.dumps(mutation.model_dump(exclude_none=True, mode="json"))
      )

  The 12-byte tag prefix in the *byte sequence* (not in the on-the-wire
  hex digest) provides domain separation: any cf-derived hash is
  distinguishable from a vanilla structural hash even when the original
  graph and mutation collide. The on-the-wire artifact is the 64-char
  hex sha256 digest -- the literal ``b"stargraph-cf-v1"`` prefix lives only
  inside the pre-image. ``respond_payloads`` participates in the
  pre-image like every other field, so two cf-mutations that differ
  only in the override payload at step N produce distinct cf-hashes
  (replay determinism per NFR-4).

* :func:`apply_respond_override` -- helper that resolves the cassette/
  override decision at a :class:`~stargraph.runtime.events.WaitingForInputEvent`
  step. Default (no override): returns ``None`` and the caller cassette-
  replays the original respond payload. Override (mutation has a payload
  for ``step_n``): returns ``(payload, source)`` where
  ``source = "cf:<actor>"``. The ``cf:`` prefix is the audit filter
  marker (locked Decision #2): replay machinery and audits filter on it
  to distinguish cf-authored respond facts from original-actor ones.

Capability ownership (FR-56, design §4.5): the cf override path
*deliberately* bypasses ``runs:respond``. The original live respond
fact already carried ``runs:respond`` at live time; the cf submitter
authors the override under ``runs:counterfactual`` (Phase-2 ratelimit
gate, locked Decision #6 -- shared per-actor in-memory token bucket).
The cf-respond fact's provenance documents the cf submitter as the
source via the ``cf:<actor>`` prefix.
"""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785
from pydantic import BaseModel, ConfigDict

__all__ = [
    "CF_HASH_DOMAIN_TAG",
    "CF_RESPOND_SOURCE_PREFIX",
    "CounterfactualMutation",
    "apply_respond_override",
    "derived_graph_hash",
]


# 12-byte domain-separation tag prefixed into the cf-derived hash pre-image
# per design §3.8.3 Learning E. Mirrors the literal in
# ``stargraph.graph.run._CF_HASH_PREFIX`` (used by the resume-time refusal in
# AC-3.4 / FR-27); the two are kept lex-identical by convention.
CF_HASH_DOMAIN_TAG: bytes = b"stargraph-cf-v1"


# Provenance ``source`` prefix for cf-authored respond facts (locked
# Decision #2). The full source string is ``f"{CF_RESPOND_SOURCE_PREFIX}{actor}"``
# (e.g. ``"cf:alice@example.com"``). Replay machinery + audits filter on
# this prefix to distinguish cf-authored respond evidence from the
# original analyst's live-time respond.
CF_RESPOND_SOURCE_PREFIX: str = "cf:"


class CounterfactualMutation(BaseModel):
    """Typed builder for the six FR-27/FR-56 cf-fork mutation fields (design §3.8.2, §4.5).

    All fields default to ``None`` so the empty mutation is a valid no-op
    builder (useful for "what does this run look like under cf-replay
    semantics, with no semantic change" probes -- the derived hash still
    differs from the original by virtue of the domain-separation tag).

    Attributes:
        state_overrides: Plain ``dict[str, Any]`` -- merged into the
            checkpoint state at the cf step, replacing matching keys.
            Mixed value types are intentional (the runtime state schema
            is not known here; type-checking happens downstream when the
            mutated state is fed into the next node).
        facts_assert: Optional list of CLIPS-style fact records to
            ``assert`` at the cf step (rule-engine substrate, FR-27).
        facts_retract: Optional list of CLIPS-style fact records to
            ``retract`` at the cf step.
        rule_pack_version: Optional rule-pack semver string -- pins the
            mutated run to a different rule-pack snapshot than the
            original (design §3.8.4 step 3).
        node_output_overrides: Plain ``dict[str, Any]`` keyed by
            ``node_id`` -- the value at the cf-step replaces the
            recorded output for that node (design §3.8.4 step 6).
        respond_payloads: Optional ``dict[int, dict[str, Any]]`` --
            maps ``step_n`` (the run-step at which the original live
            run emitted a :class:`~stargraph.runtime.events.WaitingForInputEvent`)
            to a respond payload dict. The override replaces the
            cassette-recorded analyst response for that step; the
            cf-replay engine asserts a fresh ``stargraph.evidence`` fact
            with ``origin="user"`` + ``source="cf:<actor>"`` (FR-56,
            design §4.5, locked Decision #2). The ``<actor>`` is taken
            from the cf submitter's authenticated
            :class:`~stargraph.serve.auth.AuthContext` at cf-request time;
            see :func:`apply_respond_override` for the resolver helper.
            The payload shape mirrors the live ``POST /runs/{id}/respond``
            body (raw JSON dict, locked Decision #2).
    """

    model_config = ConfigDict(extra="forbid")

    state_overrides: dict[str, Any] | None = None
    facts_assert: list[dict[str, Any]] | None = None
    facts_retract: list[dict[str, Any]] | None = None
    rule_pack_version: str | None = None
    node_output_overrides: dict[str, Any] | None = None
    respond_payloads: dict[int, dict[str, Any]] | None = None


def derived_graph_hash(original_hash: str, mutation: CounterfactualMutation) -> str:
    """Return the cf-derived 64-char hex sha256 digest (design §3.8.3).

    Pre-image byte sequence (Learning E)::

        b"stargraph-cf-v1" + b"\\x00"
        + original_hash.encode("ascii") + b"\\x00"
        + rfc8785.dumps(mutation.model_dump(exclude_none=True, mode="json"))

    Args:
        original_hash: The 64-char hex ``graph_hash`` of the parent run.
        mutation: The :class:`CounterfactualMutation` builder describing
            the cf fork. Canonicalized via RFC 8785 (JCS) so the hash is
            insensitive to dict-key insertion order.

    Returns:
        Lowercase 64-char hex sha256 digest of the JCS-canonical pre-image.
    """
    canonical = rfc8785.dumps(mutation.model_dump(exclude_none=True, mode="json"))
    h = hashlib.sha256()
    h.update(CF_HASH_DOMAIN_TAG)
    h.update(b"\x00")
    h.update(original_hash.encode("ascii"))
    h.update(b"\x00")
    h.update(canonical)
    return h.hexdigest()


def apply_respond_override(
    mutation: CounterfactualMutation,
    step_n: int,
    actor: str,
) -> tuple[dict[str, Any], str] | None:
    """Resolve cassette/override decision at a ``WaitingForInputEvent`` step (FR-56, FR-86).

    The cf-replay engine consults this helper at each step that the
    original live run emitted a :class:`~stargraph.runtime.events.WaitingForInputEvent`:

    * **Default (no override)**: the mutation has no entry for
        ``step_n`` -- returns ``None``. The caller cassette-replays the
        original respond payload from the audit log; the replay does NOT
        wait for an ``awaiting-input`` round-trip (the recorded fact is
        the durable artifact, not a live block).

    * **Override**: ``mutation.respond_payloads[step_n]`` is present --
        returns ``(payload, source)`` where ``source = "cf:<actor>"``.
        The caller asserts a fresh ``stargraph.evidence`` fact with
        ``origin="user"`` + ``source=<source>`` carrying the override
        payload in the ``data`` slot (locked Decision #2 shape:
        raw JSON dict, no envelope).

    Per locked Decision #2, the ``cf:`` prefix on ``source`` is the
    audit filter marker. Replay machinery + audits filter on it to
    distinguish cf-authored respond evidence from the original
    analyst's live respond.

    Capability ownership (FR-56, design §4.5): the override path
    *deliberately* bypasses ``runs:respond``. The original live respond
    fact already carried ``runs:respond`` at live time (HTTP API gate,
    Phase 1 task 1.23); the cf submitter authors the override under
    ``runs:counterfactual`` (Phase-2 ratelimit gate, locked Decision #6).

    Args:
        mutation: The :class:`CounterfactualMutation` driving the
            cf-replay.
        step_n: The run-step at which the original emitted a
            :class:`~stargraph.runtime.events.WaitingForInputEvent`.
        actor: The cf submitter's authenticated principal id (taken
            from :class:`~stargraph.serve.auth.AuthContext.actor`).

    Returns:
        ``None`` when no override applies (caller cassette-replays).
        ``(payload, "cf:<actor>")`` when the mutation overrides at
        ``step_n``.
    """
    if mutation.respond_payloads is None:
        return None
    payload = mutation.respond_payloads.get(step_n)
    if payload is None:
        return None
    source = f"{CF_RESPOND_SOURCE_PREFIX}{actor}"
    return payload, source
