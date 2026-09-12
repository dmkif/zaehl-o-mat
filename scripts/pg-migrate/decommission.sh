#!/usr/bin/env bash
# The ONLY script permitted to remove a migration Source Instance's data
# (FR-010) — decommissioning is always a separate, explicit, manually-
# triggered action, never a side effect of migrate.sh or verify.py.
#
# Usage:
#   decommission.sh --volume=NAME
#
# Requires the operator to type the volume's name back exactly as an
# interactive confirmation before anything is deleted.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

usage() {
  cat <<'EOF'
Usage: decommission.sh --volume=NAME

Permanently removes the named container volume (e.g. an old
PostgreSQL-16 'db-data' volume left behind after a successful, verified
migration). Requires typing the volume name back as confirmation.

This script is never invoked by migrate.sh or verify.py — decommissioning
the source is always a separate, deliberate operator action (FR-010).
EOF
}

main() {
  local volume=""
  for arg in "$@"; do
    case "$arg" in
      --volume=*) volume="${arg#*=}" ;;
      -h|--help) usage; exit 0 ;;
      *) pgmigrate::die "unknown argument: $arg" ;;
    esac
  done
  [[ -n "$volume" ]] || { usage; pgmigrate::die "--volume is required"; }

  local runtime; runtime=$(pgmigrate::container_runtime)
  if ! "$runtime" volume inspect "$volume" >/dev/null 2>&1; then
    pgmigrate::die "no such volume: $volume"
  fi

  echo "This will PERMANENTLY delete volume '$volume' and everything in it."
  echo "This is irreversible. Only proceed after a passing verification"
  echo "report (User Story 3) has confirmed the migrated target is correct."
  echo
  read -r -p "Type the volume name ('$volume') to confirm deletion: " confirmation

  if [[ "$confirmation" != "$volume" ]]; then
    pgmigrate::die "confirmation did not match '$volume' — aborting, nothing deleted"
  fi

  "$runtime" volume rm "$volume"
  pgmigrate::log "volume '$volume' deleted."
}

main "$@"
