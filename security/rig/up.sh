#!/usr/bin/env bash
# Start the security rig: postgres (podman), migrations, stand-ins, backend.
# Everything binds to 127.0.0.1 only, secrets are generated per run.
# Env knobs: SEC_BACKEND_DIR (backend source dir, default <repo>/backend; used for mutants),
#            SEC_PYTHON (python used to create the venv, default python3),
#            SEC_PG_IMAGE (default docker.io/library/postgres:18-trixie).
set -euo pipefail

RIG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(cd "$RIG_DIR/.." && pwd)"
REPO="$(cd "$SEC_DIR/.." && pwd)"
STATE="$RIG_DIR/.state"
BACKEND_DIR="${SEC_BACKEND_DIR:-$REPO/backend}"
PG_IMAGE="${SEC_PG_IMAGE:-docker.io/library/postgres:18-trixie}"
PG_CONTAINER=zom-sec-pg
VENV="$SEC_DIR/.venv"
LOOP=127.0.0.1

log() { echo "[rig] $*" >&2; }
die() { echo "[rig] ERROR: $*" >&2; exit 1; }

command -v podman >/dev/null || die "podman not found"
[ -f "$BACKEND_DIR/alembic.ini" ] || die "backend dir $BACKEND_DIR has no alembic.ini"

# --- clean slate (idempotent) -------------------------------------------------
if [ -e "$STATE/rig.env" ] || [ -d "$STATE/pids" ] || podman container exists "$PG_CONTAINER" 2>/dev/null; then
  log "previous rig state found, tearing it down first"
  "$RIG_DIR/down.sh" >&2
fi
mkdir -p "$STATE/pids" "$STATE/uploads"
chmod 700 "$STATE"

# --- python venv --------------------------------------------------------------
if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import fastapi, uvicorn, sqlalchemy, alembic, psycopg2, httpx, pytest, pytest_asyncio, pytest_timeout, authlib, PIL' 2>/dev/null; then
  log "creating security/.venv from security/requirements.txt"
  rm -rf "$VENV"
  "${SEC_PYTHON:-python3}" -m venv "$VENV"
  if ! "$VENV/bin/pip" install -q -r "$SEC_DIR/requirements.txt" >"$STATE/pip.log" 2>&1; then
    tail -20 "$STATE/pip.log" >&2
    die "pip install failed (see above). Retry with SEC_PYTHON=/path/to/python3.12, or point at an existing venv: ln -s <venv> security/.venv"
  fi
fi
PY="$VENV/bin/python"

# --- helpers ------------------------------------------------------------------
free_port() { "$PY" -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1])'; }
rand() { "$PY" -c "import secrets;print(secrets.token_urlsafe($1))"; }

PG_PORT=$(free_port); BACKEND_PORT=$(free_port); OIDC_PORT=$(free_port); GW_PORT=$(free_port); OCR_PORT=$(free_port)
PG_PASS=$(rand 18); JWT_SECRET=$(rand 48); SA_USER=rig-superadmin; SA_PASS=$(rand 18)
OIDC_SECRET=$(rand 24); GW_KEY=$(rand 24)

BACKEND_URL="http://$LOOP:$BACKEND_PORT"
DATABASE_URL="postgresql://rig:$PG_PASS@$LOOP:$PG_PORT/rig"
OIDC_URL="http://$LOOP:$OIDC_PORT"
GATEWAY_URL="http://$LOOP:$GW_PORT/v1"
OCR_URL="http://$LOOP:$OCR_PORT"
UPLOAD_DIR="$STATE/uploads"
BACKEND_LOG="$STATE/backend.log"

# refuse anything that is not loopback
for u in "$BACKEND_URL" "$DATABASE_URL" "$OIDC_URL" "$GATEWAY_URL" "$OCR_URL"; do
  case "$u" in
    *://127.0.0.1:*|*@127.0.0.1:*) ;;
    *) die "refusing non-loopback target: $u" ;;
  esac
done

# --- postgres -----------------------------------------------------------------
log "starting postgres ($PG_IMAGE) on $LOOP:$PG_PORT"
podman run -d --name "$PG_CONTAINER" \
  -p "$LOOP:$PG_PORT:5432" \
  -e POSTGRES_USER=rig -e POSTGRES_PASSWORD="$PG_PASS" -e POSTGRES_DB=rig \
  --label zom-sec-rig=1 \
  "$PG_IMAGE" >/dev/null 2>"$STATE/podman.err" </dev/null || { cat "$STATE/podman.err" >&2; die "podman run failed"; }
ok=0
for _ in $(seq 1 60); do
  # initdb restarts the server once; require two consecutive successes over TCP
  if podman exec "$PG_CONTAINER" pg_isready -h 127.0.0.1 -U rig -d rig >/dev/null 2>&1 \
     && "$PY" -c "import psycopg2,sys;psycopg2.connect(sys.argv[1]).close()" "$DATABASE_URL" 2>/dev/null; then
    ok=$((ok+1)); [ "$ok" -ge 2 ] && break
  else ok=0; fi
  sleep 1
done
[ "$ok" -ge 2 ] || { podman logs "$PG_CONTAINER" 2>&1 | tail -20 >&2; die "postgres did not become ready"; }

# --- clean env for all python processes --------------------------------------
# Never inherit DATABASE_URL & co. from the developer shell.
ENVI=(env -i "PATH=$VENV/bin:/usr/bin:/bin" "HOME=$STATE" LANG=C.UTF-8 PYTHONUNBUFFERED=1)

BACKEND_ENV=(
  "DATABASE_URL=$DATABASE_URL"
  "JWT_SECRET_KEY=$JWT_SECRET"
  "OIDC_CLIENT_ID=rig-client"
  "OIDC_CLIENT_SECRET=$OIDC_SECRET"
  "OIDC_DISCOVERY_URL=$OIDC_URL/.well-known/openid-configuration"
  "OIDC_REDIRECT_URI=$BACKEND_URL/api/auth/callback"
  "OIDC_ADMIN_GROUP=rig-admins"
  "OIDC_MANAGER_GROUP=rig-managers"
  "OIDC_USER_GROUP=rig-users"
  "OIDC_DEFAULT_MANAGER_GROUP=rig-managers"
  "SUPERADMIN_USER=$SA_USER"
  "SUPERADMIN_PASSWORD=$SA_PASS"
  "UPLOAD_PATH=$UPLOAD_DIR"
  "OCR_BACKEND=gateway"
  "GATEWAY_BASE_URL=$GATEWAY_URL"
  "GATEWAY_API_KEY=$GW_KEY"
  "OCR_URL=$OCR_URL"
  "APP_BASE_URL=$BACKEND_URL"
  "LOG_LEVEL=debug"
  "DEBUG=${SEC_RIG_APP_DEBUG:-true}"
  # The rig peer is loopback, which is in the default TRUSTED_PROXIES: a spoofed X-Forwarded-For would be honoured.
  # Default for the rig = a harmless non-loopback range (no peer is trusted); E-A07-001 (trusted-proxy variant)
  # restarts with 127.0.0.0/8 to test the rightmost-untrusted rule.
  "TRUSTED_PROXIES=${SEC_RIG_TRUSTED_PROXIES:-10.255.255.0/24}"
  "RIG_DISABLE_LIMITER=${RIG_DISABLE_LIMITER:-1}"   # read by rig/rig_app.py only (rate-limit tests restart with it unset)
)

printf '%s\n' "${BACKEND_ENV[@]}" >"$STATE/backend.env"   # used by conftest restart_backend
chmod 600 "$STATE/backend.env"

# --- migrations ---------------------------------------------------------------
log "alembic upgrade head"
( cd "$BACKEND_DIR" && "${ENVI[@]}" "${BACKEND_ENV[@]}" "$PY" -m alembic upgrade head ) >"$STATE/alembic.log" 2>&1 \
  || { tail -30 "$STATE/alembic.log" >&2; die "alembic failed"; }

# --- stand-ins ----------------------------------------------------------------
STANDIN_PIDS=""
start_standin() { # name port [extra args...]
  local name=$1 port=$2; shift 2
  local f="$RIG_DIR/standins/$name.py"
  if [ ! -f "$f" ]; then log "stand-in $name.py missing, skipped (port $port stays closed)"; return 0; fi
  # setsid directly as the background job: $! is then the real python pid (own process group)
  ( cd "$STATE"; setsid "${ENVI[@]}" "RIG_OIDC_URL=$OIDC_URL" "RIG_BACKEND_URL=$BACKEND_URL" "RIG_OIDC_CLIENT_ID=rig-client" \
      "RIG_OIDC_CLIENT_SECRET=$OIDC_SECRET" "RIG_GATEWAY_API_KEY=$GW_KEY" "RIG_REDIRECT_URI=$BACKEND_URL/api/auth/callback" \
      "$PY" "$f" --port "$port" "$@" >"$STATE/$name.log" 2>&1 </dev/null & echo $! >"$STATE/pids/$name" )
  STANDIN_PIDS="${STANDIN_PIDS:+$STANDIN_PIDS,}$(cat "$STATE/pids/$name")"
}
start_standin fake_oidc "$OIDC_PORT" --client-secret "$OIDC_SECRET"
start_standin fake_gateway "$GW_PORT" --api-key "$GW_KEY"
start_standin fake_ocr "$OCR_PORT"
for pair in "fake_oidc:$OIDC_PORT" "fake_gateway:$GW_PORT" "fake_ocr:$OCR_PORT"; do
  n=${pair%%:*}; p=${pair##*:}
  [ -f "$STATE/pids/$n" ] || continue
  up=0
  for _ in $(seq 1 30); do
    "$PY" - "$p" <<'PYEOF' 2>/dev/null && { up=1; break; }
import socket, sys
socket.create_connection(("127.0.0.1", int(sys.argv[1])), 0.5).close()
PYEOF
    sleep 0.5
  done
  [ "$up" = 1 ] || { tail -20 "$STATE/$n.log" >&2; die "stand-in $n did not open port $p"; }
done

# --- backend ------------------------------------------------------------------
log "starting backend on $BACKEND_URL (source: $BACKEND_DIR)"
( cd "$STATE"; setsid "${ENVI[@]}" "${BACKEND_ENV[@]}" "PYTHONPATH=$RIG_DIR" "$PY" -m uvicorn rig_app:app --app-dir "$BACKEND_DIR" \
    --host "$LOOP" --port "$BACKEND_PORT" --log-level debug --no-proxy-headers >"$BACKEND_LOG" 2>&1 </dev/null & echo $! >"$STATE/pids/backend" )
BACKEND_PID=$(cat "$STATE/pids/backend")
healthy=0
for _ in $(seq 1 60); do
  kill -0 "$BACKEND_PID" 2>/dev/null || { tail -30 "$BACKEND_LOG" >&2; die "backend exited during start-up"; }
  if "$PY" -c "import httpx,sys;sys.exit(0 if httpx.get(sys.argv[1]+'/api/health/live',timeout=2).status_code==200 else 1)" "$BACKEND_URL" 2>/dev/null; then healthy=1; break; fi
  sleep 1
done
[ "$healthy" = 1 ] || { tail -30 "$BACKEND_LOG" >&2; die "backend not healthy"; }

# --- state file ---------------------------------------------------------------
cat >"$STATE/rig.env.tmp" <<EOT
BACKEND_URL=$BACKEND_URL
DATABASE_URL=$DATABASE_URL
PG_CONTAINER=$PG_CONTAINER
PG_PORT=$PG_PORT
JWT_SECRET_KEY=$JWT_SECRET
SUPERADMIN_USER=$SA_USER
SUPERADMIN_PASSWORD=$SA_PASS
UPLOAD_DIR=$UPLOAD_DIR
OIDC_URL=$OIDC_URL
OIDC_CLIENT_ID=rig-client
OIDC_CLIENT_SECRET=$OIDC_SECRET
GATEWAY_URL=$GATEWAY_URL
GATEWAY_API_KEY=$GW_KEY
OCR_URL=$OCR_URL
BACKEND_PID=$BACKEND_PID
STANDIN_PIDS=$STANDIN_PIDS
BACKEND_LOG=$BACKEND_LOG
BACKEND_DIR=$BACKEND_DIR
EOT
chmod 600 "$STATE/rig.env.tmp"; mv "$STATE/rig.env.tmp" "$STATE/rig.env"

# --- seed ---------------------------------------------------------------------
log "seeding synthetic data"
( cd "$BACKEND_DIR" && "${ENVI[@]}" "PYTHONPATH=$BACKEND_DIR" "$PY" "$RIG_DIR/seed.py" ) >"$STATE/seed.out" 2>"$STATE/seed.err" \
  || { cat "$STATE/seed.err" >&2; die "seed failed"; }
log "rig up: backend $BACKEND_URL, state $STATE/rig.env"
