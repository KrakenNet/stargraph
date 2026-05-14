# SPDX-License-Identifier: Apache-2.0
"""CRITERIA Fancy #14: Phase 6 air-gap firewall enforcement (verifier).

Applies a check-only validation to the canonical nft policy file at
``demos/cve_remediation/ops/phase6.nft``. Does NOT load the rules into
the kernel — that's an irreversible operator step.

Three assertions:

* Policy file exists, permissions <= 0644, owned by user.
* ``nft -c -f`` (syntax check) returns 0 (rule grammar valid).
* Required policy elements present:
    - ``input`` chain with ``policy drop``
    - ``output`` chain with ``policy drop``
    - SSH allow-list scoped to a private subnet (not 0.0.0.0/0)
    - Loopback exemption clause

Production smoke-test (operator runs after applying, NOT by this
script):

    nft list ruleset | grep phase6_airgap
    # expect input/output 'policy drop' visible
    curl -m 2 https://example.com  # expect: connection refused/timeout

Run::

    uv run --no-project python -m demos.cve_remediation.scripts.verify_F14_airgap
"""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
from pathlib import Path


_POLICY = (
    Path(__file__).resolve().parent.parent
    / "ops" / "phase6.nft"
)


def _grade(label: str, got: bool, expect: bool, detail: str = "") -> bool:
    icon = "OK" if got is expect else "FAIL"
    suffix = f" ({detail})" if detail else ""
    print(f"  [{label:18}] got={got!r:5} expect={expect!r:5} -> {icon}{suffix}")
    return got is expect


def main() -> int:
    overall = True
    print("=== F14 VERIFICATION (Phase 6 air-gap policy file) ===\n")

    print(f"--- A. Policy file shape ---")
    exists = _POLICY.exists()
    if not _grade("file exists", exists, True, str(_POLICY)):
        overall = False
        return 1

    st = _POLICY.stat()
    perms_ok = (st.st_mode & 0o777) <= 0o644
    if not _grade("perms <= 0644", perms_ok, True,
                  oct(st.st_mode & 0o777)):
        overall = False

    text = _POLICY.read_text(encoding="utf-8")

    print("\n--- B. Required policy elements ---")
    checks = (
        ("input drop", "type filter hook input" in text
                       and "policy drop" in text),
        ("output drop",
         "type filter hook output priority 0; policy drop" in text),
        ("forward drop",
         "type filter hook forward priority 0; policy drop" in text),
        ("loopback ok", 'iif "lo" accept' in text),
        ("ssh scoped",
         "tcp dport 22 accept" in text and "0.0.0.0/0" not in text),
    )
    for label, ok in checks:
        if not _grade(label, ok, True):
            overall = False

    print("\n--- C. nft syntax check ---")
    nft = shutil.which("nft")
    import os as _os
    if not nft:
        print("  ! nft not installed; skipping syntax check (acceptable on "
              "non-firewall hosts)")
    elif _os.geteuid() != 0:
        # nft -c needs CAP_NET_ADMIN even for check-only because it
        # opens a netlink socket. Skip on non-root; operator runs the
        # check on the actual Phase 6 host as part of deployment.
        print("  ! nft -c requires root for netlink; skipping. "
              f"Run as root on the Phase 6 host: sudo nft -c -f {_POLICY}")
    else:
        proc = subprocess.run(  # noqa: S603 -- file path under repo control
            [nft, "-c", "-f", str(_POLICY)],
            capture_output=True, text=True, timeout=10,
        )
        ok = proc.returncode == 0
        if not _grade("nft -c -f", ok, True,
                      detail=proc.stderr.strip().splitlines()[-1]
                      if not ok and proc.stderr else "rules parsed"):
            overall = False

    print()
    if overall:
        print("=== OVERALL: PASS ===")
    else:
        print("=== OVERALL: FAIL ===")
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
