# SPDX-License-Identifier: Apache-2.0
"""TDD-RED→GREEN: ``WaitingForInputEvent`` cassette/override semantics (FR-86, AC-14.7, NFR-4).

Pins the cf-replay decision rule at a step that emitted a
:class:`~stargraph.runtime.events.WaitingForInputEvent`:

1. **Default (no override)**: ``mutation.respond_payloads`` is ``None``
   or has no entry for ``step_n`` -- the helper returns ``None`` and the
   caller is expected to cassette-replay the original recorded respond
   payload (no live ``awaiting-input`` round-trip).

2. **Override**: ``mutation.respond_payloads[step_n]`` is present -- the
   helper returns ``(payload, "cf:<actor>")``. The ``cf:`` prefix is
   the locked Decision #2 audit-filter marker.

3. **Determinism (NFR-4)**: same mutation + same original graph_hash
   produce the same cf-derived graph_hash, so replay is byte-stable.
   Two mutations differing only in ``respond_payloads[step_n]`` produce
   *different* cf-hashes (the override participates in the cf-hash
   pre-image).

The tests deliberately do not depend on a step-by-step replay engine
(none exists yet -- see :meth:`stargraph.GraphRun.counterfactual` which
only applies ``state_overrides`` at the fork step). They pin the
helper contract that a future cf-replay engine will consume.
"""

from __future__ import annotations

from stargraph.replay import (
    CounterfactualMutation,
    apply_respond_override,
    derived_graph_hash,
)
from stargraph.replay.counterfactual import CF_RESPOND_SOURCE_PREFIX


def test_no_override_returns_none_for_cassette_replay() -> None:
    """No ``respond_payloads`` entry -> helper returns ``None`` (cassette path)."""
    mutation = CounterfactualMutation()
    result = apply_respond_override(mutation, step_n=5, actor="alice@example.com")
    assert result is None


def test_no_override_at_this_step_returns_none() -> None:
    """``respond_payloads`` is set but missing ``step_n`` -> still ``None``."""
    mutation = CounterfactualMutation(respond_payloads={3: {"approve": True}})
    result = apply_respond_override(mutation, step_n=5, actor="alice@example.com")
    assert result is None


def test_override_at_step_returns_payload_and_cf_actor_source() -> None:
    """Override entry -> ``(payload, "cf:<actor>")`` per locked Decision #2."""
    mutation = CounterfactualMutation(
        respond_payloads={5: {"decision": "reject", "reason": "out-of-scope"}},
    )
    result = apply_respond_override(mutation, step_n=5, actor="alice@example.com")
    assert result is not None
    payload, source = result
    assert payload == {"decision": "reject", "reason": "out-of-scope"}
    assert source == "cf:alice@example.com"
    # Locked Decision #2 invariant: source carries the ``cf:`` prefix
    # so audit filters can distinguish cf-authored respond evidence.
    assert source.startswith(CF_RESPOND_SOURCE_PREFIX)


def test_override_payload_is_raw_dict_not_envelope() -> None:
    """Override payload mirrors live ``POST /respond`` body shape (locked Decision #2)."""
    raw_response = {"decision": "approve", "comments": "looks good"}
    mutation = CounterfactualMutation(respond_payloads={2: raw_response})
    result = apply_respond_override(mutation, step_n=2, actor="ops")
    assert result is not None
    payload, _ = result
    # The payload is the raw dict, not wrapped (no envelope, no
    # serialization). Downstream cf-replay asserts a ``stargraph.evidence``
    # fact with ``data=<this dict>`` -- same shape as live ``respond``.
    assert payload is raw_response or payload == raw_response


def test_cf_hash_deterministic_for_same_mutation() -> None:
    """Same mutation + same original hash -> same cf-derived hash (NFR-4)."""
    original_hash = "0" * 64
    mutation_a = CounterfactualMutation(
        respond_payloads={5: {"decision": "reject"}},
    )
    mutation_b = CounterfactualMutation(
        respond_payloads={5: {"decision": "reject"}},
    )
    h_a = derived_graph_hash(original_hash, mutation_a)
    h_b = derived_graph_hash(original_hash, mutation_b)
    assert h_a == h_b


def test_cf_hash_differs_when_respond_payload_differs() -> None:
    """Different override payloads -> different cf-derived hashes (replay determinism)."""
    original_hash = "0" * 64
    mutation_reject = CounterfactualMutation(
        respond_payloads={5: {"decision": "reject"}},
    )
    mutation_approve = CounterfactualMutation(
        respond_payloads={5: {"decision": "approve"}},
    )
    h_reject = derived_graph_hash(original_hash, mutation_reject)
    h_approve = derived_graph_hash(original_hash, mutation_approve)
    assert h_reject != h_approve, (
        "respond_payloads must participate in the cf-hash pre-image; "
        "differing override payloads must yield differing cf-hashes"
    )


def test_cf_hash_differs_when_step_index_differs() -> None:
    """Same payload at different ``step_n`` -> different cf-hashes."""
    original_hash = "0" * 64
    mutation_a = CounterfactualMutation(
        respond_payloads={3: {"decision": "reject"}},
    )
    mutation_b = CounterfactualMutation(
        respond_payloads={5: {"decision": "reject"}},
    )
    assert derived_graph_hash(original_hash, mutation_a) != derived_graph_hash(
        original_hash, mutation_b
    )


def test_override_does_not_mutate_input_dict() -> None:
    """Helper is a pure read; mutation's ``respond_payloads`` is unchanged."""
    original_payload = {"decision": "reject"}
    mutation = CounterfactualMutation(respond_payloads={5: original_payload})
    apply_respond_override(mutation, step_n=5, actor="alice")
    # Pydantic deep-copies on construction, so the original dict isn't
    # the same object -- but the field still equals the input.
    assert mutation.respond_payloads is not None
    assert mutation.respond_payloads[5] == {"decision": "reject"}
