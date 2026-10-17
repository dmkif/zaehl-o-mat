#!/usr/bin/env python
"""Load synthetic data into the rig database (never real data).

Reads security/rig/.state/rig.env, writes through the backend's SQLAlchemy
models (run with PYTHONPATH=<backend dir>, as up.sh does), prints the id map as
JSON and stores it in security/rig/.state/seed.json.
"""
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE = Path(__file__).resolve().parent / ".state"


def load_env(path: Path) -> dict:
    env = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k] = v
    return env


def main() -> None:
    rig = load_env(STATE / "rig.env")
    host = rig["DATABASE_URL"].split("@", 1)[1].split(":", 1)[0]
    if host != "127.0.0.1":
        sys.exit(f"refusing non-loopback database host {host}")

    # settings validation needs these before app.* is imported
    os.environ.update(
        DATABASE_URL=rig["DATABASE_URL"],
        JWT_SECRET_KEY=rig["JWT_SECRET_KEY"],
        UPLOAD_PATH=rig["UPLOAD_DIR"],
    )
    # NOTE: only m3 works as unit on Postgres: migration 0000 enum has kWh/L but the model stores
    # enum NAMES (kwh/liter) -> see report.
    from PIL import Image, ImageDraw

    from app.database import SessionLocal
    from app.models import (
        IntegrationType, Meter, MeterType, MeterUnit, Property, PropertyUser,
        PropertyUserRole, Reading, ReadingSource, User, UserRole,
    )

    upload_dir = Path(rig["UPLOAD_DIR"])
    upload_dir.mkdir(parents=True, exist_ok=True)

    def make_image(label: str, fmt: str) -> tuple[str, str]:
        """Small synthetic meter-like image. Returns (stored path, sha256)."""
        img = Image.new("RGB", (160, 80), (230, 230, 220))
        ImageDraw.Draw(img).text((10, 30), f"RIG {label}", fill=(10, 10, 10))
        name = f"{uuid.uuid4()}.{'png' if fmt == 'PNG' else 'jpg'}"
        img.save(upload_dir / name, fmt)
        digest = hashlib.sha256((upload_dir / name).read_bytes()).hexdigest()
        return f"uploads/{name}", digest

    db = SessionLocal()
    try:
        users_spec = [
            # key, username, role, has OIDC sub
            ("superadmin", rig["SUPERADMIN_USER"], UserRole.superadmin, False),
            ("admin", "rig-admin", UserRole.admin, True),
            ("manager", "rig-manager", UserRole.manager, True),
            ("user_a", "rig-user-a", UserRole.user, True),
            ("user_b", "rig-user-b", UserRole.user, True),
            # no group at the IdP: app role "user" but no property membership
            ("nogroup", "rig-nogroup", UserRole.user, True),
        ]
        users: dict[str, User] = {}
        for key, username, role, oidc in users_spec:
            u = User(username=username, email=f"{username}@rig.invalid", role=role, is_active=True,
                     oidc_sub=f"rig-sub-{username}" if oidc else None)
            db.add(u)
            users[key] = u
        db.flush()

        props = {
            "prop_a": Property(name="prop-a", address="1 Synthetic Way", property_type="residential"),
            "prop_b": Property(name="prop-b", address="2 Synthetic Way", property_type="residential"),
        }
        db.add_all(props.values())
        db.flush()
        db.add_all([
            PropertyUser(property_id=props["prop_a"].id, user_id=users["user_a"].id, role=PropertyUserRole.user),
            PropertyUser(property_id=props["prop_a"].id, user_id=users["manager"].id, role=PropertyUserRole.manager),
            PropertyUser(property_id=props["prop_b"].id, user_id=users["user_b"].id, role=PropertyUserRole.user),
        ])

        now = datetime.now(timezone.utc).replace(microsecond=0)
        out_meters: dict[str, dict] = {}
        out_readings: dict[str, list] = {}
        for pkey, prop in props.items():
            suffix = pkey[-1]
            for (label, mtype), unit, base in ((("water", MeterType.water), MeterUnit.m3, 100.0), (("water2", MeterType.water), MeterUnit.m3, 5000.0)):
                mkey = f"meter_{suffix}_{label}"
                m = Meter(property_id=prop.id, meter_type=mtype, unit=unit, name=f"rig {label} {suffix}",
                          serial_number=f"RIG-{suffix.upper()}-{label.upper()}-001",
                          integration_type=IntegrationType.manual)
                db.add(m)
                db.flush()
                out_meters[mkey] = {"id": str(m.id), "property": pkey, "serial_number": m.serial_number}
                out_readings[mkey] = []
                for i in range(3):
                    path, digest = make_image(f"{mkey} {i}", "PNG" if i % 2 == 0 else "JPEG")
                    r = Reading(meter_id=m.id, value=base + i * 10, read_at=now - timedelta(days=30 * (3 - i)),
                                source=ReadingSource.manual, image_path=path, image_hash=digest, note=f"synthetic {i}")
                    db.add(r)
                    db.flush()
                    out_readings[mkey].append({"id": r.id, "image_path": path, "filename": path.split("/", 1)[1],
                                               "image_hash": digest})
        db.commit()

        seed = {
            "users": {k: {"id": str(u.id), "username": u.username, "role": u.role.value} for k, u in users.items()},
            "properties": {k: {"id": str(p.id), "name": p.name} for k, p in props.items()},
            "property_access": {"prop_a": ["user_a", "manager"], "prop_b": ["user_b"]},
            "meters": out_meters,
            "readings": out_readings,
        }
    finally:
        db.close()

    (STATE / "seed.json").write_text(json.dumps(seed, indent=2))
    print(json.dumps(seed, indent=2))


if __name__ == "__main__":
    main()
