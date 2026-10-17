#!/usr/bin/env bash
# One command: rig up -> pytest -m security -> rig down (always, via trap). Exit code = pytest's.
#   scripts/security-rig.sh [--quick] [--only '<pytest -k expr>'] [--mutant <name>] [-- extra pytest args]
#   --quick: race profile 20 rounds / 30 parallel sign-ins (default 100/100); rig runs with the rate limiter off
#            (RIG_DISABLE_LIMITER=1, security/rig/rig_app.py); only E-A07-001 restarts it with the real limiter.
# --mutant applies security/mutants/<name>.patch to a TEMP COPY of backend/ and runs the rig on it.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SEC="$REPO/security"
ONLY=""; MUTANT=""; EXTRA=()
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="${2:?--only needs a -k expression}"; shift 2 ;;
    --quick) export RIG_RACE_ROUNDS="${RIG_RACE_ROUNDS:-20}" RIG_RACE_SIGNINS="${RIG_RACE_SIGNINS:-30}"; shift ;;
    --mutant) MUTANT="${2:?--mutant needs a name}"; shift 2 ;;
    --) shift; EXTRA=("$@"); break ;;
    -h|--help) sed -n 2,8p "$0"; exit 0 ;;
    *) EXTRA+=("$1"); shift ;;
  esac
done

TMP=""
cleanup() {
  local rc=$?
  # keep logs of the last run (down.sh deletes the state dir)
  if [ -d "$SEC/rig/.state" ]; then
    rm -rf "$SEC/rig/.last-logs"; mkdir -p "$SEC/rig/.last-logs"
    cp "$SEC"/rig/.state/*.log "$SEC/rig/.last-logs/" 2>/dev/null || true
  fi
  "$SEC/rig/down.sh" || true
  [ -n "$TMP" ] && rm -rf "$TMP"
  exit $rc
}
trap cleanup EXIT
trap 'exit 130' INT TERM

if [ -n "$MUTANT" ]; then
  PATCH="$SEC/mutants/$MUTANT.patch"
  [ -f "$PATCH" ] || { echo "no such mutant: $PATCH" >&2; exit 2; }
  TMP="$(mktemp -d)"
  cp -r "$REPO/backend" "$TMP/backend"
  rm -rf "$TMP/backend/.venv" "$TMP/backend/tests"
  # patches are relative to the repo root (a/backend/... b/backend/...)
  if ! patch -p1 -d "$TMP" --quiet < "$PATCH"; then echo "mutant patch does not apply" >&2; exit 2; fi
  export SEC_BACKEND_DIR="$TMP/backend"
  echo "[security-rig] mutant $MUTANT applied to temp copy $SEC_BACKEND_DIR" >&2
fi

"$SEC/rig/up.sh" || { echo "[security-rig] rig failed to start" >&2; exit 3; }

ARGS=(-c "$SEC/pytest.ini" -m security --rootdir "$SEC" "$SEC/experiments")
[ -n "$ONLY" ] && ARGS+=(-k "$ONLY")
"$SEC/.venv/bin/python" -m pytest "${ARGS[@]}" "${EXTRA[@]}"
