#!/usr/bin/env python3
"""
Row-count verification between a migration Source Instance and Target
Instance (spec.md FR-008/FR-009; data-model.md "Verification Report").

Table names are never hardcoded here — they come from the backend's own
`app.database.Base.metadata` (Decision 6, research.md), the same
mechanism `backend/alembic/env.py` already uses. This keeps the
FR-009 "does the target already have data" threshold and this module's
own PASS/FAIL report using one single, unambiguous definition of
"application table" (deliberately excluding `alembic_version`, which is
Alembic's own bookkeeping, not a declared SQLAlchemy model).

Never prints a DSN or any part of it — a connection error's message may
be logged (Principle VII A09 requires no secret in logs), but the DSN
string itself (which embeds the DB password) is never echoed here.
"""
import argparse
import os
import sys
from pathlib import Path

# app/config.py's Settings() requires DATABASE_URL/JWT_SECRET_KEY to be
# overridden from their insecure sentinel defaults before it will
# construct at all (see its _validate_security_settings). This module
# never uses app.database's own engine/session (it opens its own
# psycopg2 connections to whatever --source/--target DSN the operator
# passes) — these placeholders only satisfy that import-time validation,
# mirroring the exact pattern backend/tests/conftest.py already uses.
os.environ.setdefault("DATABASE_URL", "postgresql://placeholder:placeholder@localhost/placeholder")
os.environ.setdefault("JWT_SECRET_KEY", "pg-migrate-verify-placeholder-not-a-real-secret")  # nosec B105

import psycopg2  # noqa: E402
from psycopg2 import sql  # noqa: E402

try:
    # Works when run inside the backend's own container image, where /app
    # (the app package's own root) is already on PYTHONPATH.
    from app.database import Base  # noqa: E402
    import app.models  # noqa: E402,F401 -- registers every model on Base.metadata
except ModuleNotFoundError:
    # Works when run as a bare script from the host: `app` lives under the
    # sibling backend/ directory (scripts/pg-migrate/verify.py -> backend/).
    _BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(_BACKEND_DIR))
    from app.database import Base  # noqa: E402
    import app.models  # noqa: E402,F401

# Exit codes (spec.md Clarifications / tasks.md T021).
EXIT_PASS = 0
EXIT_MISMATCH = 1
EXIT_ERROR = 2


def application_table_names() -> list[str]:
    """The authoritative list of application tables — never hardcoded."""
    return sorted(Base.metadata.tables.keys())


def collect_table_counts(dsn: str) -> dict[str, int]:
    """Connect to `dsn` and return {table_name: row_count} for every
    application table. Read-only (FR-003): only ever issues SELECT.

    A table that doesn't exist yet counts as 0 rows rather than raising —
    this is the normal state of a freshly initialized target before its
    schema has been created (no migrations run, no restore applied yet),
    which per spec.md's Clarifications counts as "empty", not an error."""
    counts: dict[str, int] = {}
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
            existing_tables = {row[0] for row in cur.fetchall()}
            for table in application_table_names():
                if table not in existing_tables:
                    counts[table] = 0
                    continue
                cur.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                counts[table] = cur.fetchone()[0]
    finally:
        conn.close()
    return counts


def compare_counts(source_counts: dict[str, int], target_counts: dict[str, int]) -> dict:
    """Pure comparison — no DB access, no I/O. Given two {table: count}
    mappings, return a per-table match/mismatch report plus overall
    pass/fail (FR-008)."""
    tables = sorted(set(source_counts) | set(target_counts))
    per_table = []
    overall_pass = True
    for table in tables:
        source_count = source_counts.get(table)
        target_count = target_counts.get(table)
        match = source_count == target_count
        if not match:
            overall_pass = False
        per_table.append(
            {"table": table, "source_count": source_count, "target_count": target_count, "match": match}
        )
    return {"per_table": per_table, "overall_pass": overall_pass}


def target_has_data(target_counts: dict[str, int]) -> bool:
    """FR-009: a target counts as 'already has data' iff the sum of row
    counts across application tables is greater than zero. A schema with
    only alembic_version populated (zero rows in every application
    table) counts as empty."""
    return sum(target_counts.values()) > 0


def format_report(report: dict) -> str:
    lines = []
    for row in report["per_table"]:
        status = "OK" if row["match"] else "MISMATCH"
        lines.append(
            f"{row['table']:<30} source={row['source_count']!s:>10} "
            f"target={row['target_count']!s:>10}  {status}"
        )
    lines.append("PASS" if report["overall_pass"] else "FAIL")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare application-table row counts between two PostgreSQL databases."
    )
    parser.add_argument("--source", metavar="DSN", help="Source database DSN")
    parser.add_argument("--target", required=True, metavar="DSN", help="Target database DSN")
    parser.add_argument(
        "--check-target-empty",
        action="store_true",
        help="Only check whether --target already has application data (FR-009); "
        "exits 0 if empty, 1 if it already has data. --source is not needed in this mode.",
    )
    args = parser.parse_args(argv)

    if not args.check_target_empty and not args.source:
        parser.error("--source is required unless --check-target-empty is set")

    try:
        if args.check_target_empty:
            target_counts = collect_table_counts(args.target)
            if target_has_data(target_counts):
                print("target already contains application data — refusing to restore", file=sys.stderr)
                return EXIT_MISMATCH
            print("target is empty (application tables) — safe to restore into")
            return EXIT_PASS

        source_counts = collect_table_counts(args.source)
        target_counts = collect_table_counts(args.target)
    except psycopg2.Error as exc:
        # psycopg2's own error text does not echo the DSN/password back —
        # only the server-reported failure reason (Principle VII A09).
        print(f"verify.py: connection/setup error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    report = compare_counts(source_counts, target_counts)
    print(format_report(report))
    return EXIT_PASS if report["overall_pass"] else EXIT_MISMATCH


if __name__ == "__main__":
    raise SystemExit(main())
