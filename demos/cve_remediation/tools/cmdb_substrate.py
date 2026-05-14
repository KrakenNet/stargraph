# SPDX-License-Identifier: Apache-2.0
"""CPE-driven substrate guard for CVE→host correlation.

A CMDB ``nameLIKE`` query against ``cmdb_ci_spkg`` routinely scores a
plausible Software CI for a CVE whose CPE 2.3 metadata explicitly says
the affected platform is something the deployment substrate cannot
host (Microsoft Internet Explorer on a Linux router; Apple iOS on a
build server). The substrate guard rejects these BEFORE host
correlation, so the pipeline doesn't manufacture false-positive host
matches for substrate-impossible CVEs.

Decision driver: the NVD record's CPE 2.3 list (one row per affected
product configuration), NOT a hand-authored ``(vendor, product) →
verdict`` table. Every entry's ``part`` / ``target_sw`` / ``target_hw``
field maps mechanically to applicability against a ``SubstrateSpec``
that describes the local fleet (h11 = Linux x86_64). This is the only
way the decision generalizes beyond the per-vendor enumeration: any
CVE with NVD CPE data is classified by the same lookup.

When a CVE has no CPE rows (sparse advisory, fresh / un-classified
CVE), the guard fails OPEN — default-allow with the unknown reason
logged — rather than silently dropping legitimate hosts.

The same decision is mirrored in the
``rule-packs/cve-rem-cmdb-substrate`` Nautilus rule pack so that when
``CVE_REM_LIVE_BROKER=1`` is set with a registered broker, the
``scope_constraint`` facts asserted by CLIPS rules render into the
ServiceNow ``sysparm_query`` BEFORE the CMDB call (defence in depth:
broker-side rules attest the decision, python-side filter catches
the offline / pre-roll-out case).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubstrateSpec:
    """Describes one deployment substrate (a fleet of compatible hosts).

    Decisions are config, not per-CVE. ``h11`` deploys Linux x86_64
    containers/VMs; firmware, Windows, macOS, iOS, Android etc. cannot
    run there. ``denied_target_sw`` is exhaustive against the CPE 2.3
    ``target_sw`` field's published vocabulary so any CPE referencing
    those gets rejected mechanically.
    """

    name: str = "linux-x86_64"
    allowed_target_sw: frozenset[str] = frozenset({"linux", "*", "-", ""})
    denied_target_sw: frozenset[str] = frozenset({
        "windows", "windows_10", "windows_11", "windows_server",
        "macos", "mac_os", "mac_os_x", "osx",
        "ios", "ipados", "watchos", "tvos", "visionos",
        "android",
        "chromeos", "chrome_os",
        "freertos", "vxworks", "rtos",
        "esxi", "vmware_esxi",
    })
    denied_cpe_parts: frozenset[str] = frozenset({"h"})
    allowed_target_hw: frozenset[str] = frozenset({"x86_64", "x64", "amd64", "*", "-", ""})


# Default substrate for the h11 demo fleet. Real deployments would
# load this from harbor.toml / nautilus.yaml.
DEFAULT_SUBSTRATE_SPEC = SubstrateSpec()


@dataclass(frozen=True)
class CpeDecision:
    """One classification result for one CPE 2.3 URI."""

    cpe_uri: str
    part: str
    vendor: str
    product: str
    target_sw: str
    target_hw: str
    applicable: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "cpe_uri": self.cpe_uri,
            "part": self.part,
            "vendor": self.vendor,
            "product": self.product,
            "target_sw": self.target_sw,
            "target_hw": self.target_hw,
            "applicable": self.applicable,
            "reason": self.reason,
        }


@dataclass
class SubstrateProfile:
    """Resolved substrate constraint for one CVE.

    Two shapes:

    * ``is_open() == True`` — CVE could apply to substrate; host
      correlation proceeds normally.
    * ``deny_unmatched == True`` with empty ``allowed_role_prefixes``
      — CVE's CPE list is incompatible with substrate; every host is
      dropped by :func:`apply_substrate_filter`.
    """

    rule_id: str = ""
    allowed_role_prefixes: tuple[str, ...] = ()
    denied_role_prefixes: tuple[str, ...] = ()
    deny_unmatched: bool = False
    reason: str = ""
    cpe_part: str = ""

    def is_open(self) -> bool:
        return (
            not self.allowed_role_prefixes
            and not self.denied_role_prefixes
            and not self.deny_unmatched
        )

    def verdict(self, role_tokens: list[str]) -> tuple[bool, str]:
        toks = [t.strip().lower() for t in (role_tokens or []) if t]
        if "*" in self.allowed_role_prefixes:
            return True, "wildcard_allowed"
        hit_deny = next(
            (t for t in toks if t in self.denied_role_prefixes),
            None,
        )
        if hit_deny is not None:
            return False, f"token={hit_deny!r} denied by {self.rule_id}"
        if self.allowed_role_prefixes:
            hit_allow = next(
                (t for t in toks if t in self.allowed_role_prefixes),
                None,
            )
            if hit_allow is not None:
                return True, f"token={hit_allow!r} explicitly allowed"
            if self.deny_unmatched:
                return False, (
                    f"tokens={toks} not in allow-list "
                    f"{self.allowed_role_prefixes} for {self.rule_id}"
                )
            return True, "allow-list non-strict (unlisted tokens pass)"
        if self.deny_unmatched and not self.allowed_role_prefixes:
            return False, (
                f"tokens={toks} denied by {self.rule_id}: {self.reason}"
            )
        return True, "no_constraint"


@dataclass
class SubstrateDecision:
    """Per-host decision record for audit."""

    host_name: str
    role_prefix: str
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "host_name": self.host_name,
            "role_prefix": self.role_prefix,
            "allowed": self.allowed,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# CPE classification (mechanical, no hardcoded vendor/product table)
# ---------------------------------------------------------------------------


def classify_cpe(cpe_uri: str, spec: SubstrateSpec = DEFAULT_SUBSTRATE_SPEC) -> CpeDecision:
    """Classify one CPE 2.3 URI against a substrate spec.

    Format::

        cpe:2.3:{part}:{vendor}:{product}:{version}:{update}:{edition}:
                {language}:{sw_edition}:{target_sw}:{target_hw}:{other}

    Decision precedence (first match wins):

    1. ``part`` in ``spec.denied_cpe_parts`` → not_applicable.
    2. ``target_sw`` in ``spec.denied_target_sw`` → not_applicable.
    3. ``target_sw`` present and not in ``spec.allowed_target_sw`` → not_applicable.
    4. ``target_hw`` present and not in ``spec.allowed_target_hw`` → not_applicable.
    5. Otherwise → applicable.

    Malformed CPE (<13 colon-segments) fails OPEN with reason logged.
    """
    parts = (cpe_uri or "").split(":")
    if len(parts) < 13:
        return CpeDecision(
            cpe_uri=cpe_uri,
            part="", vendor="", product="", target_sw="", target_hw="",
            applicable=True,
            reason="malformed_cpe_failopen",
        )
    part = parts[2].lower()
    vendor = parts[3].lower()
    product = parts[4].lower()
    target_sw = parts[10].lower()
    target_hw = parts[11].lower()

    if part in spec.denied_cpe_parts:
        return CpeDecision(
            cpe_uri=cpe_uri, part=part, vendor=vendor, product=product,
            target_sw=target_sw, target_hw=target_hw,
            applicable=False,
            reason=f"cpe_part={part!r} excluded by substrate {spec.name!r}",
        )
    if target_sw in spec.denied_target_sw:
        return CpeDecision(
            cpe_uri=cpe_uri, part=part, vendor=vendor, product=product,
            target_sw=target_sw, target_hw=target_hw,
            applicable=False,
            reason=f"target_sw={target_sw!r} denied by substrate {spec.name!r}",
        )
    if target_sw and target_sw not in spec.allowed_target_sw:
        return CpeDecision(
            cpe_uri=cpe_uri, part=part, vendor=vendor, product=product,
            target_sw=target_sw, target_hw=target_hw,
            applicable=False,
            reason=f"target_sw={target_sw!r} not in allow-list for {spec.name!r}",
        )
    if target_hw and target_hw not in spec.allowed_target_hw:
        return CpeDecision(
            cpe_uri=cpe_uri, part=part, vendor=vendor, product=product,
            target_sw=target_sw, target_hw=target_hw,
            applicable=False,
            reason=f"target_hw={target_hw!r} not in allow-list for {spec.name!r}",
        )
    return CpeDecision(
        cpe_uri=cpe_uri, part=part, vendor=vendor, product=product,
        target_sw=target_sw, target_hw=target_hw,
        applicable=True,
        reason="cpe_compatible_with_substrate",
    )


def derive_substrate_profile_from_cpes(
    cpe_uris: list[str],
    spec: SubstrateSpec = DEFAULT_SUBSTRATE_SPEC,
) -> tuple[SubstrateProfile, list[CpeDecision]]:
    """Aggregate per-CPE decisions into one substrate profile.

    ANY-applicable wins: if even one CPE in the CVE's list is
    compatible with the substrate, the CVE is treated as applicable
    (host correlation proceeds; CMDB will decide per-host). ALL
    incompatible → the CVE is substrate-denied and downstream drops
    every host.

    Empty CPE list → open profile with ``cpe_list_empty`` reason
    (fail-open is intentional: unknown ≠ ineligible).
    """
    if not cpe_uris:
        return (
            SubstrateProfile(rule_id="cpe_list_empty", reason="no_cpe_data_failopen"),
            [],
        )
    decisions = [classify_cpe(u, spec) for u in cpe_uris]
    applicable = [d for d in decisions if d.applicable]
    if applicable:
        return (
            SubstrateProfile(
                rule_id="cpe_substrate_applicable",
                reason=(
                    f"{len(applicable)}/{len(decisions)} cpe rows compatible "
                    f"with substrate={spec.name!r}"
                ),
            ),
            decisions,
        )
    # Every row incompatible → substrate-denied.
    sample = decisions[0]
    return (
        SubstrateProfile(
            rule_id="cpe_substrate_denied",
            allowed_role_prefixes=(),
            denied_role_prefixes=(),
            deny_unmatched=True,
            reason=(
                f"all {len(decisions)} cpe rows incompatible with substrate="
                f"{spec.name!r}; sample: {sample.reason}"
            ),
            cpe_part=sample.part,
        ),
        decisions,
    )


# ---------------------------------------------------------------------------
# Host-filter pipeline
# ---------------------------------------------------------------------------


_HOSTNAME_SPLIT: re.Pattern[str] = re.compile(r"[-_.]+")


def extract_role_tokens(host_name: str) -> list[str]:
    """Return the non-numeric segments of a hostname as role tokens."""
    if not host_name:
        return []
    parts = _HOSTNAME_SPLIT.split(host_name.strip().lower())
    return [p for p in parts if p and not p.isdigit()]


def extract_role_prefix(host_name: str) -> str:
    """Backward-compatible single-token extractor."""
    toks = extract_role_tokens(host_name)
    if len(toks) >= 2:
        return toks[1]
    return toks[0] if toks else ""


def apply_substrate_filter(
    host_names: list[str],
    profile: SubstrateProfile,
) -> tuple[list[str], list[SubstrateDecision]]:
    """Filter ``host_names`` through ``profile``.

    Returns ``(kept_names, decisions)``. ``decisions`` lists EVERY
    host with its role prefix and allow/deny verdict.

    Hosts whose role prefix can't be extracted (unclassified) are
    always kept — the substrate guard fails open on unrecognized
    naming patterns rather than dropping legitimate hosts.
    """
    if not host_names:
        return [], []
    decisions: list[SubstrateDecision] = []
    kept: list[str] = []
    for name in host_names:
        tokens = extract_role_tokens(name)
        primary = tokens[1] if len(tokens) >= 2 else (tokens[0] if tokens else "")
        if not tokens:
            decisions.append(
                SubstrateDecision(
                    host_name=name,
                    role_prefix="",
                    allowed=True,
                    reason="unclassified_hostname_failsafe_allow",
                )
            )
            kept.append(name)
            continue
        if profile.is_open():
            decisions.append(
                SubstrateDecision(
                    host_name=name,
                    role_prefix=primary,
                    allowed=True,
                    reason="substrate_open",
                )
            )
            kept.append(name)
            continue
        allowed, reason = profile.verdict(tokens)
        decisions.append(
            SubstrateDecision(
                host_name=name,
                role_prefix=primary,
                allowed=allowed,
                reason=reason,
            )
        )
        if allowed:
            kept.append(name)
    return kept, decisions


# ---------------------------------------------------------------------------
# Audit envelope helpers
# ---------------------------------------------------------------------------


def envelope_payload(
    profile: SubstrateProfile,
    decisions: list[SubstrateDecision],
    cpe_decisions: list[CpeDecision] | None = None,
) -> dict[str, object]:
    """Build the ``substrate_filter`` payload for broker envelope audit."""
    out: dict[str, object] = {
        "rule_id": profile.rule_id,
        "allowed_role_prefixes": list(profile.allowed_role_prefixes),
        "denied_role_prefixes": list(profile.denied_role_prefixes),
        "deny_unmatched": profile.deny_unmatched,
        "cpe_part": profile.cpe_part,
        "reason": profile.reason,
        "decisions": [d.to_dict() for d in decisions],
        "dropped_count": sum(1 for d in decisions if not d.allowed),
        "kept_count": sum(1 for d in decisions if d.allowed),
    }
    if cpe_decisions is not None:
        out["cpe_decisions"] = [d.to_dict() for d in cpe_decisions]
        out["cpe_applicable_count"] = sum(1 for d in cpe_decisions if d.applicable)
        out["cpe_not_applicable_count"] = sum(1 for d in cpe_decisions if not d.applicable)
    return out
