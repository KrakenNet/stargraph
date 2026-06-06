# SPDX-License-Identifier: Apache-2.0
"""OpenAPI 3.1 generation for the Stargraph serve surface (design §5.3).

This module ships :func:`regen_openapi_spec` -- a single entry point the
FastAPI app factory wires into ``app.openapi`` so ``GET /openapi.json``
and ``GET /docs`` (Swagger UI) emit the augmented spec by default. The
augmentation is **additive**: FastAPI's stock
:func:`fastapi.openapi.utils.get_openapi` produces a complete OpenAPI
3.1 spec from the route signatures alone; we then merge the stargraph IR
Pydantic models (``IRDocument``, ``RunSummary``, ``ArtifactRef``,
``Event``, ``BrokerResponse``) directly into ``components/schemas`` so
clients consuming ``/openapi.json`` see the IR types reachable by name
(the route signatures only reference these IR models indirectly via
``response_model``, so without the merge the components map would
contain inlined / mangled variants).

Why merge instead of declare-on-routes-only:

* The IR ``Event`` type is a Pydantic discriminated union annotated
  with ``Field(discriminator="type")`` -- declaring it as a response
  model would inline 16 separate schemas under the route's response
  body and lose the named-component shape clients want for client SDK
  generation. ``pydantic.TypeAdapter(Event).json_schema()`` returns the
  canonical ``oneOf`` + ``discriminator`` shape we want under
  ``components/schemas/Event``.
* ``BrokerResponse`` lives in the ``nautilus_rkm`` distribution
  (top-level ``nautilus`` package). It's not used by any stargraph route
  signature directly (the broker is consumed inside graph nodes, not
  exposed via HTTP), but clients integrating with stargraph want the
  schema reachable for typed dispatch on broker results. Merging it
  here documents the contract without forcing a route to take it.
* ``IRDocument``, ``RunSummary``, ``ArtifactRef`` are referenced by
  routes already (``GET /v1/runs``, ``GET /v1/runs/{id}/artifacts``,
  etc.) but FastAPI's stock generator names them with Pydantic v2's
  ``__name__``-derived component key; explicitly merging the
  ``model_json_schema(mode="serialization")`` output guarantees the
  canonical name (``IRDocument`` not ``IRDocument-Output``) and the
  serialization-mode (vs validation-mode) shape -- aligning with
  ``stargraph/schemas/ir-v1.json``.

Pattern: ``fastapi.openapi.utils.get_openapi(...) +
deep_dict_update(spec, ir_pydantic_components)``. ``separate_input_output_schemas=True``
(Pydantic v2) so the stock generator emits clean Input/Output schemas
where the field shapes diverge (e.g. computed fields, default
factories).

Design refs: §5.3 (OpenAPI 3.1 generation pattern). FR-12, AC-7.4.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from fastapi.openapi.utils import get_openapi
from pydantic import TypeAdapter

from stargraph.artifacts.base import ArtifactRef
from stargraph.checkpoint.protocol import RunSummary
from stargraph.ir._models import IRDocument
from stargraph.runtime.events import Event

if TYPE_CHECKING:
    from fastapi import FastAPI


__all__ = ["deep_dict_update", "regen_openapi_spec"]


# --- Helpers ----------------------------------------------------------------


def deep_dict_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Recursively merge ``source`` into ``target`` in place.

    Later values win on conflict at every level. Dicts are descended
    into; non-dict values are replaced wholesale. Used to merge the IR
    Pydantic component dict into the FastAPI-emitted
    ``components/schemas`` map without clobbering FastAPI's
    route-derived entries (``HTTPValidationError`` etc.).

    The merge is left-biased on missing keys (``target`` keeps any key
    not present in ``source``) and right-biased on overlap. Lists are
    treated as opaque values (replaced, not concatenated) since the
    only list under ``components/schemas`` is each schema's required
    list and the IR-Pydantic form is the source of truth where it
    overlaps.
    """
    for key, src_val in source.items():
        tgt_val = target.get(key)
        if isinstance(tgt_val, dict) and isinstance(src_val, dict):
            deep_dict_update(
                cast("dict[str, Any]", tgt_val),
                cast("dict[str, Any]", src_val),
            )
        else:
            target[key] = src_val


def _ir_component_schemas() -> dict[str, dict[str, Any]]:
    """Return the stargraph IR Pydantic models as ``{name: schema}``.

    Each schema is the model's ``model_json_schema(mode="serialization")``
    output (or ``TypeAdapter(...).json_schema(mode="serialization")`` for
    the discriminated-union ``Event`` alias which is not a class). The
    returned dict is meant to be merged under
    ``spec["components"]["schemas"]`` so each model is reachable by
    name from the OpenAPI components map.

    ``BrokerResponse`` is imported lazily from the optional
    ``nautilus_rkm`` distribution (top-level package ``nautilus``); if
    the package is absent at runtime we skip just that one schema and
    let the rest of the merge proceed. Stargraph's ``pyproject.toml`` pins
    ``nautilus_rkm==0.1.2`` so the import normally succeeds; the
    fallback is for stripped composition tests (design §16.10) that
    remove the dep to verify zero-import-coupling.
    """
    schemas: dict[str, dict[str, Any]] = {
        "IRDocument": IRDocument.model_json_schema(mode="serialization"),
        "RunSummary": RunSummary.model_json_schema(mode="serialization"),
        "ArtifactRef": ArtifactRef.model_json_schema(mode="serialization"),
        "Event": TypeAdapter(Event).json_schema(mode="serialization"),
    }
    try:
        from nautilus import BrokerResponse  # pyright: ignore[reportMissingTypeStubs]
    except ImportError:
        # Composition test path: nautilus removed -> skip the schema.
        # Documented gap; the other 4 schemas still merge cleanly.
        return schemas
    schemas["BrokerResponse"] = BrokerResponse.model_json_schema(
        mode="serialization",
    )
    return schemas


# --- Public API -------------------------------------------------------------


def regen_openapi_spec(app: FastAPI) -> dict[str, Any]:
    """Generate the augmented OpenAPI 3.1 spec for ``app``.

    Builds the base spec via :func:`fastapi.openapi.utils.get_openapi`
    then merges the stargraph IR Pydantic component schemas under
    ``components/schemas`` so the IR types are reachable by name. The
    result is a plain dict suitable for direct return from the
    ``/openapi.json`` route or for serialization to
    ``docs/reference/openapi.json`` by ``scripts/regen_openapi.py``
    (Phase 4 task).

    Parameters
    ----------
    app:
        The FastAPI application built by :func:`stargraph.serve.api.create_app`.
        Reads ``app.title``, ``app.version``, ``app.description``,
        ``app.routes``, ``app.webhooks``, and ``app.openapi_tags`` /
        ``app.servers`` so the generated spec matches the same metadata
        FastAPI's stock generator would produce.

    Returns
    -------
    dict[str, Any]
        The augmented OpenAPI 3.1 spec. ``spec["openapi"]`` is
        ``"3.1.0"`` (FastAPI 0.115+ default with Pydantic v2);
        ``spec["components"]["schemas"]`` contains every FastAPI-derived
        schema plus the 5 IR-Pydantic schemas reachable by name.

    Notes
    -----
    * ``separate_input_output_schemas=True`` is the Pydantic v2 default
      for ``get_openapi`` and produces clean separate input/output
      shapes where they diverge (computed fields, default factories);
      we pass it explicitly to pin the contract.
    * FastAPI's ``get_openapi`` returns a fresh dict on every call
      (no caching). Pinning ``app.openapi = lambda: regen_openapi_spec(app)``
      makes ``app.openapi()`` cache-aware via FastAPI's stock
      ``app.openapi_schema`` slot -- but our wiring deliberately
      bypasses that cache (the IR component merge is cheap and the
      cache hides regeneration when routes are added at test time).
    """
    spec = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=app.openapi_version,
        summary=app.summary,
        description=app.description,
        routes=app.routes,
        webhooks=app.webhooks.routes,
        tags=app.openapi_tags,
        servers=app.servers,
        separate_input_output_schemas=True,
    )
    components: dict[str, Any] = spec.setdefault("components", {})
    schemas: dict[str, Any] = components.setdefault("schemas", {})
    deep_dict_update(schemas, _ir_component_schemas())
    return spec
