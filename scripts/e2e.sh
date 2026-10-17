#!/usr/bin/env bash
# Browser end-to-end tests (feature 013): build the SPA, start the rig behind the production-like HTTPS edge,
# run Playwright (Chromium), tear everything down again (trap). Exit code = Playwright's.
#   scripts/e2e.sh [playwright args, e.g. tests/oil-deliveries.spec.ts -g probable]
# Env: SEC_BACKEND_DIR   backend source to test (default <repo>/backend; e.g. a `git worktree` of an older tag)
#      E2E_SKIP_BUILD=1  reuse frontend/dist (also skips npm ci there)
#      E2E_SKIP_INSTALL=1 do not run `npm ci` / `playwright install` in e2e/
#      --mutant <name>   (first argument) run against a temp copy of backend/ with e2e/mutants/<name>.patch applied:
#                        proof that the suite catches no-forwarded-proto, details-anonymous, details-admin-only
#      E2E_PW_DEPS=1     `playwright install --with-deps` (CI: system libraries for Chromium)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RIG="$REPO/security/rig"
log() { echo "[e2e] $*" >&2; }

TMP=""
if [ "${1:-}" = "--mutant" ]; then
  PATCH="$REPO/e2e/mutants/${2:?--mutant needs a name}.patch"
  [ -f "$PATCH" ] || { log "no such mutant: $PATCH"; exit 2; }
  shift 2
  TMP="$(mktemp -d)"
  cp -r "$REPO/backend" "$TMP/backend"
  rm -rf "$TMP/backend/.venv" "$TMP/backend/tests"
  patch -p1 -d "$TMP" --quiet < "$PATCH" || { log "mutant patch does not apply"; rm -rf "$TMP"; exit 2; }
  export SEC_BACKEND_DIR="$TMP/backend"
  log "mutant applied to $SEC_BACKEND_DIR"
fi

cleanup() {
  local rc=$?
  [ -n "$TMP" ] && rm -rf "$TMP"
  if [ -d "$RIG/.state" ]; then
    rm -rf "$RIG/.last-logs"; mkdir -p "$RIG/.last-logs"
    cp "$RIG"/.state/*.log "$RIG/.last-logs/" 2>/dev/null || true
  fi
  "$RIG/down.sh" || true
  exit $rc
}
trap cleanup EXIT
trap 'exit 130' INT TERM

command -v node >/dev/null && command -v npm >/dev/null || { log "node/npm not found"; exit 3; }

if [ "${E2E_SKIP_BUILD:-0}" != 1 ]; then
  log "building the frontend"
  ( cd "$REPO/frontend" && npm ci --no-audit --no-fund && npm run build ) >&2 || { log "frontend build failed"; exit 3; }
fi

if [ "${E2E_SKIP_INSTALL:-0}" != 1 ]; then
  log "installing the e2e dependencies and Chromium"
  ( cd "$REPO/e2e" && npm ci --no-audit --no-fund ) >&2 || { log "npm ci (e2e) failed"; exit 3; }
  if [ "${E2E_PW_DEPS:-0}" = 1 ]; then
    ( cd "$REPO/e2e" && npx playwright install --with-deps chromium ) >&2 || { log "playwright install failed"; exit 3; }
  else
    ( cd "$REPO/e2e" && npx playwright install chromium ) >&2 || { log "playwright install failed (see e2e/README.md for the container fallback)"; exit 3; }
  fi
fi

"$RIG/up-e2e.sh" || { log "rig failed to start"; exit 3; }
log "rig up, running the browser tests"
cd "$REPO/e2e" && npx playwright test "$@"
