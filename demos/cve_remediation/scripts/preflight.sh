#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# Tier 0 preflight for cve_remediation demo.
# Verifies every external dependency is reachable BEFORE iteration runs.
# Exit 0 = green; non-zero = stop, do not run graph.
#
#   Stack:    PG, pgvector, Redis, Neo4j/RyuGraph, mock-SN container
#   External: real PDI ServiceNow, LM endpoint (Ollama), CargoNet h11 hosts
#   Config:   ~/.config/harbor/nautilus.yaml symlink resolved
#
# Run:  bash demos/cve_remediation/scripts/preflight.sh

set -uo pipefail

DEMO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${DEMO_DIR}/.env"

# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

PASS=0
FAIL=0

ok()   { printf '  \033[32mOK\033[0m   %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m %s — %s\n' "$1" "$2"; FAIL=$((FAIL+1)); }
note() { printf '  ----  %s\n' "$1"; }

echo "=== preflight: cve_remediation ==="

# 1. nautilus.yaml symlink
TARGET="${HOME}/.config/harbor/nautilus.yaml"
SOURCE="${DEMO_DIR}/nautilus.yaml"
if [[ -L "$TARGET" ]] && [[ "$(readlink -f "$TARGET")" == "$(readlink -f "$SOURCE")" ]]; then
  ok "nautilus.yaml symlink"
elif [[ -f "$TARGET" ]]; then
  ok "nautilus.yaml present (not symlink)"
else
  bad "nautilus.yaml" "missing at $TARGET — run: ln -sf '$SOURCE' '$TARGET'"
fi

# 2. LM endpoint
LM_URL="${LLM_BASE_URL:-http://localhost:41001/v1}"
LM_RC=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 "${LM_URL}/models" 2>/dev/null || echo 000)
if [[ "$LM_RC" == "200" ]]; then
  ok "LM endpoint $LM_URL"
else
  bad "LM endpoint" "$LM_URL returned $LM_RC"
fi

# 3. Postgres
PG_PORT="${POSTGRES_PORT:-5439}"
if docker exec cve-rem-postgres pg_isready -U "${POSTGRES_USER:-harbor}" -d "${POSTGRES_DB:-cve_rem}" >/dev/null 2>&1; then
  ok "postgres :$PG_PORT"
else
  bad "postgres" "pg_isready failed on cve-rem-postgres"
fi

# 4. pgvector
PGV_PORT="${PGVECTOR_PORT:-5440}"
if docker exec cve-rem-pgvector pg_isready -U "${PGVECTOR_USER:-harbor}" -d "${PGVECTOR_DB:-cve_rem_vec}" >/dev/null 2>&1; then
  ok "pgvector :$PGV_PORT"
else
  bad "pgvector" "pg_isready failed on cve-rem-pgvector"
fi

# 5. Redis
REDIS_PORT="${REDIS_PORT:-6390}"
if docker exec cve-rem-redis redis-cli ping 2>/dev/null | grep -q PONG; then
  ok "redis :$REDIS_PORT"
else
  bad "redis" "PING failed on cve-rem-redis"
fi

# 6. Neo4j / RyuGraph (bolt :7687, http :7474)
NEO_HTTP="${NEO4J_HTTP_PORT:-7474}"
NEO_RC=$(curl -s -o /dev/null -w "%{http_code}" --max-time 4 "http://localhost:${NEO_HTTP}/" 2>/dev/null || echo 000)
if [[ "$NEO_RC" =~ ^(200|302|401)$ ]]; then
  ok "neo4j http :$NEO_HTTP (rc=$NEO_RC)"
else
  bad "neo4j" "http :$NEO_HTTP returned $NEO_RC"
fi

# 7. ServiceNow PDI reachability (no creds in argv; header from env)
SN_URL="${SERVICENOW_BASE_URL:-}"
if [[ -n "${SERVICENOW_USERNAME:-}" && -n "${SERVICENOW_PASSWORD:-}" && -n "$SN_URL" ]]; then
  SN_AUTH="$(printf '%s:%s' "$SERVICENOW_USERNAME" "$SERVICENOW_PASSWORD" | base64 -w0)"
  SN_RC=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
    -H "Authorization: Basic $SN_AUTH" \
    -H "Accept: application/json" \
    "${SN_URL%/}/api/now/table/sys_user?sysparm_limit=1" 2>/dev/null || echo 000)
  unset SN_AUTH
  if [[ "$SN_RC" == "200" ]]; then
    ok "ServiceNow PDI ${SN_URL}"
  else
    bad "ServiceNow PDI" "${SN_URL} returned $SN_RC"
  fi
else
  bad "ServiceNow PDI" "creds not in .env"
fi

# 8. CargoNet h11-* hosts
H11_COUNT=$(docker ps --format '{{.Names}}' | grep -c '^clab-cn-net-.*-h11-' || true)
if [[ "$H11_COUNT" -ge 30 ]]; then
  ok "CargoNet h11 hosts up: $H11_COUNT"
else
  bad "CargoNet h11" "only $H11_COUNT containers up (expected ≥30)"
fi

# 9. Audit dir writable
AUDIT_DIR="$(dirname "${NAUTILUS_AUDIT_PATH:-./.harbor/nautilus-audit.jsonl}")"
mkdir -p "$AUDIT_DIR" 2>/dev/null
if [[ -w "$AUDIT_DIR" ]]; then
  ok "audit dir $AUDIT_DIR writable"
else
  bad "audit dir" "$AUDIT_DIR not writable"
fi

echo
echo "=== summary ==="
echo "  PASS=$PASS  FAIL=$FAIL"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
