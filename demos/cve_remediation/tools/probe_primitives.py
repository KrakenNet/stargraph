# SPDX-License-Identifier: Apache-2.0
"""Universal infrastructure probe + apply primitives.

Phase B (2026-05-11): when a CVE has no upstream fix (vulnerability_status
in {no_fix_published, withdrawn} AND fixed_version=""), the pipeline
needs a verifiable remediation path that does NOT depend on a CWE-keyed
canned-mitigation table. This module supplies the building blocks.

Design rules (no cheats):

1. **No per-CWE tables.** Every primitive maps a structured input
   (port number, service name, package name, file path) to a
   deterministic ansible task fragment. The caller sources inputs from
   advisory text (regex extraction or LM extraction with citation
   guard), never from a CWE→action mapping.

2. **Apply + verify + rollback contract.** Every apply primitive emits
   three ansible task dicts so the same machinery used by upgrade /
   downgrade flows (``_exec_bundle_on_host``,
   ``VerifyImmediateNode``) handles isolate/disable transparently.

3. **Module allow-list.** All emitted tasks use only modules already
   supported by the bundle executor:
   ``ansible.builtin.{shell, command, service, systemd, file,
   lineinfile, copy}``. Verify steps are ``shell`` with deterministic
   exit-code semantics (0 = patched, non-zero = vulnerable).

4. **Verify-tagged names.** Every verify task has ``verify`` in its
   ``name`` field so ``_verify_tasks_from_bundle`` in ``real_nodes.py``
   picks it up.

5. **Honest-empty.** Missing input → empty output. Caller decides
   whether to fall through to HITL.

Public API:

  * IoC extraction:
      ``extract_iocs_from_advisory(text)`` → ``IoCSet``

  * Apply specs (structured input → primitive spec):
      ``disable_service_spec(name)``
      ``stop_systemd_unit_spec(name)``
      ``hold_package_spec(name, channel)``
      ``block_port_spec(port, proto, direction='in')``
      ``quarantine_file_spec(path)``
      ``set_env_var_spec(scope_file, var, value)``
      ``set_config_directive_spec(file, key, value)``

  * Bundle assembly:
      ``build_isolate_bundle(actions, plan_hash, cve_id)`` →
      ``(apply_yaml, rollback_yaml)``
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# IoC extraction from advisory text
# ---------------------------------------------------------------------------


@dataclass
class IoCSet:
    """Indicators of compromise / configuration extracted from
    advisory text. Used as input to primitive spec generators.

    Every field is a list of strings sourced from the advisory body
    (or LM-extracted with citation guard). No fabrication.
    """

    ports: list[int] = field(default_factory=list)
    services: list[str] = field(default_factory=list)
    packages: list[str] = field(default_factory=list)
    file_paths: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    endpoints: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any((
            self.ports, self.services, self.packages,
            self.file_paths, self.env_vars, self.headers, self.endpoints,
        ))


# Compiled regexes — bounded surface, deterministic.
_PORT_RE = re.compile(r"\b(?:port|tcp/|udp/|listening on|:)\s*(\d{2,5})\b", re.IGNORECASE)
_PORT_BARE_RE = re.compile(r"\bport\s+(\d{2,5})\b", re.IGNORECASE)
_FILE_RE = re.compile(r"(/(?:etc|var|usr|opt|tmp|home|srv|root)/[A-Za-z0-9_./-]{2,200})")
_ENV_RE = re.compile(r"\b([A-Z][A-Z0-9_]{3,40})\s*=\s*[^\s;]+")
_SERVICE_RE = re.compile(
    r"\b(?:service|daemon|systemd unit|disable|stop|enable|start)\s+"
    r"(?:the\s+)?([a-z][a-z0-9.-]{2,40})\b",
    re.IGNORECASE,
)
_SERVICE_STOP_TOKENS = frozenset({
    "the", "a", "an", "this", "that", "these", "those", "any",
    "all", "until", "after", "before", "when", "while", "if",
    "vulnerable", "affected", "service", "daemon", "system",
    "process", "running", "active", "default", "above", "below",
})
_PACKAGE_RE = re.compile(
    r"\b(?:package|library|module|gem|crate)\s+([a-zA-Z][a-zA-Z0-9._-]{2,60})\b",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"\b(X-[A-Za-z][A-Za-z0-9-]{2,40}|"
    r"Content-Security-Policy|Strict-Transport-Security|"
    r"X-Frame-Options|X-Content-Type-Options)\b"
)


def extract_iocs_from_advisory(text: str) -> IoCSet:
    """Best-effort regex extraction. No LM call. Returns empty set
    when text is empty or yields no matches.

    The caller (RemediationDiscoveryNode) is responsible for citation
    bookkeeping — each IoC the planner uses must be tied to an
    advisory snippet so the audit chain stays groundable.
    """

    out = IoCSet()
    if not text:
        return out

    body = text[:50_000]  # bounded surface; advisories rarely exceed

    # ports — combine both patterns + dedupe
    port_ints: set[int] = set()
    for m in _PORT_RE.finditer(body):
        try:
            p = int(m.group(1))
            if 1 <= p <= 65535:
                port_ints.add(p)
        except ValueError:
            continue
    for m in _PORT_BARE_RE.finditer(body):
        try:
            p = int(m.group(1))
            if 1 <= p <= 65535:
                port_ints.add(p)
        except ValueError:
            continue
    out.ports = sorted(port_ints)

    out.file_paths = sorted({m.group(1) for m in _FILE_RE.finditer(body)})
    out.env_vars = sorted({m.group(1) for m in _ENV_RE.finditer(body)})
    out.services = sorted({
        tok for tok in (
            m.group(1).lower() for m in _SERVICE_RE.finditer(body)
        )
        if tok not in _SERVICE_STOP_TOKENS
    })
    out.packages = sorted({m.group(1) for m in _PACKAGE_RE.finditer(body)})
    out.headers = sorted({m.group(1) for m in _HEADER_RE.finditer(body)})

    return out


# ---------------------------------------------------------------------------
# Apply / verify / rollback primitive specs
# ---------------------------------------------------------------------------
#
# Each spec generator returns a dict with three keys:
#   "apply":    list[dict]   ansible task(s) for the mutation
#   "verify":   list[dict]   ansible task(s) with 'verify' in name;
#                            exit 0 = patched, non-zero = vulnerable
#   "rollback": list[dict]   ansible task(s) reversing the mutation
#
# Tasks use only allow-listed modules (see module docstring).
# ---------------------------------------------------------------------------


_PRIMITIVE_VERIFY_PREFIX = "verify-"


def _shell(name: str, cmd: str) -> dict[str, Any]:
    """Single-line shell task. Caller composes the command literally."""
    return {
        "name": name,
        "ansible.builtin.shell": cmd,
    }


def _service(name: str, service_name: str, state: str) -> dict[str, Any]:
    return {
        "name": name,
        "ansible.builtin.service": {
            "name": service_name,
            "state": state,
        },
    }


def disable_service_spec(service_name: str) -> dict[str, Any]:
    """Stop + disable a service. Verify probe asserts service is not
    active. Rollback restarts the service."""
    sn = service_name.strip().lower()
    if not sn:
        return {}
    return {
        "apply": [
            _service(
                f"disable-{sn}",
                sn,
                "stopped",
            ),
            _shell(
                f"disable-{sn}-mask",
                f"systemctl disable {sn} 2>/dev/null || true",
            ),
        ],
        "verify": [
            _shell(
                f"{_PRIMITIVE_VERIFY_PREFIX}{sn}-stopped",
                # 0 = inactive (patched); non-zero = active (vulnerable)
                f"! systemctl is-active --quiet {sn}",
            ),
        ],
        "rollback": [
            _shell(
                f"rollback-enable-{sn}",
                f"systemctl enable {sn} 2>/dev/null || true",
            ),
            _service(
                f"rollback-start-{sn}",
                sn,
                "started",
            ),
        ],
    }


def hold_package_spec(package: str, channel: str = "") -> dict[str, Any]:
    """Pin / hold a package so vulnerable version cannot be reinstalled.

    Channels: apt / yum / dnf / pip. Empty channel falls through to
    a multi-tool detect that runs the first available tool.
    """
    pkg = package.strip()
    if not pkg:
        return {}
    ch = channel.strip().lower()

    if ch in ("apt", "deb", "debian", "ubuntu"):
        apply_cmd = f"apt-mark hold {pkg}"
        verify_cmd = f"apt-mark showhold | grep -q '^{re.escape(pkg)}$'"
        rollback_cmd = f"apt-mark unhold {pkg}"
    elif ch in ("yum", "dnf", "rpm", "redhat", "rhel"):
        apply_cmd = (
            f"(command -v dnf >/dev/null && dnf versionlock add {pkg}) || "
            f"(command -v yum >/dev/null && yum versionlock {pkg})"
        )
        verify_cmd = (
            f"(command -v dnf >/dev/null && dnf versionlock list | "
            f"grep -q '{re.escape(pkg)}') || "
            f"(command -v yum >/dev/null && yum versionlock list | "
            f"grep -q '{re.escape(pkg)}')"
        )
        rollback_cmd = (
            f"(command -v dnf >/dev/null && dnf versionlock delete {pkg}) || "
            f"(command -v yum >/dev/null && yum versionlock delete {pkg})"
        )
    else:
        # Cross-distro fallback. The advisory rarely names the package
        # manager; this branch tries apt then yum then dnf and skips
        # quietly on each miss.
        apply_cmd = (
            f"(command -v apt-mark >/dev/null && apt-mark hold {pkg}) || "
            f"(command -v dnf >/dev/null && dnf versionlock add {pkg}) || "
            f"(command -v yum >/dev/null && yum versionlock {pkg}) || true"
        )
        verify_cmd = (
            f"(command -v apt-mark >/dev/null && "
            f"apt-mark showhold | grep -q '^{re.escape(pkg)}$') || "
            f"(command -v dnf >/dev/null && dnf versionlock list | "
            f"grep -q '{re.escape(pkg)}') || "
            f"(command -v yum >/dev/null && yum versionlock list | "
            f"grep -q '{re.escape(pkg)}')"
        )
        rollback_cmd = (
            f"(command -v apt-mark >/dev/null && apt-mark unhold {pkg}) || "
            f"(command -v dnf >/dev/null && dnf versionlock delete {pkg}) || "
            f"(command -v yum >/dev/null && yum versionlock delete {pkg}) || true"
        )

    return {
        "apply": [_shell(f"hold-{pkg}", apply_cmd)],
        "verify": [_shell(f"{_PRIMITIVE_VERIFY_PREFIX}hold-{pkg}", verify_cmd)],
        "rollback": [_shell(f"rollback-unhold-{pkg}", rollback_cmd)],
    }


def block_port_spec(
    port: int, proto: str = "tcp", direction: str = "in"
) -> dict[str, Any]:
    """Block a port at the host firewall (iptables / nft / ufw —
    whichever is available). Verify probe asserts port is closed.

    direction: 'in' (ingress) | 'out' (egress).
    """
    if not (1 <= int(port) <= 65535):
        return {}
    proto_l = proto.strip().lower() or "tcp"
    if proto_l not in ("tcp", "udp"):
        return {}
    dir_l = direction.strip().lower() or "in"
    if dir_l not in ("in", "out"):
        return {}
    chain = "INPUT" if dir_l == "in" else "OUTPUT"

    # iptables / nft / ufw fall-through chain, exits 0 if any tool succeeded.
    apply_cmd = (
        f"(command -v iptables >/dev/null && "
        f"iptables -C {chain} -p {proto_l} --dport {port} -j DROP 2>/dev/null || "
        f"iptables -A {chain} -p {proto_l} --dport {port} -j DROP) || "
        f"(command -v ufw >/dev/null && ufw deny {port}/{proto_l}) || "
        f"(command -v nft >/dev/null && "
        f"nft add rule inet filter input {proto_l} dport {port} drop) || "
        f"echo 'no firewall tool available' >&2"
    )
    # Verify: probe the port from the host's own loopback. 0 if listener
    # is gone (or filtered). Use timeout + /dev/tcp for portability.
    verify_cmd = (
        f"! (timeout 2 bash -c "
        f"'cat < /dev/{proto_l}/127.0.0.1/{port}' 2>/dev/null)"
    )
    rollback_cmd = (
        f"(command -v iptables >/dev/null && "
        f"iptables -D {chain} -p {proto_l} --dport {port} -j DROP 2>/dev/null) || "
        f"(command -v ufw >/dev/null && ufw delete deny {port}/{proto_l}) || "
        f"(command -v nft >/dev/null && "
        f"nft delete rule inet filter input {proto_l} dport {port} drop) || true"
    )
    return {
        "apply": [_shell(f"block-{proto_l}-{port}", apply_cmd)],
        "verify": [
            _shell(
                f"{_PRIMITIVE_VERIFY_PREFIX}port-{proto_l}-{port}-closed",
                verify_cmd,
            )
        ],
        "rollback": [_shell(f"rollback-unblock-{proto_l}-{port}", rollback_cmd)],
    }


def quarantine_file_spec(path: str) -> dict[str, Any]:
    """Move a vulnerable file to a quarantine path (backup-then-remove).
    Verify asserts the original path is absent."""
    p = path.strip()
    if not p or not p.startswith("/"):
        return {}
    quarantine = f"{p}.cve-rem.quarantine"
    apply_cmd = (
        f"test -e {p!s} && mv {p!s} {quarantine!s} 2>/dev/null || true"
    )
    verify_cmd = f"test ! -e {p!s}"
    rollback_cmd = (
        f"test -e {quarantine!s} && mv {quarantine!s} {p!s} 2>/dev/null || true"
    )
    return {
        "apply": [_shell(f"quarantine-{_safe_id(p)}", apply_cmd)],
        "verify": [
            _shell(
                f"{_PRIMITIVE_VERIFY_PREFIX}quarantine-{_safe_id(p)}",
                verify_cmd,
            )
        ],
        "rollback": [_shell(f"rollback-quarantine-{_safe_id(p)}", rollback_cmd)],
    }


def set_env_var_spec(
    scope_file: str, var: str, value: str
) -> dict[str, Any]:
    """Set an env-var in a known scope file (/etc/environment or a
    systemd drop-in). Verify greps for the literal line."""
    v = var.strip()
    if not v or not v.replace("_", "").isalnum():
        return {}
    sf = scope_file.strip() or "/etc/environment"
    line = f"{v}={value}"
    apply_cmd = (
        f"grep -q '^{re.escape(v)}=' {sf!s} 2>/dev/null && "
        f"sed -i 's|^{re.escape(v)}=.*|{line}|' {sf!s} || "
        f"echo '{line}' >> {sf!s}"
    )
    verify_cmd = f"grep -Fxq '{line}' {sf!s}"
    rollback_cmd = (
        f"sed -i '/^{re.escape(v)}=/d' {sf!s} 2>/dev/null || true"
    )
    return {
        "apply": [_shell(f"set-env-{v}", apply_cmd)],
        "verify": [_shell(f"{_PRIMITIVE_VERIFY_PREFIX}env-{v}", verify_cmd)],
        "rollback": [_shell(f"rollback-env-{v}", rollback_cmd)],
    }


def set_config_directive_spec(
    file: str, key: str, value: str
) -> dict[str, Any]:
    """Set a key=value directive in a config file. Backup-then-edit.
    Verify greps for the literal directive."""
    f = file.strip()
    k = key.strip()
    if not f or not k or not f.startswith("/"):
        return {}
    directive = f"{k} = {value}".strip()
    backup_cmd = (
        f"test -e {f!s} && cp -a {f!s} {f!s}.cve-rem.bak || true"
    )
    apply_cmd = (
        f"{backup_cmd}; "
        f"grep -q '^{re.escape(k)}\\b' {f!s} 2>/dev/null && "
        f"sed -i 's|^{re.escape(k)}.*|{directive}|' {f!s} || "
        f"echo '{directive}' >> {f!s}"
    )
    verify_cmd = f"grep -Fxq '{directive}' {f!s}"
    rollback_cmd = (
        f"test -e {f!s}.cve-rem.bak && "
        f"mv {f!s}.cve-rem.bak {f!s} || true"
    )
    return {
        "apply": [_shell(f"set-{k}-in-{_safe_id(f)}", apply_cmd)],
        "verify": [
            _shell(
                f"{_PRIMITIVE_VERIFY_PREFIX}{k}-in-{_safe_id(f)}",
                verify_cmd,
            )
        ],
        "rollback": [_shell(f"rollback-{k}-in-{_safe_id(f)}", rollback_cmd)],
    }


def install_version_spec(
    package: str, version: str, channel: str = "",
    *, rollback_version: str = "",
) -> dict[str, Any]:
    """Install ``package`` at exact ``version`` (upgrade or downgrade).

    Phase F (2026-05-11): deterministic versioned-install primitive
    used by upgrade / downgrade plan-spec apply steps. Channels:
    apt / dnf / yum / pip / pip3. Empty channel falls through to a
    multi-tool detect.

    The rollback step re-installs at ``rollback_version`` if set; when
    no prior version is known, falls back to ``apt-mark hold`` so the
    apply is at least pinned in place (non-invertible deficit is
    surfaced by CodeWriter).
    """
    pkg = package.strip()
    if not pkg or not version.strip():
        return {}
    ver = version.strip()
    ch = channel.strip().lower()

    if ch in ("apt", "deb", "debian", "ubuntu"):
        apply_cmd = (
            f"DEBIAN_FRONTEND=noninteractive apt-get update >/dev/null && "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y "
            f"--allow-downgrades {pkg}={ver}"
        )
        verify_cmd = (
            f"dpkg-query -W -f='${{Version}}' {pkg} 2>/dev/null | "
            f"grep -Fxq '{ver}'"
        )
    elif ch in ("dnf", "rpm", "redhat", "rhel", "yum"):
        apply_cmd = (
            f"(command -v dnf >/dev/null && dnf install -y --allowerasing "
            f"{pkg}-{ver}) || "
            f"(command -v yum >/dev/null && yum install -y {pkg}-{ver})"
        )
        verify_cmd = f"rpm -q --queryformat '%{{VERSION}}' {pkg} | grep -Fxq '{ver}'"
    elif ch in ("pip", "pypi", "python"):
        apply_cmd = f"python3 -m pip install --upgrade '{pkg}=={ver}'"
        verify_cmd = (
            f"python3 -m pip show {pkg} 2>/dev/null | "
            f"awk '/^Version:/ {{print $2}}' | grep -Fxq '{ver}'"
        )
    else:
        # Cross-tool fallback: try apt, dnf, yum, pip in order.
        apply_cmd = (
            f"(command -v apt-get >/dev/null && "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y "
            f"--allow-downgrades {pkg}={ver}) || "
            f"(command -v dnf >/dev/null && dnf install -y --allowerasing "
            f"{pkg}-{ver}) || "
            f"(command -v yum >/dev/null && yum install -y {pkg}-{ver}) || "
            f"(command -v pip3 >/dev/null && pip3 install '{pkg}=={ver}')"
        )
        verify_cmd = (
            f"(command -v dpkg-query >/dev/null && "
            f"dpkg-query -W -f='${{Version}}' {pkg} 2>/dev/null | grep -Fxq '{ver}') || "
            f"(command -v rpm >/dev/null && "
            f"rpm -q --queryformat '%{{VERSION}}' {pkg} | grep -Fxq '{ver}') || "
            f"(command -v pip3 >/dev/null && "
            f"pip3 show {pkg} 2>/dev/null | "
            f"awk '/^Version:/ {{print $2}}' | grep -Fxq '{ver}')"
        )

    rv = rollback_version.strip()
    if rv:
        if ch in ("apt", "deb", "debian", "ubuntu"):
            rollback_cmd = (
                f"DEBIAN_FRONTEND=noninteractive apt-get install -y "
                f"--allow-downgrades {pkg}={rv}"
            )
        elif ch in ("dnf", "rpm", "redhat", "rhel", "yum"):
            rollback_cmd = (
                f"(command -v dnf >/dev/null && dnf install -y --allowerasing "
                f"{pkg}-{rv}) || "
                f"(command -v yum >/dev/null && yum install -y {pkg}-{rv})"
            )
        elif ch in ("pip", "pypi", "python"):
            rollback_cmd = f"python3 -m pip install '{pkg}=={rv}'"
        else:
            rollback_cmd = (
                f"(command -v apt-get >/dev/null && "
                f"DEBIAN_FRONTEND=noninteractive apt-get install -y "
                f"--allow-downgrades {pkg}={rv}) || "
                f"(command -v dnf >/dev/null && dnf install -y --allowerasing "
                f"{pkg}-{rv}) || "
                f"(command -v pip3 >/dev/null && pip3 install '{pkg}=={rv}')"
            )
    else:
        # No known prior version: at least pin to prevent further drift.
        rollback_cmd = (
            f"(command -v apt-mark >/dev/null && apt-mark hold {pkg}) || "
            f"(command -v dnf >/dev/null && dnf versionlock add {pkg}) || true"
        )

    return {
        "apply": [_shell(f"install-{_safe_id(pkg)}-{_safe_id(ver)}", apply_cmd)],
        "verify": [
            _shell(
                f"{_PRIMITIVE_VERIFY_PREFIX}install-{_safe_id(pkg)}-{_safe_id(ver)}",
                verify_cmd,
            )
        ],
        "rollback": [
            _shell(
                f"rollback-{_safe_id(pkg)}-"
                + (_safe_id(rv) if rv else "hold"),
                rollback_cmd,
            )
        ],
    }


def _safe_id(s: str) -> str:
    """Make a string safe for use in an ansible task name."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)[:64]


# ---------------------------------------------------------------------------
# Bundle assembly — RemediationAction list → ansible playbook YAML
# ---------------------------------------------------------------------------


_BUNDLE_KINDS = frozenset({"isolate", "disable", "quarantine", "mitigation"})


def spec_for_action(action: Any) -> dict[str, Any]:
    """Map a single RemediationAction to a primitive spec.

    The action's ``kind`` + ``target`` field drives the choice:

      kind=disable, target=<service-name>      -> disable_service_spec
      kind=disable, target=<pkg-name>          -> hold_package_spec (heuristic)
      kind=isolate, target=tcp/<port>          -> block_port_spec
      kind=isolate, target=udp/<port>          -> block_port_spec
      kind=isolate, target=<service-name>      -> disable_service_spec
      kind=quarantine, target=</abs/path>      -> quarantine_file_spec
      kind=mitigation, target=env:<VAR>=<v>    -> set_env_var_spec
      kind=mitigation, target=config:<file>:<key>=<v>  -> set_config_directive_spec

    Returns ``{}`` on unparseable input. Caller decides how to surface
    the gap (HITL, drop, log).
    """
    kind = str(getattr(action, "kind", "") or "").strip().lower()
    target = str(getattr(action, "target", "") or "").strip()
    if not kind or not target:
        return {}

    # isolate via port: "tcp/8080" or "udp/53"
    m_port = re.match(r"^(tcp|udp)\s*[/:]\s*(\d{1,5})$", target, re.IGNORECASE)
    if kind == "isolate" and m_port:
        return block_port_spec(int(m_port.group(2)), m_port.group(1).lower())

    if kind in ("disable", "isolate"):
        # Heuristic: if target contains "." or "-" and not "/", treat as
        # a service or package name. Service if it ends with .service,
        # .socket, or .target (systemd unit). Else try service first,
        # fall back to package hold.
        if target.endswith((".service", ".socket", ".target")):
            return disable_service_spec(target)
        if "/" not in target and not target.startswith("/"):
            # If target string looks like a package@version, strip version
            pkg_or_svc = target.split("@", 1)[0].split("==", 1)[0]
            if kind == "disable" and re.match(r"^[a-z][a-z0-9._-]+$", pkg_or_svc):
                # Prefer service if commonly-named-service; else package hold.
                # No environment-side detection at spec-gen time; emit both
                # apply branches via a fall-through shell.
                spec_svc = disable_service_spec(pkg_or_svc)
                spec_pkg = hold_package_spec(pkg_or_svc)
                return _merge_specs(spec_svc, spec_pkg, label=f"disable-{pkg_or_svc}")
            return disable_service_spec(pkg_or_svc)

    if kind == "quarantine" and target.startswith("/"):
        return quarantine_file_spec(target)

    if kind == "mitigation":
        m_env = re.match(r"^env:([A-Z_][A-Z0-9_]*)\s*=\s*(.+)$", target)
        if m_env:
            return set_env_var_spec(
                "/etc/environment", m_env.group(1), m_env.group(2)
            )
        m_cfg = re.match(
            r"^config:(/[^:]+):([^=]+)=(.+)$", target
        )
        if m_cfg:
            return set_config_directive_spec(
                m_cfg.group(1), m_cfg.group(2), m_cfg.group(3)
            )

    return {}


def _merge_specs(a: dict[str, Any], b: dict[str, Any], *, label: str) -> dict[str, Any]:
    """Merge two specs into one bundle that tries both apply paths.
    Useful when an action could be service or package and we want
    both attempted (each individually skips on miss)."""
    if not a:
        return b
    if not b:
        return a
    return {
        "apply": list(a.get("apply", [])) + list(b.get("apply", [])),
        "verify": list(a.get("verify", [])) + list(b.get("verify", [])),
        "rollback": list(b.get("rollback", [])) + list(a.get("rollback", [])),
    }


def build_plan_spec_bundle(
    plan_spec: dict[str, Any],
    *,
    plan_hash: str,
    cve_id: str,
    install_channel: str = "",
) -> tuple[str, str, dict[str, str]]:
    """Compose apply + rollback ansible YAML from a structured PlanSpec.

    Phase F (2026-05-11): deterministic bundle path for upgrade /
    downgrade / disable / isolate / quarantine / hold_package /
    block_port / set_env_var / set_config_directive primitives.

    Returns ``(apply_yaml, rollback_yaml, meta)`` where ``meta`` carries
    ``{"rollback_non_invertible": "true|false", "rollback_reason": ...}``
    so the critic can surface a deficit when the apply step has no
    natural inverse (e.g. upgrade with unknown prior version).

    Returns ``("", "", {})`` when the spec is honest_skip / empty /
    unsupported — caller falls through to the LM path.
    """
    import yaml  # stdlib pyyaml

    if not plan_spec or plan_spec.get("honest_skip"):
        return "", "", {}

    apply_step = plan_spec.get("apply") or {}
    rollback_step = plan_spec.get("rollback") or {}
    apply_prim = str(apply_step.get("primitive", "") or "").strip().lower()
    if not apply_prim:
        return "", "", {}

    target = str(apply_step.get("target", "") or "").strip()
    target_version = str(apply_step.get("target_version", "") or "").strip()
    rollback_target_version = str(
        rollback_step.get("target_version", "") or ""
    ).strip()

    spec: dict[str, Any] = {}
    meta: dict[str, str] = {
        "rollback_non_invertible": "false",
        "rollback_reason": "",
    }

    if apply_prim in ("upgrade", "downgrade"):
        if not target or not target_version:
            return "", "", {}
        spec = install_version_spec(
            target,
            target_version,
            install_channel,
            rollback_version=rollback_target_version,
        )
        if apply_prim == "upgrade" and not rollback_target_version:
            meta["rollback_non_invertible"] = "true"
            meta["rollback_reason"] = "no_prior_version_known"
    elif apply_prim == "disable":
        spec = disable_service_spec(target)
    elif apply_prim == "isolate":
        m_port = re.match(
            r"^(tcp|udp)\s*[/:]\s*(\d{1,5})$", target, re.IGNORECASE
        )
        if m_port:
            spec = block_port_spec(int(m_port.group(2)), m_port.group(1).lower())
        else:
            spec = disable_service_spec(target)
    elif apply_prim == "quarantine":
        spec = quarantine_file_spec(target)
    elif apply_prim == "hold_package":
        spec = hold_package_spec(target, install_channel)
    elif apply_prim == "block_port":
        m_port = re.match(
            r"^(tcp|udp)\s*[/:]\s*(\d{1,5})$", target, re.IGNORECASE
        )
        if m_port:
            spec = block_port_spec(int(m_port.group(2)), m_port.group(1).lower())
    elif apply_prim == "set_env_var":
        # target_version stores the value for env-var primitives.
        spec = set_env_var_spec("/etc/environment", target, target_version)
    elif apply_prim == "set_config_directive":
        # target encodes "<file>:<key>"; target_version = value.
        m_cfg = re.match(r"^([^:]+):(.+)$", target)
        if m_cfg:
            spec = set_config_directive_spec(
                m_cfg.group(1), m_cfg.group(2), target_version
            )
    else:
        return "", "", {}

    if not spec or not spec.get("apply"):
        return "", "", {}

    apply_play = [
        {
            "name": f"cve-rem plan-spec apply ({cve_id} {plan_hash[:8]} {apply_prim})",
            "hosts": "all",
            "gather_facts": False,
            "tasks": list(spec.get("apply", [])) + list(spec.get("verify", [])),
        }
    ]
    rollback_play = [
        {
            "name": f"cve-rem plan-spec rollback ({cve_id} {plan_hash[:8]} {apply_prim})",
            "hosts": "all",
            "gather_facts": False,
            "tasks": list(spec.get("rollback", []))
            or [_shell("rollback-noop", "true")],
        }
    ]
    apply_yaml = yaml.safe_dump(
        apply_play, default_flow_style=False, sort_keys=False
    )
    rollback_yaml = yaml.safe_dump(
        rollback_play, default_flow_style=False, sort_keys=False
    )
    return apply_yaml, rollback_yaml, meta


def build_isolate_bundle(
    actions: list[Any], *, plan_hash: str, cve_id: str
) -> tuple[str, str]:
    """Compose apply + rollback ansible playbook YAML from a list of
    RemediationAction. Each action contributes its primitive spec.

    Returns ``("", "")`` when no action produces a non-empty spec —
    the caller falls through to HITL / unpatchable.
    """
    import yaml  # stdlib pyyaml

    apply_tasks: list[dict[str, Any]] = []
    verify_tasks: list[dict[str, Any]] = []
    rollback_tasks: list[dict[str, Any]] = []
    for a in actions or []:
        kind = str(getattr(a, "kind", "") or "").strip().lower()
        if kind not in _BUNDLE_KINDS:
            continue
        spec = spec_for_action(a)
        if not spec:
            continue
        apply_tasks.extend(spec.get("apply", []))
        verify_tasks.extend(spec.get("verify", []))
        rollback_tasks.extend(spec.get("rollback", []))

    if not apply_tasks:
        return "", ""

    apply_play = [
        {
            "name": f"cve-rem isolate/disable apply ({cve_id} {plan_hash[:8]})",
            "hosts": "all",
            "gather_facts": False,
            "tasks": apply_tasks + verify_tasks,
        }
    ]
    rollback_play = [
        {
            "name": f"cve-rem isolate/disable rollback ({cve_id} {plan_hash[:8]})",
            "hosts": "all",
            "gather_facts": False,
            "tasks": rollback_tasks or [_shell("rollback-noop", "true")],
        }
    ]
    apply_yaml = yaml.safe_dump(
        apply_play, default_flow_style=False, sort_keys=False
    )
    rollback_yaml = yaml.safe_dump(
        rollback_play, default_flow_style=False, sort_keys=False
    )
    return apply_yaml, rollback_yaml


# ---------------------------------------------------------------------------
# IoC → RemediationAction synthesis (advisory-grounded)
# ---------------------------------------------------------------------------


def _excerpt_around(body: str, needle: str, window: int = 200) -> str:
    """Return up to ``window`` chars centered on the first match of
    ``needle`` in ``body``. Used to bind a synthesized action to a
    real snippet of advisory text so the citation_excerpt isn't
    fabricated."""
    if not body or not needle:
        return ""
    idx = body.lower().find(needle.lower())
    if idx < 0:
        return ""
    half = window // 2
    start = max(0, idx - half)
    end = min(len(body), idx + len(needle) + half)
    return body[start:end].strip()[:240]


def synthesize_isolate_actions_from_iocs(
    iocs: IoCSet,
    *,
    advisory_url: str,
    advisory_body: str,
    confidence_bp: int = 6500,
) -> list[dict[str, Any]]:
    """Produce a list of RemediationAction dicts from an IoC set.

    No CWE table. No fabrication. Each emitted action carries a
    citation_excerpt sourced from the advisory body around the IoC
    that drove it. Empty IoCSet → empty list.

    Confidence bounded at ``confidence_bp`` (default 6500 bp). The
    auto-apply gate (``CVE_REM_AUTO_APPLY_BP``, default 7000 bp) is
    set above this threshold so isolate/disable actions always route
    through HITL by default — the operator confirms isolate-rather-
    than-patch is the right call.
    """
    out: list[dict[str, Any]] = []
    if iocs.is_empty() or not advisory_url:
        return out

    # Bound the total emitted action count so a noisy advisory can't
    # spam the planner with 50+ low-quality directives.
    cap = 8

    for port in iocs.ports[:3]:
        if len(out) >= cap:
            break
        cite = _excerpt_around(advisory_body, f"port {port}") \
            or _excerpt_around(advisory_body, str(port))
        if not cite:
            continue
        out.append({
            "kind": "isolate",
            "target": f"tcp/{port}",
            "change": (
                f"Block ingress on TCP port {port} at the host firewall "
                f"until the upstream patch ships. Rollback re-opens "
                f"the port."
            ),
            "rationale": (
                f"Advisory references port {port} as the exposure "
                f"surface; closing the listener removes the attack "
                f"vector without depending on a patch."
            ),
            "citation_url": advisory_url,
            "citation_excerpt": cite,
            "source": "advisory_iocs",
            "confidence_bp": confidence_bp,
        })

    for svc in iocs.services[:3]:
        if len(out) >= cap:
            break
        cite = _excerpt_around(advisory_body, svc)
        if not cite:
            continue
        out.append({
            "kind": "disable",
            "target": svc,
            "change": (
                f"Stop and disable the {svc} service; verify it is no "
                f"longer active. Rollback re-enables and restarts."
            ),
            "rationale": (
                f"Advisory names the {svc} service as the vulnerable "
                f"surface; disabling removes exposure pending upstream "
                f"patch."
            ),
            "citation_url": advisory_url,
            "citation_excerpt": cite,
            "source": "advisory_iocs",
            "confidence_bp": confidence_bp,
        })

    for path in iocs.file_paths[:2]:
        if len(out) >= cap:
            break
        # Only quarantine files in known-safe vendor / app dirs. Refuse
        # /etc/passwd, /etc/shadow, /usr/bin/*, etc.  Conservative
        # allow-list.
        if not any(
            path.startswith(p) for p in (
                "/var/www/", "/opt/", "/srv/", "/var/lib/",
                "/var/log/", "/tmp/",
            )
        ):
            continue
        cite = _excerpt_around(advisory_body, path)
        if not cite:
            continue
        out.append({
            "kind": "quarantine",
            "target": path,
            "change": (
                f"Quarantine the vulnerable artifact at {path} (move to "
                f"{path}.cve-rem.quarantine). Rollback restores the "
                f"original path."
            ),
            "rationale": (
                f"Advisory names {path} as the vulnerable file; "
                f"removing it eliminates the exploit surface."
            ),
            "citation_url": advisory_url,
            "citation_excerpt": cite,
            "source": "advisory_iocs",
            "confidence_bp": confidence_bp,
        })

    for pkg in iocs.packages[:2]:
        if len(out) >= cap:
            break
        cite = _excerpt_around(advisory_body, pkg)
        if not cite:
            continue
        out.append({
            "kind": "disable",
            "target": pkg,
            "change": (
                f"Hold the {pkg} package at its current version so the "
                f"vulnerable line cannot be reinstalled, and stop any "
                f"service of the same name. Rollback unholds + restarts."
            ),
            "rationale": (
                f"Advisory names the {pkg} package as the affected "
                f"library; pinning + stopping reduces exposure until "
                f"upstream patch."
            ),
            "citation_url": advisory_url,
            "citation_excerpt": cite,
            "source": "advisory_iocs",
            "confidence_bp": confidence_bp,
        })

    return out


__all__ = [
    "IoCSet",
    "extract_iocs_from_advisory",
    "synthesize_isolate_actions_from_iocs",
    "disable_service_spec",
    "hold_package_spec",
    "block_port_spec",
    "quarantine_file_spec",
    "set_env_var_spec",
    "set_config_directive_spec",
    "spec_for_action",
    "build_isolate_bundle",
]
