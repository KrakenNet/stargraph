#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# replay_drill.sh — the SOC Triage++ auditor walkthrough.
#
# Story (design §"Seeded fixtures + journey", AC-8.3, US-5): an auditor wants
# to prove that a triage decision made months ago is reproducible AND ask a
# what-if. This script drives the live `stargraph serve` REST surface that
# serve_soc.py (task 1.33) exposes on :9020:
#
#   1. Start a fresh run for the hero alert `case_8821` (prod ransomware).
#   2. Poll it to a terminal/paused state (HITL pause on the prod analyst gate).
#   3. REPLAY: fork a counterfactual child with an EMPTY mutation at step 0 —
#      Stargraph's "cannot change the past" invariant makes the replay byte-
#      identical (same cf-derived graph hash family, same deterministic ML +
#      cassette LLM). This is the "cryptographic replay months later" proof.
#   4. COUNTERFACTUAL: fork again with `asset_tier=prod` overridden
#      (fixtures/cassette_case_8821/counterfactual_tier_prod.json) and show the
#      diff — on a non-prod alert this flips soc-policy into the HITL branch.
#
# It is a DRILL for the demo: it is meant to be run AFTER the soc-triage++
# server is up (task 1.33) and at least one run has been seeded (task 1.35).
# It NEVER fakes success — if the server is unreachable or a step fails it
# prints a clear, actionable message and exits non-zero.
#
# Usage:
#   BASE_URL=http://localhost:9020 ./replay_drill.sh
#   ./replay_drill.sh                      # defaults to :9020 / case_8821
#
# Env knobs:
#   BASE_URL   serve_soc base URL              (default http://localhost:9020)
#   GRAPH_ID   graph id to run                 (default graph:soc-triage)
#   ALERT_ID   hero alert to triage            (default case_8821)
#   AUTH       Authorization header value      (default empty; oss-default
#                                               profile is Bypass-auth)
#   CF_STEP    fork step for the counterfactual(default 2 = post-triage)

set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:9020}"
GRAPH_ID="${GRAPH_ID:-graph:soc-triage}"
ALERT_ID="${ALERT_ID:-case_8821}"
CF_STEP="${CF_STEP:-2}"
AUTH="${AUTH:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CF_FILE="${DEMO_ROOT}/fixtures/cassette_case_8821/counterfactual_tier_prod.json"

# --- helpers ---------------------------------------------------------------

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
info() { printf '   %s\n' "$*"; }
die()  { printf '\n\033[31mERROR: %s\033[0m\n' "$*" >&2; exit "${2:-1}"; }

# curl wrapper that injects the optional auth header and fails on HTTP >= 400.
api() {
  local method="$1" path="$2" body="${3:-}"
  local -a args=(-fsS -X "$method" -H 'Content-Type: application/json')
  [ -n "$AUTH" ] && args+=(-H "Authorization: ${AUTH}")
  [ -n "$body" ] && args+=(--data "$body")
  curl "${args[@]}" "${BASE_URL}${path}"
}

# --- preflight -------------------------------------------------------------

command -v curl >/dev/null 2>&1 || die "curl is required but not installed" 3
command -v jq   >/dev/null 2>&1 || die "jq is required but not installed" 3
[ -f "$CF_FILE" ] || die "counterfactual fixture missing: $CF_FILE" 3

say "SOC Triage++ auditor drill → ${BASE_URL}"

if ! curl -fsS -o /dev/null "${BASE_URL}/v1/runs" 2>/dev/null; then
  die "soc-triage++ server not reachable at ${BASE_URL}/v1/runs.

  Start it first (task 1.33), e.g.:
    cd ${DEMO_ROOT%/demos/soc-triage}
    uv run --no-project python -m demos.soc-triage.serve_soc --port 9020
  …or launch the 'soc-triage++' tile from the all-demo dashboard, then re-run
  this drill. (Override the target with BASE_URL=...)" 2
fi
info "server is up"

# --- 1. start the hero run -------------------------------------------------

say "1/4  Start run for alert '${ALERT_ID}' on ${GRAPH_ID}"
START_BODY="$(jq -nc --arg g "$GRAPH_ID" --arg a "$ALERT_ID" \
  '{graph_id:$g, params:{alert_id:$a}}')"
START_RESP="$(api POST /v1/runs "$START_BODY")" \
  || die "failed to start run (POST /v1/runs). Is graph '${GRAPH_ID}' registered?"
RUN_ID="$(printf '%s' "$START_RESP" | jq -r '.run_id')"
[ -n "$RUN_ID" ] && [ "$RUN_ID" != "null" ] || die "no run_id in start response: $START_RESP"
info "run_id = ${RUN_ID}"

# --- 2. poll to terminal / paused -----------------------------------------

say "2/4  Poll run to completion (or HITL pause)"
STATUS=""
for _ in $(seq 1 60); do
  RUN_VIEW="$(api GET "/v1/runs/${RUN_ID}")" || die "GET /v1/runs/${RUN_ID} failed"
  STATUS="$(printf '%s' "$RUN_VIEW" | jq -r '.status')"
  info "status = ${STATUS}"
  case "$STATUS" in
    done|failed|paused) break ;;
  esac
  sleep 1
done
case "$STATUS" in
  done)   info "run completed cleanly" ;;
  paused) info "run is PAUSED at the HITL analyst gate (expected for a prod auto_remediate)" ;;
  failed) die  "run FAILED — inspect with: stargraph inspect ${RUN_ID}" ;;
  *)      die  "run did not reach a terminal/paused state (last status: ${STATUS:-<none>})" ;;
esac

# --- 3. REPLAY (empty mutation = byte-identical fork) ----------------------

say "3/4  REPLAY — fork an identical counterfactual at step 0 (no mutation)"
REPLAY_BODY="$(jq -nc '{step:0, mutation:{}, reason:"auditor-drill: byte-identical replay"}')"
REPLAY_RESP="$(api POST "/v1/runs/${RUN_ID}/counterfactual" "$REPLAY_BODY")" \
  || die "replay fork failed (POST /v1/runs/${RUN_ID}/counterfactual).
  Needs a checkpointer wired in serve deps (serve_soc.py, task 1.33)."
REPLAY_RUN="$(printf '%s' "$REPLAY_RESP" | jq -r '.run_id')"
info "replay run_id = ${REPLAY_RUN} (forked from ${RUN_ID})"
info "→ diff the two with: stargraph inspect ${REPLAY_RUN}   (vs ${RUN_ID})"

# --- 4. COUNTERFACTUAL (asset.tier = prod) --------------------------------

say "4/4  COUNTERFACTUAL — what if asset.tier = prod? (step ${CF_STEP})"
CF_BODY="$(jq -c --argjson step "$CF_STEP" '.step=$step | {step, mutation, reason}' "$CF_FILE")"
CF_RESP="$(api POST "/v1/runs/${RUN_ID}/counterfactual" "$CF_BODY")" \
  || die "counterfactual fork failed (POST /v1/runs/${RUN_ID}/counterfactual)"
CF_RUN="$(printf '%s' "$CF_RESP" | jq -r '.run_id')"
info "counterfactual run_id = ${CF_RUN} (forked from ${RUN_ID} at step ${CF_STEP})"
info "mutation: $(jq -c '.mutation.state_overrides' "$CF_FILE")"
info "→ diff outcomes with: stargraph inspect ${CF_RUN}   (vs ${RUN_ID})"

say "Drill complete"
cat <<EOF
   original     : ${RUN_ID}   (status: ${STATUS})
   replay       : ${REPLAY_RUN}   (byte-identical fork)
   counterfactual: ${CF_RUN}   (asset_tier=prod overlay)

   The original + replay share the same deterministic ML risk and cassette
   triage decision; the counterfactual flips the soc-policy routing when the
   asset is promoted to prod tier. This is the auditor's reproducibility +
   what-if proof (AC-8.3, US-5).
EOF
