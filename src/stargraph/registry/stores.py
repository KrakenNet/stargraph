# SPDX-License-Identifier: Apache-2.0
"""StoreRegistry -- in-memory store registry (FR-19, FR-22, design §3.16).

Populated at plugin-load time via the foundation's pluggy
``register_stores`` hook (:mod:`stargraph.plugin.hookspecs`). Each
:class:`stargraph.ir.StoreSpec` is registered by its globally unique
``name``; duplicate names raise :class:`NamespaceConflictError`
(stargraph-knowledge design §4.5: namespaces must be globally unique at
plugin-load time -- loud failure, no last-writer-wins).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from stargraph.errors import NamespaceConflictError

if TYPE_CHECKING:
    from stargraph.ir import StoreSpec

__all__ = ["StoreRegistry"]


class StoreRegistry:
    """In-memory store registry keyed by ``StoreSpec.name``.

    The registry exposes the surface ``list_stores`` / ``get_store`` /
    ``register`` documented in design §3.16. ``register`` raises
    :class:`NamespaceConflictError` if a name is claimed twice (the
    error carries ``namespace``, ``existing_owner`` and ``new_owner``
    so operators can resolve the conflict from the structured log).
    """

    def __init__(self) -> None:
        self._by_name: dict[str, StoreSpec] = {}
        self._owners: dict[str, str] = {}

    def register(self, spec: StoreSpec, *, owner: str) -> None:
        """Register ``spec`` under ``owner`` (the contributing dist name).

        Raises :class:`NamespaceConflictError` if another dist already
        registered a store with the same name.
        """
        prior_owner = self._owners.get(spec.name)
        if prior_owner is not None and prior_owner != owner:
            raise NamespaceConflictError(
                f"store namespace conflict: {spec.name!r} claimed by both "
                f"{prior_owner!r} and {owner!r}",
                namespace=spec.name,
                existing_owner=prior_owner,
                new_owner=owner,
            )
        self._by_name[spec.name] = spec
        self._owners[spec.name] = owner

    def list_stores(self) -> list[StoreSpec]:
        """Return all registered stores in insertion order."""
        return list(self._by_name.values())

    def get_store(self, name: str) -> StoreSpec:
        """Look up a store by name; raises :class:`NamespaceConflictError`-sibling on miss.

        A miss is :class:`KeyError` (not :class:`NamespaceConflictError`)
        because "unknown store" is a programmer error, not a registry
        invariant violation.
        """
        return self._by_name[name]
