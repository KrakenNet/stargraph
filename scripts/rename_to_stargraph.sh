#!/usr/bin/env bash
# Staged rename: stargraph -> stargraph (repo, package, CLI, env vars, configs).
# Solo-maintainer clean break: no compat shims. Old persisted runs will not
# replay/hash-verify after this (module paths feed graph hashes).
#
# Usage: scripts/rename_to_stargraph.sh <stage|all>
#   Stages: preflight github mv replace rebuild test commit
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

# Never touched: VCS, venvs, build artifacts, runtime state, embedded worktrees
# (.git, .venv, dist, .worktree, .stargraph, .checkpoints, data, node_modules).

stage_preflight() {
  echo "== preflight"
  [ -z "$(git status --porcelain)" ] || { echo "FATAL: dirty tree"; exit 1; }
  [ "$(git branch --show-current)" = "main" ] || { echo "FATAL: not on main"; exit 1; }
  git fetch origin && git diff --quiet origin/main main || { echo "FATAL: main != origin/main"; exit 1; }
  echo "preflight ok"
}

stage_github() {
  echo "== github repo rename"
  gh repo rename stargraph -R KrakenNet/stargraph --yes
  git remote set-url origin https://github.com/KrakenNet/stargraph
  git fetch origin
  echo "remote now: $(git remote get-url origin)"
}

stage_mv() {
  echo "== git mv stargraph-named paths"
  git mv src/stargraph src/stargraph
  # Every other tracked file/dir with 'stargraph' in its name (deepest first so
  # parent dir renames don't invalidate child paths).
  git ls-files | grep -i stargraph | grep -v '^src/stargraph' | awk -F/ '{print NF, $0}' | sort -rn | cut -d' ' -f2- | while read -r f; do
    [ -e "$f" ] || continue  # already moved with a parent dir
    new=$(echo "$f" | sed -e 's/stargraph/stargraph/g' -e 's/Stargraph/Stargraph/g' -e 's/STARGRAPH/STARGRAPH/g')
    mkdir -p "$(dirname "$new")"
    git mv "$f" "$new"
  done
  echo "moved: $(git diff --cached --name-only --diff-filter=R | wc -l) paths"
}

stage_replace() {
  echo "== content replace (3 case passes, tracked text files only)"
  excl_re="^(\.venv|dist|\.worktree|\.stargraph|\.checkpoints|data|node_modules)(/|$)"
  git ls-files | grep -vE "$excl_re" \
    | while read -r f; do
        grep -Iq . "$f" 2>/dev/null || continue   # skip binary
        grep -qiE 'stargraph' "$f" || continue
        sed -i -e 's/stargraph/stargraph/g' -e 's/Stargraph/Stargraph/g' -e 's/STARGRAPH/STARGRAPH/g' "$f"
      done
  echo "remaining (should be ~0):"
  git grep -ci stargraph -- . | head -20 || echo "none"
}

stage_rebuild() {
  echo "== rebuild venv + dist"
  rm -rf .venv dist
  python3 -m venv .venv
  .venv/bin/pip install -q -e ".[dev]" 2>/dev/null || .venv/bin/pip install -q -e .
  .venv/bin/stargraph --help >/dev/null && echo "CLI 'stargraph' ok"
  ! command -v .venv/bin/stargraph >/dev/null || { echo "FATAL: old 'stargraph' script still installed"; exit 1; }
}

stage_test() {
  echo "== tests"
  .venv/bin/python -m pytest -q -x
}

stage_commit() {
  echo "== commit + push"
  git add -A
  git commit -m "refactor!: rename stargraph -> stargraph (package, CLI, env vars, configs)

Clean break, no compat shims (solo deployment):
- package src/stargraph -> src/stargraph; all imports rewritten
- CLI console script: stargraph -> stargraph
- entry-point groups: stargraph.* -> stargraph.*
- env vars: STARGRAPH_* -> STARGRAPH_*
- config conventions: stargraph.yaml -> stargraph.yaml, ~/.stargraph -> ~/.stargraph
- repo: KrakenNet/stargraph -> KrakenNet/stargraph (redirect active)

BREAKING CHANGE: old run/checkpoint state hashed under stargraph.* module
paths will not replay or hash-verify.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
  git push origin main
}

case "${1:-}" in
  preflight|github|mv|replace|rebuild|test|commit) "stage_$1" ;;
  all) for s in preflight github mv replace rebuild test commit; do "stage_$s"; done ;;
  *) echo "usage: $0 <preflight|github|mv|replace|rebuild|test|commit|all>"; exit 1 ;;
esac
