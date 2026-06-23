# SPDX-License-Identifier: Apache-2.0
"""The planner — request → typed build manifest, via the configured LM.

Mirrors a smith's generation seam: a DSPy signature pins the output contract (a
JSON array of build items, exactly one ``graph`` spine), and ``default_planner``
is the live implementation the ``plan`` node uses when no planner is injected. The
node takes the planner as a constructor seam, so tests pin it to a deterministic
stub and the LM is the only nondeterministic part.
"""

from __future__ import annotations

import dspy  # pyright: ignore[reportMissingTypeStubs]

from stargraph.skills.foundry.manifest import BuildManifest, coerce

__all__ = ["PlanSignature", "default_planner"]


class PlanSignature(dspy.Signature):  # type: ignore[misc]
    """Decompose a Stargraph build request into a typed build manifest.

    Return a JSON array of build items. EXACTLY ONE item must have kind "graph" —
    the runnable spine (the nodes + state that do the work). Any other items are
    capabilities mounted around the spine: "store" (a document/vector store),
    "pack" (a Bosun CLIPS governance rule pack), "ml" (a model node), "tool" (a
    callable), or "adapter"/"trigger"/"skill"/"plugin"/"node". Each item is an
    object with: "kind", a short kebab-case "name", and a one-paragraph "brief"
    that is handed verbatim to that kind's smith as its build instruction.
    """

    request: str = dspy.InputField()  # pyright: ignore[reportUnknownMemberType]
    manifest_json: str = dspy.OutputField(  # pyright: ignore[reportUnknownMemberType]
        desc="JSON array of {kind, name, brief} build items"
    )


def default_planner(request: str) -> BuildManifest:
    """Decompose ``request`` into a validated manifest using the ambient DSPy LM.

    Assumes an LM is configured in scope (as for every smith's recall step).
    Raises on a malformed/invalid plan (the executor never runs an invalid plan).
    """
    prediction = dspy.Predict(PlanSignature)(request=request)
    return coerce(str(getattr(prediction, "manifest_json", "")))
