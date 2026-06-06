# SPDX-License-Identifier: Apache-2.0
"""Capability records + default-deny gate (NFR-7, design §3.11).

The engine's tool-execution path (task 1.27,
:mod:`stargraph.runtime.tool_exec`) calls :meth:`Capabilities.check`
before invoking any tool whose :attr:`stargraph.ir.ToolSpec.permissions`
list is non-empty. A tool's required permission is a string of the form
``"<name>"`` (unscoped) or ``"<name>:<scope>"`` (scope is a literal or
glob, e.g. ``"fs.read:/workspace/*"``).

Default-deny semantics (design §3.11):

* Tools declaring no required permissions always pass.
* When ``default_deny=False`` (dev): any tool with required permissions
  must have each requirement satisfied by a granted claim. An unscoped
  grant (``scope=None``) covers any scope of the same ``name``.
* When ``default_deny=True`` (cleared): unscoped grants are refused
  outright (``scope=None`` claims never match anything); every
  required permission must be matched by a *scoped* claim whose scope
  glob covers the request.

Both :class:`CapabilityClaim` and :class:`Capabilities` are frozen
Pydantic models so they're hashable (claim → ``set`` membership) and
immutable post-construction (defense-in-depth: a tool body cannot
mutate the granted set during execution).
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from stargraph.ir import ToolSpec

__all__ = [
    "Capabilities",
    "CapabilityClaim",
]


class CapabilityClaim(BaseModel):
    """A single granted permission (design §3.11).

    ``name`` is the capability namespace (``"fs.read"``, ``"net.fetch"``,
    ``"db.facts"``); ``scope`` is an optional glob restricting *what*
    resources the claim covers (``"/workspace/*"``,
    ``"https://api.example.com/*"``). ``scope=None`` means unscoped --
    accepted in dev (``default_deny=False``), refused in cleared
    deployments (``default_deny=True``) per design §3.11.
    """

    name: str
    scope: str | None = None

    model_config = ConfigDict(frozen=True)

    def __hash__(self) -> int:
        # Pydantic v2 ``frozen=True`` synthesizes ``__hash__`` at runtime, but
        # the type-checker can't see it; make it explicit so ``set[CapabilityClaim]``
        # type-checks under pyright strict mode.
        return hash((self.name, self.scope))


class Capabilities(BaseModel):
    """Default-deny capability gate (NFR-7, design §3.11).

    Threaded through :meth:`stargraph.Graph.start` and consulted by every
    tool-execution path. Frozen post-construction to prevent
    in-flight mutation.
    """

    default_deny: bool = False
    granted: set[CapabilityClaim] = Field(default_factory=set[CapabilityClaim])

    model_config = ConfigDict(frozen=True)

    def check(self, tool: ToolSpec) -> bool:
        """Return ``True`` iff every required permission of ``tool`` is granted.

        A tool with no declared permissions always passes (no gate to
        check). Otherwise every entry in
        :attr:`stargraph.ir.ToolSpec.permissions` must be matched by some
        :class:`CapabilityClaim` in :attr:`granted`.
        """
        if not tool.permissions:
            return True
        return all(self.has_permission(req) for req in tool.permissions)

    def has_permission(self, capability: str) -> bool:
        """Return ``True`` iff ``capability`` is covered by a granted claim.

        ``capability`` is parsed as ``"<name>:<scope>"`` (scope may be a
        literal path or glob); a missing colon means an unscoped
        request. Match rules:

        * Names must match exactly (no glob on ``name``).
        * Unscoped grants (``claim.scope is None``) cover any scope of
          the same name -- but only when ``default_deny=False``.
        * Scoped grants match when ``fnmatchcase(request_scope,
          claim.scope)`` is true; an unscoped *request* against a
          scoped grant is rejected (the request must be at least as
          specific as the grant).
        """
        req_name, _, req_scope = capability.partition(":")
        # ``partition`` returns ``("name", "", "")`` when no colon present;
        # normalize the empty-string scope back to ``None`` so the
        # "unscoped request" branch below is unambiguous.
        req_scope_or_none = req_scope or None

        for claim in self.granted:
            if claim.name != req_name:
                continue
            if claim.scope is None:
                # Unscoped grant: refused outright in cleared deployments
                # (design §3.11 "unscoped grants refused"); otherwise
                # covers any scope of the same name.
                if self.default_deny:
                    continue
                return True
            # Scoped grant: an unscoped request is too broad to match.
            if req_scope_or_none is None:
                continue
            if fnmatchcase(req_scope_or_none, claim.scope):
                return True
        return False
