# SPDX-License-Identifier: Apache-2.0
"""The pack smith verify gate — the "always works" contract for Bosun rule packs.

The three-tier shape + subprocess isolation live in :mod:`stargraph.skills._smith.gate`;
this module supplies the *rule pack* contract: write the bundle (``rules.clp`` + the
assembled ``pack.yaml`` + ``manifest.yaml`` + ``test_pack.py``), then in a subprocess
(1) LOAD ``rules.clp`` into a real ``fathom.Engine`` (CLIPS compile — catches malformed
rules/templates), (2) assert the fixture's input fact, FIRE the engine to quiescence,
and assert the rule produced an output fact matching the fixture's expected action, and
(3) SIGN the assembled pack tree with an ephemeral Ed25519 key and VERIFY it under a
mandatory-verify profile — proving ``rules.clp`` + ``pack.yaml`` + ``manifest.yaml`` form
one coherent, tree-hash-verifiable pack (the deploy/registration contract). Because the
asserts run against a live engine + the real signing path, a trivially-passing generated
test cannot land a pack whose rules don't compile, don't fire, or don't cohere as a
signable unit.

TRUST BOUNDARY: see :mod:`stargraph.skills._smith.gate` — tiers 2-3 execute
LLM-generated code in a subprocess (process isolation, not a sandbox); the contract tier
additionally compiles + FIRES the generated CLIPS rules on a Fathom engine in that
subprocess. CLIPS rules are not Python, but a rule engine still runs arbitrary matching
logic; treat a generated pack as untrusted and never run a smith privileged.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from stargraph.skills._smith.gate import (
    VerifierResult,
    all_passed,
    make_contract_tier,
    run_tiered_gate,
)

__all__ = [
    "MANIFEST_FILE",
    "PACK_FILE",
    "RULES_FILE",
    "TEST_FILE",
    "VerifierResult",
    "all_passed",
    "assemble_manifest_yaml",
    "assemble_pack_yaml",
    "run_full_gate",
    "verify_sources",
]

RULES_FILE = "rules.clp"
PACK_FILE = "pack.yaml"
MANIFEST_FILE = "manifest.yaml"
TEST_FILE = "test_pack.py"

# Identifiers (pack id / flavor / template name) interpolate into YAML; reduce them to
# a safe charset so an LLM-generated value carrying a newline/colon/quote can't inject
# descriptor keys into the landed (and then signed) pack. Kebab id + dotted CLIPS names
# are preserved; anything else is dropped.
_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def _safe_id(value: str) -> str:
    return _SAFE_ID.sub("", str(value)) or "x"


def assemble_pack_yaml(*, pack_name: str, flavor: str) -> str:
    """The pack descriptor — deterministic boilerplate, correct by construction."""
    return (
        "# SPDX-License-Identifier: Apache-2.0\n"
        f"id: {_safe_id(pack_name)}\n"
        'version: "1.0"\n'
        f"flavor: {_safe_id(flavor)}\n"
        'api_version: "1"\n'
        'stargraph_facts_version: "1.0"\n'
        f"rules_file: {RULES_FILE}\n"
    )


def assemble_manifest_yaml(*, pack_name: str, output_template: str) -> str:
    """The signable manifest — declares the fact template the pack provides."""
    return (
        "# SPDX-License-Identifier: Apache-2.0\n"
        f"id: {_safe_id(pack_name)}\n"
        'version: "1.0"\n'
        "requires:\n"
        '  stargraph_facts_version: "1.0"\n'
        '  api_version: "1"\n'
        "provides:\n"
        f'  - "stargraph_facts:{_safe_id(output_template)}"\n'
    )


# Driver executed in a subprocess: load the generated CLIPS rules into a Fathom
# engine, assert the fixture input fact + fire + check the output action, then sign +
# verify the assembled pack tree. The payload carries ``meta`` (the input/output
# template names + pack name) and ``fixture`` (the input slots + expected action slots).
_CONTRACT_DRIVER = """\
import json, sys
from pathlib import Path

from fathom import Engine


def _fail(msg):
    print(json.dumps({"ok": False, "msg": msg}))
    sys.exit(0)


contract = json.loads(Path("contract.json").read_text())
meta = contract.get("meta", {})
fixture = contract.get("fixture", {})
input_template = str(meta.get("input_template", ""))
output_template = str(meta.get("output_template", ""))
pack_name = str(meta.get("pack_name", "pack"))
input_slots = fixture.get("input", {}) or {}
expects = fixture.get("expects", {}) or {}

# 1. Compile rules.clp into a real Fathom engine (native CLIPS whole-file load).
eng = Engine(default_decision="deny")
try:
    eng._env.load("rules.clp")
except Exception as e:
    _fail(f"rules.clp did not compile as CLIPS: {type(e).__name__}: {e}")


def _clips_val(v):
    if isinstance(v, bool):
        return '"true"' if v else '"false"'
    if isinstance(v, (int, float)):
        return repr(v)
    # Strip s-expression metacharacters so an untrusted slot value cannot break out
    # of the asserted fact (process-isolated either way; this just keeps it well-formed).
    s = str(v).replace("\\\\", "").replace('"', "").replace("(", "").replace(")", "")
    return '"%s"' % s


# 2. Assert the fixture input fact, fire to quiescence, read the output facts.
slots = " ".join(f"({k} {_clips_val(v)})" for k, v in input_slots.items())
try:
    eng._env.assert_string(f"({input_template} {slots})")
except Exception as e:
    _fail(f"could not assert a {input_template!r} fact: {type(e).__name__}: {e}")
try:
    eng._env.run()
except Exception as e:
    _fail(f"firing the rules raised: {type(e).__name__}: {e}")
try:
    out_facts = [dict(f) for f in eng._env.find_template(output_template).facts()]
except Exception as e:
    _fail(f"output template {output_template!r} not defined by the pack: {e}")

if not out_facts:
    _fail(f"no {output_template!r} fact was asserted — the rule did not fire on the fixture input")
if expects and not any(
    all(str(f.get(k)) == str(want) for k, want in expects.items()) for f in out_facts
):
    _fail(f"no {output_template!r} fact matched expected {expects} (got {out_facts})")

# 3. Sign the assembled pack tree with an ephemeral key and verify it under a
# mandatory-verify profile: proves rules.clp + pack.yaml + manifest.yaml hash + verify
# as one coherent pack (the deploy/registration contract). Operators re-sign with their
# own key at deploy time; this only proves the tree is well-formed + signable.
from cryptography.hazmat.primitives import serialization as _ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from stargraph.bosun.signing import StaticTrustStore, sign_pack, verify_pack
from stargraph.serve.profiles import ClearedProfile

_key = Ed25519PrivateKey.generate()
_priv = _key.private_bytes(_ser.Encoding.PEM, _ser.PrivateFormat.PKCS8, _ser.NoEncryption())
_pub = _key.public_key().public_bytes(_ser.Encoding.PEM, _ser.PublicFormat.SubjectPublicKeyInfo)
_kid = "packsmith-ephemeral"
try:
    _token = sign_pack(tree=Path.cwd(), signing_key=_priv, key_id=_kid)
    _res = verify_pack(
        tree=Path.cwd(),
        token=_token,
        trust_store=StaticTrustStore({_kid: _pub}),
        profile=ClearedProfile(),
    )
except Exception as e:
    _fail(f"pack failed to sign/verify as a coherent tree: {type(e).__name__}: {e}")
if not _res.verified:
    _fail(f"pack signature did not verify: {_res.reason}")

print(json.dumps({"ok": True, "pack": pack_name, "actions": len(out_facts)}))
"""


def run_full_gate(
    work_dir: Path,
    files: dict[str, str],
    *,
    meta: dict[str, str],
    fixture: dict[str, Any],
) -> list[VerifierResult]:
    """static → contract → tests in ``work_dir``, short-circuiting on first failure.

    The contract tier loads the generated ``rules.clp`` into a Fathom engine, fires it
    on ``fixture``, and signs + verifies the assembled pack tree; see ``_CONTRACT_DRIVER``.
    """
    return run_tiered_gate(
        work_dir,
        files,
        contract_tier=make_contract_tier(_CONTRACT_DRIVER, {"meta": meta, "fixture": fixture}),
        test_file=TEST_FILE,
    )


def verify_sources(
    *,
    pack_name: str,
    flavor: str,
    input_template: str,
    output_template: str,
    rules_clp: str,
    test_source: str,
    fixture: dict[str, Any],
) -> tuple[bool, list[VerifierResult]]:
    """Run the full gate on a raw pack bundle in a throwaway temp dir.

    The convenience entry point for callers that hold source strings rather than a work
    dir — ``packsmith make``, the doctor preflight, and seed verification. Assembles the
    descriptors, then gates the whole bundle. Returns ``(passed, results)``.
    """
    files = {
        RULES_FILE: rules_clp,
        PACK_FILE: assemble_pack_yaml(pack_name=pack_name, flavor=flavor),
        MANIFEST_FILE: assemble_manifest_yaml(pack_name=pack_name, output_template=output_template),
        TEST_FILE: test_source,
    }
    meta = {
        "input_template": input_template,
        "output_template": output_template,
        "pack_name": pack_name,
    }
    with tempfile.TemporaryDirectory(prefix="packsmith-verify-") as d:
        results = run_full_gate(Path(d), files, meta=meta, fixture=fixture)
    return all_passed(results), results
