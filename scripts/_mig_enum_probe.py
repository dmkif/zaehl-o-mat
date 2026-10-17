"""Helper for verify-migration-enums.sh: insert one meter per unit, type and integration type."""
import itertools
import os
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import IntegrationType, Meter, MeterType, MeterUnit, Property

engine = create_engine(os.environ["DATABASE_URL"])
with Session(engine) as s:
    try:
        prop = Property(id=uuid.uuid4(), name="probe")
        s.add(prop)
        s.flush()
        n = 0
        for unit, mtype, integ in itertools.product(MeterUnit, MeterType, IntegrationType):
            s.add(Meter(property_id=prop.id, meter_type=mtype, unit=unit, name=f"m{n}", integration_type=integ))
            n += 1
        s.flush()
        print(f"inserted {n} meters (all unit x type x integration combinations)")
    finally:
        s.rollback()
