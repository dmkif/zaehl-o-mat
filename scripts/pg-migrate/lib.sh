#!/usr/bin/env bash
# Shared helpers for scripts/pg-migrate/*.sh. Sourced, never executed
# directly — every function is namespaced pgmigrate:: to stay a good
# citizen when sourced into a caller's shell.
#
# Constitution Principle VII (A09): none of these helpers ever print a
# secret (DB password, gpg passphrase) to stdout/stderr/log — gpg's own
# passphrase prompt goes through pinentry, not through this script.

set -euo pipefail

# Decision 2 (research.md): use the *newer* image's client tools
# (pg_dump/pg_restore/psql) for both the PostgreSQL 16 source and the
# PostgreSQL 18 target.
PGMIGRATE_CLIENT_IMAGE="postgres:18-alpine"

# Decision 5 (research.md): symmetric encryption for the dump/uploads
# archive, at rest and in transit (FR-012).
PGMIGRATE_GPG_CIPHER="AES256"

# The container runtime used to run the ephemeral client image (Decision 2).
# Prefers podman (this project's primary local runtime) and falls back to
# docker so the tooling works on either.
pgmigrate::container_runtime() {
  if command -v podman >/dev/null 2>&1; then
    echo "podman"
  elif command -v docker >/dev/null 2>&1; then
    echo "docker"
  else
    pgmigrate::die "neither podman nor docker found on PATH"
  fi
}

pgmigrate::log() {
  echo "[pg-migrate] $*" >&2
}

pgmigrate::die() {
  pgmigrate::log "ERROR: $*"
  exit 1
}

# FR-011: check that at least $2 KB are free at path $1 before any
# destructive or long-running work begins. Fails fast with a clear
# message rather than partway through a dump/restore.
pgmigrate::check_disk_space() {
  local path="$1" required_kb="$2" available_kb
  available_kb=$(df -Pk "$path" | awk 'NR==2 {print $4}')
  if [[ -z "$available_kb" ]]; then
    pgmigrate::die "could not determine free disk space at $path"
  fi
  if (( available_kb < required_kb )); then
    pgmigrate::die "insufficient disk space at $path: ${available_kb}KB available, ${required_kb}KB required"
  fi
  pgmigrate::log "disk space OK at $path: ${available_kb}KB available (>= ${required_kb}KB required)"
}

# FR-012 (Decision 5): encrypt $1 (plaintext) to $2 (.gpg), prompting the
# operator for a passphrase interactively via gpg's own pinentry by
# default — this script never sees, stores, or logs the passphrase
# itself. PGMIGRATE_GPG_PASSPHRASE, if set, switches to non-interactive
# mode instead (passphrase read from that variable, never from argv/a
# file this script writes) — for the integration test's own use only;
# quickstart.md/README MUST NOT tell an operator to set it for a real
# migration.
pgmigrate::encrypt_to_file() {
  local input="$1" output="$2"
  if [[ -n "${PGMIGRATE_GPG_PASSPHRASE:-}" ]]; then
    gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 \
        --symmetric --cipher-algo "$PGMIGRATE_GPG_CIPHER" \
        --output "$output" "$input" <<<"$PGMIGRATE_GPG_PASSPHRASE"
  else
    gpg --batch --yes --symmetric --cipher-algo "$PGMIGRATE_GPG_CIPHER" \
        --output "$output" "$input"
  fi
}

# FR-012: decrypt $1 (.gpg) to $2 (plaintext), same interactive-passphrase
# contract (and PGMIGRATE_GPG_PASSPHRASE test-only override) as
# encrypt_to_file.
pgmigrate::decrypt_from_file() {
  local input="$1" output="$2"
  if [[ -n "${PGMIGRATE_GPG_PASSPHRASE:-}" ]]; then
    gpg --batch --yes --pinentry-mode loopback --passphrase-fd 0 \
        --output "$output" --decrypt "$input" <<<"$PGMIGRATE_GPG_PASSPHRASE"
  else
    gpg --batch --yes --output "$output" --decrypt "$input"
  fi
}

# FR-012: permanently remove every path given, once verification has
# passed. Prefers `shred -u`; falls back to `rm -f` on filesystems that
# don't support shred (e.g. some overlay/CSI volumes).
pgmigrate::secure_delete() {
  local f
  for f in "$@"; do
    [[ -e "$f" ]] || continue
    if command -v shred >/dev/null 2>&1 && shred -u -- "$f" 2>/dev/null; then
      continue
    fi
    rm -f -- "$f"
  done
}

# Validates $1 against an explicit allowlist $2... (Constitution Principle
# IX) — never executes an operator-supplied string as a free-form value.
pgmigrate::validate_choice() {
  local value="$1" flag_name="$2"; shift 2
  local choice
  for choice in "$@"; do
    if [[ "$value" == "$choice" ]]; then
      return 0
    fi
  done
  pgmigrate::die "invalid $flag_name: '$value' (expected one of: $*)"
}

# The podman/docker network a running container is attached to (its first
# one, by iteration order — this project's containers are only ever
# attached to a single user-defined network).
pgmigrate::discover_network_for_container() {
  local container="$1" runtime="$2"
  "$runtime" inspect "$container" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}'
}

# Finds the running Docker Compose 'db' service container regardless of
# whether it was started by docker-compose (name suffix "-1") or
# podman-compose (name suffix "_1").
pgmigrate::find_db_container() {
  local runtime="$1"
  "$runtime" ps --format '{{.Names}}' | grep -E '(^|[_-])db([_-]1)?$' | head -n1
}

# Compose/podman-compose prefixes named volumes with the project name
# (e.g. "uploads" in docker-compose.yaml becomes "zaehl-o-mat_uploads") —
# never hardcode the prefix, discover the real volume name instead.
pgmigrate::find_compose_volume() {
  local runtime="$1" short_name="$2"
  "$runtime" volume ls --format '{{.Name}}' | grep -E "(^|_)${short_name}\$" | head -n1
}

# The real host filesystem path backing a compose-managed named volume —
# rootless podman volumes are plain host directories the invoking user
# already owns, so no container is needed to read/write them directly.
pgmigrate::find_compose_volume_mount() {
  local runtime="$1" short_name="$2" volume
  volume=$(pgmigrate::find_compose_volume "$runtime" "$short_name")
  [[ -n "$volume" ]] || pgmigrate::die "could not find a compose volume matching '*${short_name}'"
  "$runtime" volume inspect "$volume" --format '{{.Mountpoint}}'
}

pgmigrate::wait_for_postgres_ready() {
  local runtime="$1" container="$2" i=0
  until "$runtime" exec "$container" pg_isready -U zaehlwart >/dev/null 2>&1; do
    i=$((i + 1))
    if (( i > 60 )); then
      pgmigrate::die "postgres container $container did not become ready in time"
    fi
    sleep 1
  done
}

# Runs verify.py inside an ephemeral container attached to the given
# network (or host networking if $1 is empty), reusing this project's own
# already-built backend image — it already has Python + psycopg2 +
# SQLAlchemy + the app package baked in, so no new external image
# dependency is introduced (Constitution Principle VII A03) just to reach
# an unpublished compose-network service like 'db'.
PGMIGRATE_VERIFY_IMAGE="${PGMIGRATE_VERIFY_IMAGE:-localhost/zaehl-o-mat_backend:latest}"

pgmigrate::run_verify() {
  local network="$1"; shift
  local runtime; runtime=$(pgmigrate::container_runtime)
  local net_args=(--network host)
  [[ -n "$network" ]] && net_args=(--network "$network")
  "$runtime" run --rm "${net_args[@]}" \
    -v "${REPO_ROOT}/scripts/pg-migrate/verify.py:/tmp/verify.py:ro" \
    -e PYTHONPATH=/app \
    -e DATABASE_URL="postgresql://placeholder:placeholder@localhost/placeholder" \
    -e JWT_SECRET_KEY="pg-migrate-verify-placeholder-not-a-real-secret" \
    "$PGMIGRATE_VERIFY_IMAGE" \
    python3 /tmp/verify.py "$@"
}
