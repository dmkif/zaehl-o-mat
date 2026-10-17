"""Feature 011 / F-022: migration 0009 de-duplicates without deleting data."""
import importlib.util
from pathlib import Path

import sqlalchemy as sa

_PATH = next((Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("0009_*.py"))
_spec = importlib.util.spec_from_file_location("mig0009", _PATH)
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def test_revision_chain():
    assert mig.revision == "0009" and mig.down_revision == "0008"


def test_dedupe_nulls_newer_duplicates_keeps_rows():
    eng = sa.create_engine("sqlite://")
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE readings (id INTEGER PRIMARY KEY, meter_id TEXT, image_hash TEXT, image_path TEXT)"))
        rows = [
            (1, "m1", "h", "uploads/a"), (2, "m1", "h", "uploads/b"), (3, "m1", "h", "uploads/c"),
            (4, "m2", "h", "uploads/d"),            # same hash, other meter: untouched
            (5, "m1", None, None), (6, "m1", None, None),  # NULLs untouched
            (7, "m1", "x", "uploads/e"),
        ]
        for r in rows:
            c.execute(sa.text("INSERT INTO readings VALUES (:a,:b,:c,:d)"), dict(zip("abcd", r)))
        mig.dedupe_image_hashes(c)
        got = {r[0]: r[1] for r in c.execute(sa.text("SELECT id, image_hash FROM readings"))}
        assert got == {1: "h", 2: None, 3: None, 4: "h", 5: None, 6: None, 7: "x"}
        assert c.execute(sa.text("SELECT count(*) FROM readings")).scalar() == 7
        paths = [r[0] for r in c.execute(sa.text("SELECT image_path FROM readings WHERE id IN (2,3)"))]
        assert sorted(paths) == ["uploads/b", "uploads/c"]  # image links survive
        mig.dedupe_image_hashes(c)  # idempotent
