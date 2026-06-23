<!-- SPDX-License-Identifier: Apache-2.0 -->
# SOC Triage++ — signed Bosun packs

Two governance packs, both **Ed25519/JWS-signed** with stargraph's real
`stargraph.bosun.signing.sign_pack` and verifiable with `verify_pack`:

| Pack         | id           | What it governs                                          |
| ------------ | ------------ | -------------------------------------------------------- |
| `soc-policy` | `soc-policy` | The 4 governance rules from `../soc-triage.md`           |
| `budgets`    | `soc-budgets`| Per-alert token / cost / latency caps                    |

## soc-policy rules (`soc-policy/rules.clp`)

1. **prod asset + `auto_remediate` disposition** → `interrupt` (HITL analyst sign-off).
2. **exec-owned asset + high severity** (risk band 2) → `escalate` (route to Tier 3).
3. **`risk_confidence < 0.6`** → `second_opinion` (re-run with a different model).
4. **Every rule firing emits a `bosun.provenance` fact** (`origin=rule`, rule id +
   run_id/step + decision) — the audit-trail invariant: `#actions == #provenance`.

Each firing asserts a `soc.policy.action` fact; the graph's `stargraph.yaml` routing
rules read those (`interrupt` / `escalate`) to drive the `analyst_gate` HITL branch.

## Signing — COMMITTED dev key (demo only)

```
keys/
  dev_signing_key.pem        <-- Ed25519 PKCS8 PRIVATE key, COMMITTED on purpose
  dev-soc-<8hex>.pub.pem      <-- matching public key
```

> ⚠️ **DEMO-ONLY KEY — DO NOT USE IN PRODUCTION.**
> `keys/dev_signing_key.pem` is a committed private signing key. This is a
> deliberate, resolved design decision (`specs/all-demo-ui` task 1.31):
> **reproducibility over secrecy** — anyone who checks out this repo can re-sign
> and re-verify the packs deterministically. In production, Bosun packs are signed
> with a key held in a KMS/HSM and only the `<key_id>.pub.pem` sidecar ships.

Each pack dir also carries the TOFU sidecar `<key_id>.pub.pem` (read by
`verify_pack`) and the detached `manifest.jwt` (compact EdDSA-JWT over the pack
tree-hash). The JWT `kid` header + payload `key_id` both equal `dev-soc-<8hex>`.

## Re-sign / verify

```bash
# Re-sign both packs with the committed dev key:
python - <<'PY'
from pathlib import Path
from stargraph.bosun.signing import sign_pack
key_id = "dev-soc-1cdb9c59"
priv = Path("keys/dev_signing_key.pem").read_bytes()
for p in ("soc-policy", "budgets"):
    (Path(p) / "manifest.jwt").write_text(sign_pack(Path(p), priv, key_id))
PY

# Verify a pack against the committed pubkey:
python - <<'PY'
from pathlib import Path
from stargraph.bosun.signing import StaticTrustStore, verify_pack
from stargraph.serve.profiles import ClearedProfile
key_id = "dev-soc-1cdb9c59"
pub = Path(f"keys/{key_id}.pub.pem").read_bytes()
trust = StaticTrustStore({key_id: pub})
for p in ("soc-policy", "budgets"):
    r = verify_pack(Path(p), (Path(p)/"manifest.jwt").read_text(), trust, ClearedProfile())
    print(p, r.verified, r.key_id)
PY
```
