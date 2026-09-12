#!/usr/bin/env bash
# Postgres 16->18 data migration orchestrator (specs/002-postgres-18-migration/).
#
# One script, two targets (FR-002): --target=compose (local dev, in-place
# upgrade) and --target=k8s (Helm-deployed production cluster). Never
# writes to the source (FR-003); never restores into a non-empty target
# (FR-009); never deletes the source (FR-010, see decommission.sh).
#
# Subcommands (low-level primitives, reused by both the full orchestration
# below and by tests/test_migration_integration.py):
#   migrate.sh dump         --source-dsn=DSN --out=FILE.gpg [--network=NAME]
#   migrate.sh restore      --target-dsn=DSN --in=FILE.gpg  [--network=NAME]
#   migrate.sh check-empty  --target-dsn=DSN
#   migrate.sh --target=compose --confirm-maintenance-window
#   migrate.sh --target=k8s --namespace=NS --release=REL --confirm-maintenance-window

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# shellcheck source=lib.sh
source "$SCRIPT_DIR/lib.sh"

VERIFY_PY="$SCRIPT_DIR/verify.py"
PYTHON_BIN="${PGMIGRATE_PYTHON:-python3}"
COMPOSE_DB_DSN="postgresql://zaehlwart:zaehlwart@db:5432/zaehlwart"
TARGET_CONTAINER_NAME="pg-migrate-target"
TARGET_VOLUME_NAME="db-data-pg18"

usage() {
  cat <<'EOF'
Usage:
  migrate.sh dump         --source-dsn=DSN --out=FILE.gpg [--network=NAME]
  migrate.sh restore      --target-dsn=DSN --in=FILE.gpg  [--network=NAME]
  migrate.sh check-empty  --target-dsn=DSN [--network=NAME]
  migrate.sh --target=compose --confirm-maintenance-window
  migrate.sh --target=k8s --namespace=NS --release=REL --confirm-maintenance-window
EOF
}

_parse_kv_args() {
  # Populates the caller's associative array (named $1) from --key=value args ($2..).
  local -n _out="$1"; shift
  local arg
  for arg in "$@"; do
    case "$arg" in
      --*=*) _out["${arg%%=*}"]="${arg#*=}" ;;
      --confirm-maintenance-window) _out["--confirm-maintenance-window"]=1 ;;
      *) pgmigrate::die "unknown argument: $arg" ;;
    esac
  done
}

# ---------------------------------------------------------------------------
# Low-level primitives
# ---------------------------------------------------------------------------

cmd_dump() {
  declare -A a=()
  _parse_kv_args a "$@"
  local source_dsn="${a[--source-dsn]:-}" out="${a[--out]:-}" network="${a[--network]:-}"
  [[ -n "$source_dsn" ]] || pgmigrate::die "dump: --source-dsn is required"
  [[ -n "$out" ]] || pgmigrate::die "dump: --out is required"

  local runtime; runtime=$(pgmigrate::container_runtime)
  local net_args=(--network host)
  [[ -n "$network" ]] && net_args=(--network "$network")

  local plain; plain=$(mktemp)
  pgmigrate::check_disk_space "$(dirname "$plain")" 51200

  pgmigrate::log "dumping (read-only) via ephemeral $PGMIGRATE_CLIENT_IMAGE ..."
  if ! "$runtime" run --rm "${net_args[@]}" "$PGMIGRATE_CLIENT_IMAGE" \
      pg_dump -Fc --no-owner --no-privileges --dbname="$source_dsn" > "$plain"; then
    rm -f "$plain"
    pgmigrate::die "pg_dump failed"
  fi

  pgmigrate::encrypt_to_file "$plain" "$out"
  pgmigrate::secure_delete "$plain"
  pgmigrate::log "dump encrypted to $out"
}

cmd_restore() {
  declare -A a=()
  _parse_kv_args a "$@"
  local target_dsn="${a[--target-dsn]:-}" in="${a[--in]:-}" network="${a[--network]:-}"
  [[ -n "$target_dsn" ]] || pgmigrate::die "restore: --target-dsn is required"
  [[ -n "$in" ]] || pgmigrate::die "restore: --in is required"

  local runtime; runtime=$(pgmigrate::container_runtime)
  local net_args=(--network host)
  [[ -n "$network" ]] && net_args=(--network "$network")

  local plain; plain=$(mktemp)
  pgmigrate::decrypt_from_file "$in" "$plain"

  pgmigrate::log "restoring via ephemeral $PGMIGRATE_CLIENT_IMAGE ..."
  if ! "$runtime" run --rm -i "${net_args[@]}" "$PGMIGRATE_CLIENT_IMAGE" \
      pg_restore --no-owner --no-privileges --clean --if-exists --dbname="$target_dsn" < "$plain"; then
    pgmigrate::secure_delete "$plain"
    pgmigrate::die "pg_restore failed"
  fi
  pgmigrate::secure_delete "$plain"
  pgmigrate::log "restore complete"
}

# FR-009: exits 0 if the target is empty (safe to restore into), 1 if it
# already has application data, 2 on a connection/setup error.
cmd_check_empty() {
  declare -A a=()
  _parse_kv_args a "$@"
  local target_dsn="${a[--target-dsn]:-}"
  [[ -n "$target_dsn" ]] || pgmigrate::die "check-empty: --target-dsn is required"

  "$PYTHON_BIN" "$VERIFY_PY" --check-target-empty --target="$target_dsn"
}

# ---------------------------------------------------------------------------
# Shared full-run helpers
# ---------------------------------------------------------------------------

_require_maintenance_window_confirmed() {
  local confirmed="$1"
  [[ -n "$confirmed" ]] || pgmigrate::die \
    "refusing to run without --confirm-maintenance-window (FR-007). Stop the application (or make the source database read-only) before dumping, then re-run with --confirm-maintenance-window."
}

# ---------------------------------------------------------------------------
# --target=compose orchestration (User Story 1)
# ---------------------------------------------------------------------------

run_target_compose() {
  local confirmed="$1"
  _require_maintenance_window_confirmed "$confirmed"

  local runtime; runtime=$(pgmigrate::container_runtime)
  local db_container; db_container=$(pgmigrate::find_db_container "$runtime")
  [[ -n "$db_container" ]] || pgmigrate::die "no running compose 'db' container found — is the stack up (with only backend/frontend/proxy stopped per the maintenance window)?"
  local network; network=$(pgmigrate::discover_network_for_container "$db_container" "$runtime")
  [[ -n "$network" ]] || pgmigrate::die "could not determine the compose network for $db_container"

  local dump_file; dump_file="$(mktemp -u "${TMPDIR:-/tmp}/pg-migrate-XXXXXX")".dump.gpg

  pgmigrate::log "Step 1/4: dumping source ($db_container, read-only)..."
  cmd_dump --source-dsn="$COMPOSE_DB_DSN" --out="$dump_file" --network="$network"

  pgmigrate::log "Step 2/4: initializing fresh PostgreSQL 18 target ($TARGET_CONTAINER_NAME, volume $TARGET_VOLUME_NAME)..."
  "$runtime" volume create "$TARGET_VOLUME_NAME" >/dev/null 2>&1 || true
  "$runtime" rm -f "$TARGET_CONTAINER_NAME" >/dev/null 2>&1 || true
  # PGDATA in a subdirectory of the mount, not at its root: postgres:18+
  # images refuse to start otherwise ("unused mount/volume" check) —
  # matches docker-compose.yaml's db service and the Helm chart's
  # statefulset-postgresql.yaml, both of which need the same workaround.
  "$runtime" run -d --name "$TARGET_CONTAINER_NAME" --network "$network" \
    -e POSTGRES_DB=zaehlwart -e POSTGRES_USER=zaehlwart -e POSTGRES_PASSWORD=zaehlwart \
    -e PGDATA=/var/lib/postgresql/data/pgdata \
    -v "${TARGET_VOLUME_NAME}:/var/lib/postgresql/data" \
    "$PGMIGRATE_CLIENT_IMAGE" >/dev/null
  pgmigrate::wait_for_postgres_ready "$runtime" "$TARGET_CONTAINER_NAME"

  local target_dsn="postgresql://zaehlwart:zaehlwart@${TARGET_CONTAINER_NAME}:5432/zaehlwart"

  pgmigrate::log "Checking target is empty (FR-009)..."
  if ! pgmigrate::run_verify "$network" --check-target-empty --target="$target_dsn"; then
    "$runtime" rm -f "$TARGET_CONTAINER_NAME" >/dev/null 2>&1 || true
    pgmigrate::die "target already contains application data — refusing to restore"
  fi

  pgmigrate::log "Step 3/4: restoring into target..."
  cmd_restore --target-dsn="$target_dsn" --in="$dump_file" --network="$network"

  pgmigrate::log "Step 4/4: verifying..."
  if pgmigrate::run_verify "$network" --source="$COMPOSE_DB_DSN" --target="$target_dsn"; then
    pgmigrate::secure_delete "$dump_file"
    "$runtime" stop "$TARGET_CONTAINER_NAME" >/dev/null
    pgmigrate::log "PASS — dump securely deleted."
    cat <<EOF

Migration verified. Next steps (manual, deliberate — this script will not
do these for you, per FR-010):

  podman-compose stop db
  podman volume rm ... # (inspect first) rename the OLD 'db-data' volume aside if you want to keep it
  podman run --rm -v db-data:/from -v ${TARGET_VOLUME_NAME}:/to alpine sh -c 'true'  # example only — see quickstart.md
  # Point docker-compose.yaml's db volume at ${TARGET_VOLUME_NAME} (or rename
  # the volumes so ${TARGET_VOLUME_NAME} becomes db-data), then:
  podman-compose up -d
EOF
  else
    pgmigrate::log "FAIL — target left in place at container '$TARGET_CONTAINER_NAME' / volume '$TARGET_VOLUME_NAME' for inspection. Dump NOT deleted: $dump_file"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# --target=k8s orchestration (User Story 2, Decision 8)
# ---------------------------------------------------------------------------

_k8s_pod_for_component() {
  local namespace="$1" release="$2" component="$3"
  kubectl -n "$namespace" get pod \
    -l "app.kubernetes.io/instance=${release},app.kubernetes.io/component=${component}" \
    -o jsonpath='{.items[0].metadata.name}'
}

run_target_k8s() {
  local confirmed="$1" namespace="$2" release="$3"
  _require_maintenance_window_confirmed "$confirmed"
  [[ -n "$namespace" && -n "$release" ]] || pgmigrate::die "--target=k8s requires --namespace and --release"

  local runtime; runtime=$(pgmigrate::container_runtime)
  local db_container; db_container=$(pgmigrate::find_db_container "$runtime")
  [[ -n "$db_container" ]] || pgmigrate::die "no running compose 'db' container found — the source is always the local Compose instance (data-model.md)"
  local network; network=$(pgmigrate::discover_network_for_container "$db_container" "$runtime")

  local pg_pod; pg_pod=$(_k8s_pod_for_component "$namespace" "$release" postgresql)
  [[ -n "$pg_pod" ]] || pgmigrate::die "could not find the postgresql pod for release '$release' in namespace '$namespace'"
  local backend_pod; backend_pod=$(_k8s_pod_for_component "$namespace" "$release" backend)
  [[ -n "$backend_pod" ]] || pgmigrate::die "could not find the backend pod for release '$release' in namespace '$namespace' (needed to reach the uploads PVC)"

  local dump_file; dump_file="$(mktemp -u "${TMPDIR:-/tmp}/pg-migrate-XXXXXX")".dump.gpg
  local uploads_archive; uploads_archive="$(mktemp -u "${TMPDIR:-/tmp}/pg-migrate-uploads-XXXXXX")".tar.gpg

  pgmigrate::log "Step 1/5: dumping source ($db_container, read-only)..."
  cmd_dump --source-dsn="$COMPOSE_DB_DSN" --out="$dump_file" --network="$network"

  pgmigrate::log "Step 2/5: port-forwarding to the in-cluster database to check it is empty (FR-009)..."
  # Bound on 0.0.0.0 (not just 127.0.0.1) so a container on the compose
  # network can reach it via host.containers.internal later (Step 5) —
  # kubectl's own TLS/auth to the API server is unaffected; only the
  # local end of the tunnel is more widely reachable, same trust boundary
  # as the operator's own host.
  local pf_pid pf_local_port=15432
  kubectl -n "$namespace" port-forward --address=0.0.0.0 "pod/${pg_pod}" "${pf_local_port}:5432" >/dev/null 2>&1 &
  pf_pid=$!
  trap '[[ -n "${pf_pid:-}" ]] && kill "$pf_pid" 2>/dev/null || true' RETURN
  sleep 2
  local k8s_target_dsn_local="postgresql://zaehlwart:zaehlwart@localhost:${pf_local_port}/zaehlwart"
  local k8s_target_dsn_from_container="postgresql://zaehlwart:zaehlwart@host.containers.internal:${pf_local_port}/zaehlwart"
  if ! "$PYTHON_BIN" "$VERIFY_PY" --check-target-empty --target="$k8s_target_dsn_local"; then
    kill "$pf_pid" 2>/dev/null || true
    pgmigrate::die "target release '$release' already contains application data — refusing to restore"
  fi

  pgmigrate::log "Step 3/5: decrypting locally and restoring inside $pg_pod (its own pg_restore, per Decision 8)..."
  local plain_dump_tmp; plain_dump_tmp=$(mktemp)
  # Decrypted locally, then streamed in via exec stdin — the pod itself
  # never sees the encrypted file or needs a gpg passphrase prompt
  # (Constitution Principle III: the passphrase is only ever entered by
  # the operator, into their own local gpg invocation).
  pgmigrate::decrypt_from_file "$dump_file" "$plain_dump_tmp"
  kubectl -n "$namespace" exec -i "$pg_pod" -- \
    pg_restore --no-owner --no-privileges --clean --if-exists -U zaehlwart -d zaehlwart < "$plain_dump_tmp"
  pgmigrate::secure_delete "$plain_dump_tmp"

  pgmigrate::log "Step 4/5: transferring the uploads volume into the backend pod's PVC ($backend_pod)..."
  local uploads_mount; uploads_mount=$(pgmigrate::find_compose_volume_mount "$runtime" uploads)
  local uploads_plain; uploads_plain=$(mktemp)
  tar -C "$uploads_mount" -cf "$uploads_plain" .
  pgmigrate::encrypt_to_file "$uploads_plain" "$uploads_archive"
  pgmigrate::secure_delete "$uploads_plain"
  kubectl -n "$namespace" cp "$uploads_archive" "${backend_pod}:/tmp/pg-migrate-uploads.gpg"
  local uploads_remote_plain=/tmp/pg-migrate-uploads.tar
  kubectl -n "$namespace" exec "$backend_pod" -- sh -c \
    "gpg --batch --yes --output ${uploads_remote_plain} --decrypt /tmp/pg-migrate-uploads.gpg && tar -C /data/uploads -xf ${uploads_remote_plain} && rm -f ${uploads_remote_plain} /tmp/pg-migrate-uploads.gpg"
  pgmigrate::secure_delete "$uploads_archive"

  pgmigrate::log "Step 5/5: verifying (source reached over the compose network, target over the same port-forward)..."
  # Runs inside the backend image on the compose network (to reach 'db')
  # and reaches the kubectl port-forward via host.containers.internal —
  # the one step in this path that needs both source and target
  # reachable from the same process (Decision 8 covers restore/the
  # empty-check separately; only this final comparison needs both at once).
  local verify_status=0
  pgmigrate::run_verify "$network" --source="$COMPOSE_DB_DSN" --target="$k8s_target_dsn_from_container" || verify_status=$?
  kill "$pf_pid" 2>/dev/null || true
  pgmigrate::secure_delete "$dump_file"

  if [[ "$verify_status" -eq 0 ]]; then
    pgmigrate::log "PASS — dump and uploads archive securely deleted from every staged location."
  else
    pgmigrate::log "FAIL (exit $verify_status) — target left in place for inspection."
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

main() {
  [[ $# -gt 0 ]] || { usage; exit 1; }

  case "$1" in
    dump) shift; cmd_dump "$@"; return ;;
    restore) shift; cmd_restore "$@"; return ;;
    check-empty) shift; cmd_check_empty "$@"; return ;;
    -h|--help) usage; return ;;
  esac

  declare -A a=()
  _parse_kv_args a "$@"
  local target="${a[--target]:-}"
  pgmigrate::validate_choice "$target" "--target" compose k8s

  case "$target" in
    compose) run_target_compose "${a[--confirm-maintenance-window]:-}" ;;
    k8s) run_target_k8s "${a[--confirm-maintenance-window]:-}" "${a[--namespace]:-}" "${a[--release]:-}" ;;
  esac
}

main "$@"
