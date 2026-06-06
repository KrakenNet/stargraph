# Security Sign-off Rubric — Stargraph v1

**Status**: Phase-5 prerequisite. A Stargraph release cannot ship to a
cleared deployment until every item below is checked by a non-author
reviewer and the signoff template at the bottom is filled in.

**Spec ref**: `stargraph-serve-and-bosun` §12.2 (FR-64, FR-67, AC-8.4).

This document is the canonical 13-check rubric. Each check has a
**verifier** (the command/test/observation that proves the check) and
an **owner** (the code-area maintainer responsible if the check
regresses).

## Pre-release checks

1. **STRIDE matrix completeness** — `docs/security/threat-model.md`
   has all 36 cells filled (no `TBD`, no `???`). Every documented gap
   has a tracked post-1.0 issue link. Verifier: visual review +
   `grep -c '|' docs/security/threat-model.md | awk '$1 >= 36'`.
   Owner: security lead.

2. **Capability-deny audit emission** (task 3.22) — denying a
   capability at the route gate emits a `request_audit` event with
   `status="denied"` and `capability=<name>`. Verifier:
   `tests/integration/test_capability_deny_audit.py` green; manual
   curl probe against a `cleared` profile returns 403 + audit row
   visible in JSONL sink. Owner: serve-API maintainer.

3. **Pack signing alg-strict** (task 3.21) — the Bosun pack signature
   verifier rejects `none`, HS256, and RS256 algorithms; only Ed25519
   is accepted. Verifier:
   `tests/unit/bosun/test_signing_alg_strict.py` covers the three
   rejected algs + the accepted Ed25519 path. Owner: Bosun maintainer.

4. **TOFU + static allow-list pubkey distribution** (task 2.27) —
   first-pin records the pubkey to `~/.stargraph/known_packs.json`;
   subsequent loads compare to the pinned value; static allow-list in
   `stargraph.toml` overrides TOFU when present. Verifier:
   `tests/integration/test_pack_tofu.py` green. Owner: Bosun
   maintainer.

5. **Replay isolation** (task 3.22) — cf-runs forked via
   `GraphRun.counterfactual` do NOT execute side-effecting nodes
   (HTTP, file-write, broker-emit). The replay context blocks them at
   the side-effect gate. Verifier:
   `tests/integration/test_replay_isolation.py` covers the 3 side-
   effect categories. Owner: replay maintainer.

6. **NFS refusal at bootstrap** (task 3.31) — Stargraph refuses to start
   when the configured state directory lives on NFS/SMB/AFP (POSIX
   `statfs(2)` lookup). Verifier:
   `tests/unit/serve/test_lifecycle_fs_refusal.py` covers the four
   refused FS types + the accepted ext4/xfs/btrfs path. Owner: serve-
   lifecycle maintainer.

7. **Profile default-deny enforcement** (task 2.36) — under the
   `cleared` profile, the 7 mutation routes
   (cancel/pause/respond/counterfactual/artifacts r+w/broker) return
   403 when the request does not carry the matching capability grant.
   Verifier: `tests/integration/test_profile_default_deny.py`
   parametrized over the 7 routes. Owner: serve-API maintainer.

8. **`--allow-side-effects` startup gate under cleared** (task 2.37)
   — `stargraph serve --profile cleared` refuses to start unless the
   operator passes `--allow-side-effects` (or the equivalent
   `stargraph.toml` flag). The gate is profile-conditional; OSS-default
   does not require the flag. Verifier:
   `tests/unit/cli/test_serve_side_effects_gate.py`. Owner: CLI
   maintainer.

9. **WebSocket 1011 disconnect on slow consumer** (task 3.19) — the
   broadcast emit timeout (5s) closes the WS with code 1011 when a
   subscriber backs up past the timeout. Verifier:
   `tests/integration/test_ws_slow_consumer.py` (parametrized over
   3 consumer-stall scenarios). Owner: serve-broadcast maintainer.

10. **HITL audit body-hash NOT body-content** (task 3.13) — the
    HITL `respond` flow records a SHA-256 hash of the response body
    in the audit fact; never the body itself. Verifier:
    `tests/integration/test_hitl_body_hash.py` asserts the hash
    field is present and the body field is absent in the JSONL line.
    Owner: HITL maintainer.

11. **cf-respond fact carries `source=cf:<actor>`** (task 3.23,
    locked Decision #2) — replay-side respond facts emit with the
    `cf:` prefix on `source`. Verifier:
    `tests/integration/test_cf_respond_provenance.py`. Owner: replay
    maintainer.

12. **Audit walker pyright/ruff clean** — `scripts/lineage_audit.py`
    + `scripts/regen_openapi.py` pass `uv run ruff check scripts` and
    `uv run pyright scripts` with zero findings. Verifier:
    `make lint-scripts` (Makefile target).
    Owner: docs+scripts maintainer.

13. **Lineage audit script passes against a real run** — given a
    JSONL audit log from a non-trivial integration test run,
    `python scripts/lineage_audit.py --strict` exits 0. Verifier:
    capture the audit log from
    `tests/integration/test_full_run_with_hitl_and_cf.py`, then
    `python scripts/lineage_audit.py --audit-path <log> --strict`.
    Owner: security lead.

## Sign-off template

Copy this section into the release ticket. Reviewer fills in the
checklist with name, date, and observations. Reviewer must NOT be the
author of any of the changes shipping in the release.

```
Reviewer:        ____________________________________________
Email:           ____________________________________________
Review date:     ____________________________________________
Release tag:     v________________________________________
Auditor signature (Ed25519 hex): ____________________________

Pre-release checks
[ ]  1. STRIDE matrix complete (36/36 cells)
[ ]  2. Capability-deny audit emission verified
[ ]  3. Pack signing alg-strict (none/HS256/RS256 rejected)
[ ]  4. TOFU + static allow-list pubkey distribution
[ ]  5. Replay isolation (no side-effects)
[ ]  6. NFS refusal at bootstrap
[ ]  7. Profile default-deny enforcement (7 routes)
[ ]  8. --allow-side-effects startup gate under cleared
[ ]  9. WebSocket 1011 disconnect on slow consumer
[ ] 10. HITL audit body-hash (not body-content)
[ ] 11. cf-respond fact carries source=cf:<actor>
[ ] 12. Audit walker pyright/ruff clean
[ ] 13. Lineage audit script passes against a real run

Observations / open issues:
______________________________________________________________
______________________________________________________________
______________________________________________________________

Recommendation:  [ ] APPROVE   [ ] BLOCK
Rationale:
______________________________________________________________
______________________________________________________________
```

## Audit retention

- Sign-off forms are committed to `docs/security/signoffs/<tag>.md`
  alongside the release tag.
- Reviewer Ed25519 signature pinned per release; the static allow-
  list in `stargraph.toml` is updated when a reviewer rotates.
- A failed sign-off (BLOCK) does NOT prevent merging the source
  branch — it prevents tagging + publishing the release. The
  blocking observations are tracked as issues against the next
  patch release.
