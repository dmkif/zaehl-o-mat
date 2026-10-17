#!/usr/bin/env bash
# F-014: verify migration 0008 on a throw-away PostgreSQL (podman, random loopback port).
#   (a) DB built by `alembic upgrade 0006`, then `upgrade head`: meters of every unit / type / integration insert fine
#   (b) DB built by Base.metadata.create_all + `alembic stamp 0006`: `upgrade head` leaves the enum labels unchanged
# Usage: scripts/verify-migration-enums.sh [python]   (python needs the backend requirements)
set -euo pipefail
PY="${1:-python}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${PG_IMAGE:-docker.io/library/postgres:18-trixie}"
NAME="zom-mig-verify-$$"
PORT="$(shuf -i 20000-40000 -n 1)"
PW="$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
cleanup() { podman rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT
podman run -d --name "$NAME" -e POSTGRES_PASSWORD="$PW" -p "127.0.0.1:${PORT}:5432" "$IMAGE" >/dev/null
for _ in $(seq 60); do
  podman exec "$NAME" pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1
done
sleep 2
psql_() { podman exec -e PGPASSWORD="$PW" "$NAME" psql -U postgres -v ON_ERROR_STOP=1 -tA "$@"; }
mkdb() { psql_ -c "DROP DATABASE IF EXISTS $1" -c "CREATE DATABASE $1" >/dev/null; }
url() { echo "postgresql://postgres:${PW}@127.0.0.1:${PORT}/$1"; }
labels() { psql_ -d "$1" -c "SELECT t.typname||'.'||e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid ORDER BY 1"; }

export JWT_SECRET_KEY="$(head -c 36 /dev/urandom | od -An -tx1 | tr -d " \n")"
export PYTHONPATH="$ROOT/backend"
cd "$ROOT/backend"
echo "== (a) migration-built database"
mkdb a
DATABASE_URL="$(url a)" "$PY" -m alembic upgrade 0006
echo "-- before 0008: creating a kwh meter must FAIL (reproduces F-014)"
if out="$(DATABASE_URL="$(url a)" "$PY" "$ROOT/scripts/_mig_enum_probe.py" 2>&1)"; then
  echo "unexpected: probe passed before 0008"; exit 1
fi
echo "$out" | grep -q "invalid input value for enum" || { echo "$out"; echo "failed for another reason"; exit 1; }
echo "   reproduced: invalid input value for enum"
DATABASE_URL="$(url a)" "$PY" -m alembic upgrade head
echo "-- after 0008: every unit / type / integration"
DATABASE_URL="$(url a)" "$PY" "$ROOT/scripts/_mig_enum_probe.py"
echo "-- re-run is idempotent"
DATABASE_URL="$(url a)" "$PY" -m alembic downgrade 0006
DATABASE_URL="$(url a)" "$PY" -m alembic upgrade head

echo "== (b) create_all-built database, stamped"
mkdb b
DATABASE_URL="$(url b)" "$PY" -c "from app.database import Base; import app.models; from sqlalchemy import create_engine; import os; Base.metadata.create_all(create_engine(os.environ['DATABASE_URL']))"
labels b > /tmp/zom-labels-before-$$
DATABASE_URL="$(url b)" "$PY" -m alembic stamp 0006
DATABASE_URL="$(url b)" "$PY" -m alembic upgrade head
labels b > /tmp/zom-labels-after-$$
diff /tmp/zom-labels-before-$$ /tmp/zom-labels-after-$$ && echo "labels unchanged: 0008 is a no-op on create_all databases"
DATABASE_URL="$(url b)" "$PY" "$ROOT/scripts/_mig_enum_probe.py"
rm -f /tmp/zom-labels-before-$$ /tmp/zom-labels-after-$$
echo "OK"
