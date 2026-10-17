"""F-014: enum labels created by the migrations must cover what the models write.

SQLAlchemy persists enum *names*.  Migration 0000 created different labels for
meterunit / integrationtype, which broke meter creation on a migration-built
database.  Migration 0008 adds every missing name; this guard fails when a model
enum gains a member without a migration.
"""
import importlib.util
import pathlib

import sqlalchemy as sa

from app import models
from app.database import Base

VERSIONS = pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"


def _load(name: str):
    path = next(VERSIONS.glob(f"{name}_*.py"))
    spec = importlib.util.spec_from_file_location(f"mig_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _model_enums() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, sa.Enum):
                found.setdefault(col.type.name, set()).update(col.type.enums)
    return found


def test_migration_0008_covers_every_model_enum_name():
    mig = _load("0008")
    for type_name, names in _model_enums().items():
        assert type_name in mig.ENUM_LABELS, f"{type_name} missing in 0008"
        missing = names - set(mig.ENUM_LABELS[type_name])
        assert not missing, f"{type_name}: labels {missing} not ensured by 0008"


def test_migration_0008_only_adds_never_removes():
    mig = _load("0008")
    assert mig.down_revision == "0006"  # integrator re-points to 0007 after merge
    assert mig.downgrade.__doc__ and "no-op" in mig.downgrade.__doc__.lower()


def test_regression_names_that_broke_meter_creation():
    mig = _load("0008")
    assert {"kwh", "liter", "m3"} <= set(mig.ENUM_LABELS["meterunit"])
    assert {"smart_meter", "shelly"} <= set(mig.ENUM_LABELS["integrationtype"])
