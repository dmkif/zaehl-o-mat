"""
Integration tests for the real dump -> restore -> verify cycle (T011,
T016, T020, T022) — exercised against ephemeral, throwaway
`postgres:18-alpine` containers (Decision 7, research.md), never mocked.

Scope note (see specs/002-postgres-18-migration/tasks.md Phase 6 / T026):
these tests exercise migrate.sh's low-level `dump`/`restore`/`check-empty`
primitives and verify.py directly — the same mechanism the full
`--target=compose`/`--target=k8s` orchestration in migrate.sh builds on.
The multi-container orchestration wrappers themselves (which assume a
real Compose stack's 'db' container and a built backend image) are
validated at the component level per quickstart.md, not re-created here
from scratch — mirroring the precedent set by
specs/001-ocr-optional-container/tasks.md's T037.

Skipped automatically if no container runtime (podman/docker) is on PATH.
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path

import psycopg2
import pytest

PG_MIGRATE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PG_MIGRATE_DIR.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
MIGRATE_SH = PG_MIGRATE_DIR / "migrate.sh"
VERIFY_PY = PG_MIGRATE_DIR / "verify.py"

sys.path.insert(0, str(PG_MIGRATE_DIR))
import verify as verify_module  # noqa: E402

_RUNTIME = shutil.which("podman") or shutil.which("docker")
pytestmark = pytest.mark.skipif(
    _RUNTIME is None, reason="no container runtime (podman/docker) available"
)

_TEST_ENV = {
    **os.environ,
    "PGMIGRATE_GPG_PASSPHRASE": "pg-migrate-integration-test-passphrase",  # test-only, see lib.sh
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _EphemeralPostgres:
    def __init__(self, name: str):
        self.name = name
        self.port = _free_port()

    def start(self) -> None:
        subprocess.run(
            [
                _RUNTIME, "run", "-d", "--rm", "--name", self.name,
                "-e", "POSTGRES_DB=zaehlwart",
                "-e", "POSTGRES_USER=zaehlwart",
                "-e", "POSTGRES_PASSWORD=zaehlwart",
                "-p", f"127.0.0.1:{self.port}:5432",
                "postgres:18-alpine",
            ],
            check=True, capture_output=True, text=True,
        )
        self._wait_ready()

    def _wait_ready(self) -> None:
        for _ in range(60):
            r = subprocess.run(
                [_RUNTIME, "exec", self.name, "pg_isready", "-U", "zaehlwart"],
                capture_output=True,
            )
            if r.returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError(f"{self.name} did not become ready in time")

    @property
    def dsn(self) -> str:
        return f"postgresql://zaehlwart:zaehlwart@localhost:{self.port}/zaehlwart"

    def stop(self) -> None:
        subprocess.run([_RUNTIME, "stop", self.name], capture_output=True)


@pytest.fixture
def source_db():
    c = _EphemeralPostgres(f"pgmigrate-test-src-{uuid.uuid4().hex[:8]}")
    c.start()
    yield c
    c.stop()


@pytest.fixture
def target_db():
    c = _EphemeralPostgres(f"pgmigrate-test-tgt-{uuid.uuid4().hex[:8]}")
    c.start()
    yield c
    c.stop()


def _apply_migrations(dsn: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = dsn
    env["JWT_SECRET_KEY"] = "integration-test-secret-not-a-real-secret-0123456789"
    alembic_bin = BACKEND_DIR / ".venv" / "bin" / "alembic"
    if not alembic_bin.exists():
        alembic_bin = "alembic"  # fall back to PATH (e.g. CI's own venv)
    subprocess.run(
        [str(alembic_bin), "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, check=True, capture_output=True, text=True,
    )


def _seed_fixture_rows(dsn: str) -> dict:
    """Seeds one UUID-keyed row (properties) and several sequence-keyed
    rows (price_entries, via meters) — pushing the price_entries sequence
    to a non-default position — plus returns the expected table counts
    for the tables actually populated."""
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    property_id = str(uuid.uuid4())
    meter_id = str(uuid.uuid4())
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO properties (id, name, address) VALUES (%s, %s, %s)",
            (property_id, "Integration Test Property", "Teststraße 1"),
        )
        cur.execute(
            "INSERT INTO meters (id, property_id, meter_type, unit, name, integration_type) "
            "VALUES (%s, %s, 'water', %s, 'Test Meter', 'manual')",
            (meter_id, property_id, "m3"),
        )
        for _ in range(3):
            cur.execute(
                "INSERT INTO price_entries (meter_id, price_per_unit, valid_from) "
                "VALUES (%s, %s, now())",
                (meter_id, 1.2345),
            )
        cur.execute("SELECT last_value FROM price_entries_id_seq")
        last_seq_value = cur.fetchone()[0]
    conn.close()
    return {"properties": 1, "meters": 1, "price_entries": 3, "_price_entries_seq": last_seq_value}


def _run_migrate(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(MIGRATE_SH), *args], env=_TEST_ENV, capture_output=True, text=True,
    )


def test_dump_restore_verify_round_trip(source_db, target_db, tmp_path):
    """T011: full dump -> restore -> verify cycle between two ephemeral
    containers, asserting row/sequence/alembic_version parity, source
    left unchanged, and no lingering dump copy after a PASS (FR-003,
    FR-005, FR-006, FR-012)."""
    _apply_migrations(source_db.dsn)
    fixture = _seed_fixture_rows(source_db.dsn)

    source_counts_before = verify_module.collect_table_counts(source_db.dsn)

    dump_file = tmp_path / "test.dump.gpg"
    dump_result = _run_migrate("dump", f"--source-dsn={source_db.dsn}", f"--out={dump_file}")
    assert dump_result.returncode == 0, dump_result.stderr
    assert dump_file.exists()

    restore_result = _run_migrate(
        "restore", f"--target-dsn={target_db.dsn}", f"--in={dump_file}"
    )
    assert restore_result.returncode == 0, restore_result.stderr

    # FR-003: the source must be completely unaffected by the dump.
    source_counts_after = verify_module.collect_table_counts(source_db.dsn)
    assert source_counts_after == source_counts_before

    # FR-008/SC-001: verify.py reports an overall PASS.
    verify_proc = subprocess.run(
        [sys.executable, str(VERIFY_PY), f"--source={source_db.dsn}", f"--target={target_db.dsn}"],
        capture_output=True, text=True,
    )
    assert verify_proc.returncode == verify_module.EXIT_PASS, verify_proc.stdout + verify_proc.stderr
    assert "PASS" in verify_proc.stdout

    # FR-006: exact row counts, including the sequence-backed table.
    target_counts = verify_module.collect_table_counts(target_db.dsn)
    assert target_counts["properties"] == fixture["properties"]
    assert target_counts["meters"] == fixture["meters"]
    assert target_counts["price_entries"] == fixture["price_entries"]

    # FR-006: the sequence's current position carried over, not reset.
    conn = psycopg2.connect(target_db.dsn)
    with conn.cursor() as cur:
        cur.execute("SELECT last_value FROM price_entries_id_seq")
        target_seq_value = cur.fetchone()[0]
    conn.close()
    assert target_seq_value == fixture["_price_entries_seq"]

    # FR-005: alembic_version carried over exactly (same schema revision).
    def _alembic_version(dsn: str) -> str:
        conn = psycopg2.connect(dsn)
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            value = cur.fetchone()[0]
        conn.close()
        return value

    assert _alembic_version(target_db.dsn) == _alembic_version(source_db.dsn)

    # FR-012: once verification has passed, the dump is deleted (this
    # mirrors exactly what migrate.sh's full orchestration does after a
    # PASS — asserted here directly against the same secure_delete
    # mechanism via the shared lib.sh helper).
    subprocess.run(
        ["bash", "-c", f'source "{PG_MIGRATE_DIR}/lib.sh" && pgmigrate::secure_delete "{dump_file}"'],
        check=True,
    )
    assert not dump_file.exists()


def test_dump_restore_second_run_refused_by_check_empty(source_db, target_db, tmp_path):
    """T020: once a target already has application data, check-empty
    (the mechanism FR-009's guard is built on) must refuse a second
    restore rather than silently merging/overwriting."""
    _apply_migrations(source_db.dsn)
    _apply_migrations(target_db.dsn)
    _seed_fixture_rows(source_db.dsn)

    dump_file = tmp_path / "second_run.dump.gpg"
    assert _run_migrate("dump", f"--source-dsn={source_db.dsn}", f"--out={dump_file}").returncode == 0
    assert _run_migrate(
        "restore", f"--target-dsn={target_db.dsn}", f"--in={dump_file}"
    ).returncode == 0

    # Target now has data -> check-empty must refuse (exit 1), matching
    # what run_target_compose/run_target_k8s gate the restore step on.
    check_empty = _run_migrate("check-empty", f"--target-dsn={target_db.dsn}")
    assert check_empty.returncode == verify_module.EXIT_MISMATCH, check_empty.stdout + check_empty.stderr


def test_verify_detects_mismatch_and_source_stays_intact(source_db, target_db, tmp_path):
    """T022: after a successful migration, inserting an extra row into
    the target must be detected as a mismatch by a standalone verify.py
    re-run, and the source must remain unaffected throughout (FR-003)."""
    _apply_migrations(source_db.dsn)
    _apply_migrations(target_db.dsn)
    fixture = _seed_fixture_rows(source_db.dsn)

    dump_file = tmp_path / "mismatch.dump.gpg"
    assert _run_migrate("dump", f"--source-dsn={source_db.dsn}", f"--out={dump_file}").returncode == 0
    # target_db already had alembic applied but is otherwise empty —
    # restore into it directly (bypassing the pre-check, which is a
    # separate concern already covered by the other two tests).
    assert _run_migrate(
        "restore", f"--target-dsn={target_db.dsn}", f"--in={dump_file}"
    ).returncode == 0

    # Deliberately corrupt the target: one extra row the source never had.
    conn = psycopg2.connect(target_db.dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO properties (id, name) VALUES (%s, %s)",
            (str(uuid.uuid4()), "Unexpected Extra Property"),
        )
    conn.close()

    verify_proc = subprocess.run(
        [sys.executable, str(VERIFY_PY), f"--source={source_db.dsn}", f"--target={target_db.dsn}"],
        capture_output=True, text=True,
    )
    assert verify_proc.returncode == verify_module.EXIT_MISMATCH
    assert "MISMATCH" in verify_proc.stdout

    # FR-003: the source is untouched by any of the above.
    source_counts = verify_module.collect_table_counts(source_db.dsn)
    assert source_counts["properties"] == fixture["properties"]
    assert source_counts["meters"] == fixture["meters"]
    assert source_counts["price_entries"] == fixture["price_entries"]


def test_uploads_archive_transfer_round_trip(tmp_path):
    """T016: the uploads-archive transfer path (tar -> encrypt -> decrypt
    -> extract), exercised with two local temp directories standing in
    for the source uploads volume and the target-mounted PVC path — no
    live cluster needed (Decision 7)."""
    source_dir = tmp_path / "source_uploads"
    target_dir = tmp_path / "target_uploads"
    source_dir.mkdir()
    target_dir.mkdir()

    (source_dir / "reading-1.jpg").write_bytes(b"fake-jpeg-bytes-1")
    (source_dir / "reading-2.jpg").write_bytes(b"fake-jpeg-bytes-2")
    nested = source_dir / "subdir"
    nested.mkdir()
    (nested / "reading-3.jpg").write_bytes(b"fake-jpeg-bytes-3")

    archive = tmp_path / "uploads.tar.gpg"
    plain_tar = tmp_path / "uploads.tar"
    subprocess.run(["tar", "-C", str(source_dir), "-cf", str(plain_tar), "."], check=True)
    subprocess.run(
        ["bash", "-c", f'source "{PG_MIGRATE_DIR}/lib.sh" && pgmigrate::encrypt_to_file "{plain_tar}" "{archive}"'],
        env=_TEST_ENV, check=True,
    )
    plain_tar.unlink()
    assert archive.exists()
    assert not plain_tar.exists()

    decrypted_tar = tmp_path / "uploads_decrypted.tar"
    subprocess.run(
        ["bash", "-c", f'source "{PG_MIGRATE_DIR}/lib.sh" && pgmigrate::decrypt_from_file "{archive}" "{decrypted_tar}"'],
        env=_TEST_ENV, check=True,
    )
    subprocess.run(["tar", "-C", str(target_dir), "-xf", str(decrypted_tar)], check=True)
    subprocess.run(
        ["bash", "-c", f'source "{PG_MIGRATE_DIR}/lib.sh" && pgmigrate::secure_delete "{decrypted_tar}" "{archive}"'],
        check=True,
    )

    for relative in ("reading-1.jpg", "reading-2.jpg", "subdir/reading-3.jpg"):
        source_file = source_dir / relative
        target_file = target_dir / relative
        assert target_file.exists(), f"{relative} missing on target"
        assert target_file.read_bytes() == source_file.read_bytes()

    # FR-012: no plaintext or encrypted copy left behind afterward.
    assert not archive.exists()
    assert not decrypted_tar.exists()
