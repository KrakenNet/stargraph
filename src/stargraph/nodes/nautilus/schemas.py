# SPDX-License-Identifier: Apache-2.0
"""stargraph.nodes.nautilus.schemas -- JSON Schemas for the broker request/response surface.

Re-exports :func:`nautilus.BrokerResponse.model_json_schema` so the
Stargraph tool/registry surface can publish the broker response shape
without hand-rolling a duplicate definition.

Nautilus does NOT expose a ``BrokerRequest`` Pydantic model: the
broker's :meth:`Broker.arequest` accepts ``agent_id``, ``intent``,
``context``, and ``fact_set_hash`` as plain keyword arguments. The
"request schema" exposed here is therefore synthesised from those
parameters via :class:`pydantic.create_model` -- it documents the
public call shape downstream tooling can probe (e.g. registry-driven
introspection, OpenAPI generation in the serve layer).
"""

from __future__ import annotations

from typing import Any

from nautilus import BrokerResponse  # pyright: ignore[reportMissingTypeStubs]
from pydantic import TypeAdapter, create_model

__all__ = ["broker_request_schema", "broker_response_schema"]


def broker_response_schema() -> dict[str, Any]:
    """Return the JSON Schema for :class:`nautilus.BrokerResponse` (serialization mode).

    ``mode="serialization"`` emits the schema for the *output* shape --
    i.e. what callers receive after :meth:`Broker.arequest` resolves.
    This matches the contract registry consumers expect.
    """
    return BrokerResponse.model_json_schema(mode="serialization")


def broker_request_schema() -> dict[str, Any]:
    """Return a JSON Schema describing :meth:`Broker.arequest` keyword arguments.

    Synthesised because nautilus 0.1.4 has no public ``BrokerRequest``
    Pydantic model. Fields mirror the broker's keyword parameters so
    downstream registry tooling has a stable input contract.
    """
    request_model = create_model(
        "BrokerRequestArgs",
        agent_id=(str, ...),
        intent=(str, ...),
        context=(dict[str, Any] | None, None),
        fact_set_hash=(str | None, None),
    )
    return TypeAdapter(request_model).json_schema()
