#!/usr/bin/env bash
# Tear down the security rig. Idempotent; safe to run when nothing is up.
set -uo pipefail
RIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE="$RIG_DIR/.state"
PG_CONTAINER=zom-sec-pg

killtree() { # pid : TERM the process group (setsid made it a leader), then KILL
  local pid=$1
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || return 0; sleep 0.3; done
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
}

if [ -d "$STATE/pids" ]; then
  for f in "$STATE"/pids/*; do
    [ -f "$f" ] && killtree "$(cat "$f" 2>/dev/null)"
  done
fi
if command -v podman >/dev/null 2>&1; then
  podman rm -f -t 2 "$PG_CONTAINER" >/dev/null 2>&1 || true
fi
rm -rf "$STATE"
echo "[rig] down" >&2
exit 0
