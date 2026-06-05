#!/usr/bin/env bash
# Run smoke5 scoring across multiple LLM providers and compare results.
#
# Prerequisites:
#   1. harbor serve NOT running (this script starts/stops it per model)
#   2. CargoNet + h11 topology deployed
#   3. CMDB seeded
#   4. For Claude: run LiteLLM proxy (translates OpenAI format → Anthropic API)
#      pip install litellm[proxy]
#      litellm --model anthropic/claude-opus-4-7-20250219 --port 4001
#   5. For Gemini: Google AI Studio API key
#   6. For local models: vLLM/Ollama running on :41001
#
# Usage:
#   export OPENAI_API_KEY=sk-...
#   export ANTHROPIC_API_KEY=sk-ant-...
#   export GEMINI_API_KEY=AIza...
#   bash demos/cve_remediation/scripts/compare_models.sh

set -euo pipefail
DEMO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPTS="$DEMO_ROOT/scripts"
RESULTS_DIR="$DEMO_ROOT/model_comparison_$(date +%Y%m%dT%H%M%S)"
mkdir -p "$RESULTS_DIR"

# Source base env (SN creds, CargoNet, etc.)
set -a; source "$DEMO_ROOT/.env"; set +a

CHECKPOINT_DB="/tmp/score-checkpoints.sqlite"
AUDIT_LOG="/tmp/harbor-audit.jsonl"
SERVE_PORT=9001
SERVE_PID=""

# ── Model configurations ──
# Format: "label|base_url|model_id|api_key"
MODELS=(
    # --- Commercial APIs ---
    "gpt-5.5|https://api.openai.com/v1|gpt-5.5|${OPENAI_API_KEY:-skip}"
    # Claude via LiteLLM proxy (start separately: litellm --model anthropic/claude-opus-4-7 --port 4001)
    "claude-opus-4.7|http://localhost:4001/v1|anthropic/claude-opus-4-7|${ANTHROPIC_API_KEY:-skip}"
    "claude-sonnet-4.6|http://localhost:4001/v1|anthropic/claude-sonnet-4-6|${ANTHROPIC_API_KEY:-skip}"
    # Gemini (OpenAI-compatible endpoint)
    "gemini-3.1-pro|https://generativelanguage.googleapis.com/v1beta/openai|gemini-3.1-pro-preview|${GEMINI_API_KEY:-skip}"
    # --- Open source (local, assumes vLLM/Ollama on :41001) ---
    "gpt-oss-120b|http://localhost:41001/v1|gpt-oss:120b|placeholder"
    "nemotron3|http://localhost:41001/v1|nemotron-3-nano|placeholder"
    "lfm2|http://localhost:41001/v1|lfm2|placeholder"
    "olmo-3.1|http://localhost:41001/v1|olmo-3.1|placeholder"
    "phi4-mini|http://localhost:41001/v1|phi4-mini|placeholder"
    # "laguna-xs.2|http://localhost:41001/v1|laguna-xs.2|placeholder"
)

kill_serve() {
    if [ -n "$SERVE_PID" ] && kill -0 "$SERVE_PID" 2>/dev/null; then
        kill "$SERVE_PID" 2>/dev/null
        wait "$SERVE_PID" 2>/dev/null || true
    fi
    # Belt and suspenders
    pkill -f "harbor serve.*--port $SERVE_PORT" 2>/dev/null || true
    sleep 2
}

start_serve() {
    local base_url="$1" model="$2" api_key="$3"
    kill_serve

    # Fresh checkpoint DB per model (isolate results)
    rm -f "$CHECKPOINT_DB"
    # Fresh audit log per model
    rm -f "$AUDIT_LOG"

    export LLM_BASE_URL="$base_url"
    export LLM_MODEL="$model"
    export LLM_API_KEY="$api_key"

    uv run --no-project harbor serve \
        --port "$SERVE_PORT" \
        --db "$CHECKPOINT_DB" \
        --graph "$DEMO_ROOT/graph/harbor.yaml" \
        --allow-side-effects \
        --audit-log "$AUDIT_LOG" \
        --lm-url "$base_url" \
        --lm-model "$model" \
        --lm-key "$api_key" \
        --lm-timeout 120 \
        > "$RESULTS_DIR/${label}_serve.log" 2>&1 &
    SERVE_PID=$!
    echo "  serve pid=$SERVE_PID"

    # Wait for serve to be ready
    for i in $(seq 1 20); do
        if curl -s "http://127.0.0.1:${SERVE_PORT}/v1/graphs" >/dev/null 2>&1; then
            echo "  serve ready"
            return 0
        fi
        sleep 1
    done
    echo "  ! serve failed to start"
    return 1
}

echo "=== CVE-REM Model Comparison ==="
echo "  Results: $RESULTS_DIR"
echo "  Models:  ${#MODELS[@]}"
echo ""

for entry in "${MODELS[@]}"; do
    IFS='|' read -r label base_url model api_key <<< "$entry"

    if [ "$api_key" = "skip" ]; then
        echo "[$label] SKIP — API key not set"
        continue
    fi

    echo "[$label] Starting..."
    echo "  url=$base_url model=$model"

    if ! start_serve "$base_url" "$model" "$api_key"; then
        echo "[$label] FAILED to start serve"
        continue
    fi

    echo "  Running smoke5..."
    uv run --no-project python -m demos.cve_remediation.scripts.score_100 \
        --serve-base "http://127.0.0.1:${SERVE_PORT}" \
        --checkpoint-db "$CHECKPOINT_DB" \
        --audit-log "$AUDIT_LOG" \
        --limit 5 \
        > "$RESULTS_DIR/${label}_score.log" 2>&1 || true

    # Copy artifacts
    cp "$CHECKPOINT_DB" "$RESULTS_DIR/${label}_checkpoints.sqlite" 2>/dev/null || true
    cp "$AUDIT_LOG" "$RESULTS_DIR/${label}_audit.jsonl" 2>/dev/null || true

    # Copy generated report
    latest_report=$(ls -t .harbor/artifacts/scorecard/score100_*.md 2>/dev/null | head -1)
    if [ -n "$latest_report" ]; then
        cp "$latest_report" "$RESULTS_DIR/${label}_report.md"
    fi
    latest_jsonl=$(ls -t .harbor/artifacts/scorecard/score100_*.jsonl 2>/dev/null | head -1)
    if [ -n "$latest_jsonl" ]; then
        cp "$latest_jsonl" "$RESULTS_DIR/${label}_results.jsonl"
    fi

    kill_serve
    echo "[$label] Done."
    echo ""
done

echo "=== Generating comparison report ==="

# Build side-by-side comparison from per-model JSONL files
python3 << 'PYEOF'
import json, os, sys, statistics
from pathlib import Path
from collections import Counter

results_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")

# Find all *_results.jsonl files
model_data = {}
for f in sorted(results_dir.glob("*_results.jsonl")):
    label = f.stem.replace("_results", "")
    runs = []
    for line in f.read_text().strip().split("\n"):
        if line.strip():
            runs.append(json.loads(line))
    model_data[label] = runs

if not model_data:
    print("No results found.")
    sys.exit(0)

lines = []
ap = lines.append

ap("# CVE Remediation — Model Comparison Report")
ap("")
ap(f"**Models tested:** {len(model_data)}")
ap(f"**CVEs per model:** 5 (smoke set)")
ap("")

# Summary table
ap("## Summary")
ap("")
ap("| Model | Patched | Vulnerable | Errors | Mean Wall(s) | Median Wall(s) | Total Tokens |")
ap("|-------|---------|------------|--------|-------------|----------------|-------------|")

for label, runs in sorted(model_data.items()):
    outcomes = Counter(r.get("state", {}).get("verify_outcome", "?") for r in runs)
    walls = [r["wall_s"] for r in runs if r.get("wall_s", 0) > 0]
    tokens = sum((r.get("audit", {}) or {}).get("total_tokens", 0) for r in runs)
    errs = sum(1 for r in runs if r.get("status") not in ("done", "completed"))
    avg_w = f"{statistics.mean(walls):.0f}" if walls else "?"
    med_w = f"{statistics.median(walls):.0f}" if walls else "?"
    ap(f"| {label} | {outcomes.get('patched', 0)} | "
       f"{outcomes.get('vulnerable', 0)} | {errs} | "
       f"{avg_w} | {med_w} | {tokens:,} |")

ap("")

# Per-CVE comparison
ap("## Per-CVE Comparison")
ap("")
all_cves = set()
for runs in model_data.values():
    for r in runs:
        all_cves.add(r.get("cve_id", "?"))

headers = ["CVE"] + sorted(model_data.keys())
ap("| " + " | ".join(headers) + " |")
ap("| " + " | ".join(["---"] * len(headers)) + " |")

for cve in sorted(all_cves):
    row = [cve]
    for label in sorted(model_data.keys()):
        match = [r for r in model_data[label] if r.get("cve_id") == cve]
        if match:
            r = match[0]
            s = r.get("state", {})
            outcome = s.get("verify_outcome", "?")[:8]
            wall = f"{r['wall_s']:.0f}s"
            row.append(f"{outcome} ({wall})")
        else:
            row.append("—")
    ap("| " + " | ".join(row) + " |")

ap("")

# Quality metrics
ap("## Quality Metrics")
ap("")
ap("| Model | CMDB High | Plan Quality (mean bp) | CR Self-Val | Retro PG | Doc+ |")
ap("|-------|-----------|----------------------|-------------|----------|------|")

for label, runs in sorted(model_data.items()):
    cmdb_high = sum(1 for r in runs if r.get("state", {}).get("cmdb_match_quality") == "high")
    pq = [r.get("state", {}).get("plan_quality_score_bp", 0) or 0 for r in runs]
    mean_pq = f"{statistics.mean(pq):.0f}" if pq else "?"
    cr_val = sum(1 for r in runs if r.get("state", {}).get("cr_self_validation_passed"))
    retro = sum(1 for r in runs if r.get("state", {}).get("retro_pg_written"))
    docplus = sum(1 for r in runs if r.get("state", {}).get("docplus_published"))
    ap(f"| {label} | {cmdb_high}/5 | {mean_pq} | {cr_val}/5 | {retro}/5 | {docplus}/5 |")

ap("")
report = "\n".join(lines)
out = results_dir / "comparison_report.md"
out.write_text(report)
print(f"Comparison report: {out}")
PYEOF

echo ""
echo "=== All done ==="
echo "Results in: $RESULTS_DIR"
echo "  Per-model reports: ${RESULTS_DIR}/<model>_report.md"
echo "  Per-model JSONL:   ${RESULTS_DIR}/<model>_results.jsonl"
echo "  Comparison:        ${RESULTS_DIR}/comparison_report.md"
