# SPDX-License-Identifier: Apache-2.0
"""Structured plan-spec schema for the cve_remediation planner.

Phase F (2026-05-11): the planner historically emitted a free-text
``plan_rationale`` and CodeWriterNode reverse-engineered an Ansible
bundle from it. The freeform rationale was the dominant source of
rollback flakiness on CVEs like CVE-2015-2425 and CVE-2024-38475 —
the LM produced apply tasks that the rollback LM could not reliably
invert.

This module defines a 4-tuple structured plan (apply / verify /
rollback / regression) with a Pydantic validator so the planner emits
a machine-checkable contract. CodeWriterNode consumes the spec and
generates the bundle deterministically when the spec is complete;
the LM bundle path remains as fallback when the spec is partial.

A spec is `complete` iff all four steps have an `intent` and at least
one of {primitive, target, action_ref}. A spec is `valid` iff every
populated step references a citation_url present in
``allowed_citations`` (passed by caller) OR the spec is marked
``honest_skip`` with a non-empty ``deficit_reasons``.

The Deficit enum is consumed by CriticNode to emit a structured
``critic_deficits`` list that the planner reads on retry.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# Deficit kinds shared between CriticNode and PlannerNode.
DeficitKind = Literal[
    "missing_apply",
    "missing_verify_probe",
    "missing_rollback",
    "missing_regression",
    "non_invertible_rollback",
    "version_unspecified",
    "fabricated_citation",
    "unsafe_primitive",
    "incomplete_spec",
]


class PlanStep(BaseModel):
    """One step of the 4-tuple plan (apply / verify / rollback / regression).

    `intent` is a short verb phrase the LM would have written; it
    surfaces to operators. `primitive` is the deterministic primitive
    name when one applies (upgrade, downgrade, isolate, disable,
    quarantine, hold_package, block_port, set_env_var,
    set_config_directive, mitigation, mitigation_only). `target` is
    package@version or service-name or file-path. `action_ref` is the
    index into recommended_actions when the step is derived from a
    discovered action (so CodeWriter can reuse the action's citation
    without re-fetching).
    """

    intent: str = ""
    primitive: str = ""
    target: str = ""
    target_version: str = ""
    action_ref: int = -1
    cite_url: str = ""

    def is_specified(self) -> bool:
        return bool(self.intent) and bool(
            self.primitive or self.target or self.action_ref >= 0
        )


class PlanSpec(BaseModel):
    """Structured 4-tuple plan emitted by PlannerNode.

    Empty default = no spec available (CodeWriter falls back to LM
    bundle path). ``honest_skip=True`` + ``deficit_reasons`` set
    documents WHY no spec could be built (e.g. mitigation_only with
    no advisory IoCs); CodeWriter then emits an honest-skip bundle.
    """

    apply: PlanStep = Field(default_factory=PlanStep)
    verify: PlanStep = Field(default_factory=PlanStep)
    rollback: PlanStep = Field(default_factory=PlanStep)
    regression: PlanStep = Field(default_factory=PlanStep)

    honest_skip: bool = False
    deficit_reasons: list[str] = Field(default_factory=list)
    schema_version: int = 1

    def is_complete(self) -> bool:
        return all(
            step.is_specified()
            for step in (self.apply, self.verify, self.rollback, self.regression)
        )

    def deficits(self) -> list[dict[str, str]]:
        """Enumerate which 4-tuple slots are unspecified.

        Returns deficit dicts in the shape CriticNode also emits, so a
        downstream caller can merge planner self-deficits with
        critic_deficits without conversion.
        """
        out: list[dict[str, str]] = []
        slots: list[tuple[str, str, PlanStep]] = [
            ("missing_apply", "apply", self.apply),
            ("missing_verify_probe", "verify", self.verify),
            ("missing_rollback", "rollback", self.rollback),
            ("missing_regression", "regression", self.regression),
        ]
        for kind, slot_name, step in slots:
            if not step.is_specified():
                out.append({"kind": kind, "slot": slot_name, "detail": ""})
        return out


# ---------------------------------------------------------------------------
# Derivation: build PlanSpec deterministically from extract + recommended_actions
# ---------------------------------------------------------------------------


_INVERTIBLE_PRIMITIVES: frozenset[str] = frozenset({
    "upgrade",
    "downgrade",
    "disable",
    "isolate",
    "quarantine",
    "hold_package",
    "block_port",
    "set_env_var",
    "set_config_directive",
})


def derive_plan_spec(
    *,
    cve_id: str,
    cwe: str,
    vuln_class: str,
    fixed_version: str,
    recommended_actions: list[Any],
    unpatchable_disposition: str = "",
) -> PlanSpec:
    """Build a PlanSpec from upstream signals deterministically.

    Strategy (no LM in this path):

    1. Pick the highest-confidence actionable recommended_action whose
       kind is invertible (upgrade, downgrade, isolate, disable,
       quarantine, hold_package, block_port, set_env_var,
       set_config_directive).
    2. apply.primitive = that kind; target/target_version from the
       action; cite_url from action.citation_url.
    3. rollback mirrors apply with primitive inverted. For upgrade,
       rollback is downgrade-to-prior version (target_version="" if
       unknown — emit ``non_invertible_rollback`` deficit). For
       isolate/disable/quarantine, rollback is the spec's natural
       inverse.
    4. verify.primitive = "probe"; intent="check installed version <
       fixed_version" for version actions, "check service stopped"
       for disable, "check port blocked" for block_port, etc.
    5. regression is a fixed canary intent — "run service-level
       healthcheck after apply".

    Returns a PlanSpec with honest_skip=True + deficit_reasons when no
    actionable action was found (e.g. all kinds were "mitigation_only"
    or "force_hitl").
    """

    actions = [a for a in (recommended_actions or []) if a]
    spec = PlanSpec()

    # Filter to invertible primitives, sorted by confidence_bp desc.
    invertible: list[tuple[int, Any]] = []
    for a in actions:
        kind = str(getattr(a, "kind", "") or "").strip().lower()
        if kind in _INVERTIBLE_PRIMITIVES:
            conf = int(getattr(a, "confidence_bp", 0) or 0)
            invertible.append((conf, a))
    invertible.sort(key=lambda x: x[0], reverse=True)

    if not invertible:
        # Honest-skip: no actionable primitive. Document why.
        reasons: list[str] = []
        if not actions:
            reasons.append("no_recommended_actions")
        else:
            kinds = sorted({
                str(getattr(a, "kind", "") or "").strip().lower()
                for a in actions if a
            })
            reasons.append(
                f"no_invertible_primitive (kinds={kinds})"
            )
        if unpatchable_disposition:
            reasons.append(f"unpatchable:{unpatchable_disposition}")
        spec.honest_skip = True
        spec.deficit_reasons = reasons
        return spec

    _, primary = invertible[0]
    primary_kind = str(getattr(primary, "kind", "") or "").strip().lower()
    target = str(getattr(primary, "target", "") or "")
    target_version = str(getattr(primary, "target_version", "") or "")
    cite = str(getattr(primary, "citation_url", "") or "")
    action_index = actions.index(primary) if primary in actions else -1

    # --- apply ---
    spec.apply = PlanStep(
        intent=f"{primary_kind} {target}"
        + (f"@{target_version}" if target_version else ""),
        primitive=primary_kind,
        target=target,
        target_version=target_version,
        action_ref=action_index,
        cite_url=cite,
    )

    # --- verify ---
    if primary_kind in ("upgrade", "downgrade"):
        verify_intent = (
            f"probe installed version of {target} matches {target_version}"
            if target_version
            else f"probe installed version of {target} is non-vulnerable"
        )
    elif primary_kind == "disable":
        verify_intent = f"probe service {target} is not active"
    elif primary_kind == "isolate":
        verify_intent = f"probe isolation of {target}"
    elif primary_kind == "quarantine":
        verify_intent = f"probe file {target} is absent"
    elif primary_kind == "block_port":
        verify_intent = f"probe port {target} is unreachable"
    elif primary_kind == "hold_package":
        verify_intent = f"probe package {target} is held"
    elif primary_kind == "set_env_var":
        verify_intent = f"probe env-var {target} == {target_version or '<unset>'}"
    elif primary_kind == "set_config_directive":
        verify_intent = f"probe config-file directive {target}"
    else:
        verify_intent = f"probe {primary_kind} of {target}"

    spec.verify = PlanStep(
        intent=verify_intent,
        primitive="probe",
        target=target,
        target_version=target_version,
        action_ref=action_index,
        cite_url=cite,
    )

    # --- rollback ---
    inverse_map = {
        "upgrade": "downgrade",
        "downgrade": "upgrade",
        "disable": "enable",
        "isolate": "deisolate",
        "quarantine": "restore",
        "block_port": "unblock_port",
        "hold_package": "unhold_package",
        "set_env_var": "unset_env_var",
        "set_config_directive": "unset_config_directive",
    }
    inverse_kind = inverse_map.get(primary_kind, "")
    # upgrade rollback NEEDS a prior version; we don't have it here.
    rollback_target_version = ""
    if primary_kind == "downgrade":
        # apply downgraded to target_version; rollback re-upgrades but
        # we don't know to what — fixed_version is the un-installed fix.
        rollback_target_version = fixed_version
    elif primary_kind == "upgrade":
        # No prior version in scope; mark non-invertible.
        rollback_target_version = ""

    rollback_step = PlanStep(
        intent=f"{inverse_kind} {target}"
        + (f"@{rollback_target_version}" if rollback_target_version else ""),
        primitive=inverse_kind,
        target=target,
        target_version=rollback_target_version,
        action_ref=action_index,
        cite_url=cite,
    )
    spec.rollback = rollback_step

    # If upgrade with no rollback target_version → record deficit but
    # keep step (CodeWriter will emit a `package_hold` fallback so the
    # apply itself is rolled back transactionally).
    if primary_kind == "upgrade" and not rollback_target_version:
        spec.deficit_reasons.append("non_invertible_rollback")

    # --- regression ---
    spec.regression = PlanStep(
        intent=f"post-apply healthcheck for {target}",
        primitive="healthcheck",
        target=target,
        action_ref=action_index,
        cite_url=cite,
    )

    return spec


# ---------------------------------------------------------------------------
# Validation: PlanSpec vs. allowed citations
# ---------------------------------------------------------------------------


def validate_plan_spec(
    spec: PlanSpec,
    *,
    allowed_citations: list[str],
) -> list[dict[str, str]]:
    """Return deficit dicts for any violation; empty list = clean.

    A deficit is emitted when:
    - any populated step's ``cite_url`` is not in ``allowed_citations``
      (fabricated_citation)
    - apply.primitive is upgrade/downgrade but target_version is empty
      (version_unspecified)
    - rollback.primitive in {disable, quarantine, block_port} and
      target is empty (unsafe_primitive)
    """
    out: list[dict[str, str]] = []
    if spec.honest_skip:
        return out

    allow = set(c for c in (allowed_citations or []) if c)

    def _check_cite(step: PlanStep, slot: str) -> None:
        if step.is_specified() and step.cite_url and allow:
            if step.cite_url not in allow:
                out.append({
                    "kind": "fabricated_citation",
                    "slot": slot,
                    "detail": step.cite_url,
                })

    _check_cite(spec.apply, "apply")
    _check_cite(spec.verify, "verify")
    _check_cite(spec.rollback, "rollback")
    _check_cite(spec.regression, "regression")

    if spec.apply.primitive in ("upgrade", "downgrade"):
        if not spec.apply.target_version:
            out.append({
                "kind": "version_unspecified",
                "slot": "apply",
                "detail": spec.apply.target,
            })

    if spec.rollback.primitive in ("disable", "quarantine", "block_port"):
        if not spec.rollback.target:
            out.append({
                "kind": "unsafe_primitive",
                "slot": "rollback",
                "detail": spec.rollback.primitive,
            })

    out.extend(spec.deficits())
    return out
