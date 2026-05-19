#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
#
# cve_remediation live-run wrapper.
#
# Sources demos/cve_remediation/.env, sets HARBOR_CONFIG_DIR to the demo
# directory, optionally flips the broker / ServiceNow live toggles, and
# forwards every remaining arg to ``harbor run``.
#
# Usage:
#   ./scripts/run_live.sh                          # offline, dry-run
#   ./scripts/run_live.sh --live-broker            # broker on, SN dry-run
#   ./scripts/run_live.sh --live-broker --live-sn  # broker on, SN POST live
#   ./scripts/run_live.sh --cve-id CVE-2024-3094   # override seed
#   ./scripts/run_live.sh --graph graph/harbor.yaml --live-broker
#   ./scripts/run_live.sh --non-interactive        # auto-fail on HITL
#
# Anything after ``--`` is forwarded verbatim to ``harbor run`` so flags
# the wrapper doesn't know about (``--checkpoint``, ``--log-file``, ...)
# still work:
#   ./scripts/run_live.sh --live-broker -- --log-file /tmp/run.jsonl

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve the demo root + repo root regardless of where the script was
# invoked from. ``${BASH_SOURCE[0]}`` is the script's own path; ``%/*``
# strips the basename. ``cd ... && pwd`` canonicalises the path.
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEMO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${DEMO_DIR}/../.." && pwd)"
ENV_FILE="${DEMO_DIR}/.env"
DEFAULT_GRAPH="${DEMO_DIR}/graph/harbor.yaml"
DEFAULT_CVE_ID="CVE-2021-44228"

# ---------------------------------------------------------------------------
# Flag parsing -- order-independent. Recognised flags are consumed; the
# rest are passed through to ``harbor run``.
# ---------------------------------------------------------------------------

LIVE_BROKER=0
LIVE_SN=0
CVE_ID="${DEFAULT_CVE_ID}"
GRAPH=""
NON_INTERACTIVE=0
QUIET=0
PASSTHROUGH=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --live-broker)
            LIVE_BROKER=1
            shift
            ;;
        --live-sn|--live-servicenow)
            LIVE_SN=1
            shift
            ;;
        --cve-id)
            CVE_ID="$2"
            shift 2
            ;;
        --cve-id=*)
            CVE_ID="${1#*=}"
            shift
            ;;
        --graph)
            GRAPH="$2"
            shift 2
            ;;
        --graph=*)
            GRAPH="${1#*=}"
            shift
            ;;
        --non-interactive)
            NON_INTERACTIVE=1
            shift
            ;;
        --quiet|-q)
            QUIET=1
            shift
            ;;
        --)
            shift
            PASSTHROUGH+=("$@")
            break
            ;;
        -h|--help)
            sed -n '3,21p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            PASSTHROUGH+=("$1")
            shift
            ;;
    esac
done

if [[ -z "${GRAPH}" ]]; then
    GRAPH="${DEFAULT_GRAPH}"
fi

# ---------------------------------------------------------------------------
# Load .env. ``set -a`` exports every assigned var; ``set +a`` restores.
# ``--`` after `source` so a malicious .env can't smuggle a flag here.
# ---------------------------------------------------------------------------

if [[ ! -f "${ENV_FILE}" ]]; then
    echo "run_live.sh: missing ${ENV_FILE}" >&2
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# ---------------------------------------------------------------------------
# Apply toggles. Wrapper-set vars override anything the .env defines so
# the user's CLI intent always wins.
# ---------------------------------------------------------------------------

export HARBOR_CONFIG_DIR="${DEMO_DIR}"

if [[ "${LIVE_BROKER}" -eq 1 ]]; then
    export CVE_REM_LIVE_BROKER=1
else
    unset CVE_REM_LIVE_BROKER || true
fi

if [[ "${LIVE_SN}" -eq 1 ]]; then
    export HARBOR_SERVICENOW_LIVE=1
else
    unset HARBOR_SERVICENOW_LIVE || true
fi

# ---------------------------------------------------------------------------
# Build the harbor run argv. Bash arrays preserve quoting through the
# pass-through path so values with spaces survive.
# ---------------------------------------------------------------------------

ARGS=(
    "${GRAPH}"
    -i
    "cve_id=${CVE_ID}"
)
if [[ "${LIVE_BROKER}" -eq 1 ]]; then
    ARGS+=("--live-broker")
fi
if [[ "${NON_INTERACTIVE}" -eq 1 ]]; then
    ARGS+=("--non-interactive")
fi
if [[ "${QUIET}" -eq 1 ]]; then
    ARGS+=("--quiet")
fi
ARGS+=("${PASSTHROUGH[@]}")

# ---------------------------------------------------------------------------
# Banner. Make it OBVIOUS when SN writes are live so an operator can
# Ctrl-C if they didn't mean it.
# ---------------------------------------------------------------------------

echo "==========================================" >&2
echo "harbor run (cve_remediation live wrapper)"  >&2
echo "  graph         : ${GRAPH}"                  >&2
echo "  cve_id        : ${CVE_ID}"                 >&2
echo "  config dir    : ${HARBOR_CONFIG_DIR}"      >&2
if [[ "${LIVE_BROKER}" -eq 1 ]]; then
    echo "  broker        : LIVE (Nautilus reads + audit chain)" >&2
else
    echo "  broker        : offline (deterministic envelopes)"   >&2
fi
if [[ "${LIVE_SN}" -eq 1 ]]; then
    echo "  servicenow    : LIVE -- POSTs to ${SERVICENOW_BASE_URL:-<unset>}" >&2
    echo "                  (real CRs will be created)"                       >&2
else
    echo "  servicenow    : DRY-RUN (no network)"                             >&2
fi
echo "==========================================" >&2

# ---------------------------------------------------------------------------
# Dispatch. ``cd "${REPO_ROOT}"`` so ``uv run`` resolves the project's
# venv consistently regardless of caller cwd.
# ---------------------------------------------------------------------------

cd "${REPO_ROOT}"
exec uv run --no-project harbor run "${ARGS[@]}"
