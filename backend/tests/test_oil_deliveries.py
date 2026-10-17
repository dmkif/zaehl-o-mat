"""API tests for /api/properties/{pid}/oil-deliveries (CRUD, validation, probable, access)."""
import uuid
from types import SimpleNamespace as SimpleNamespace
from datetime import datetime, timedelta, timezone

import pytest

from app.models import MeterType, MeterUnit, OilDelivery, PropertyUser, PropertyUserRole, User, UserRole
from tests.conftest import auth_headers, make_meter, make_property, make_reading, make_user


def url(pid, suffix=""):
    return f"/api/properties/{pid}/oil-deliveries{suffix}"


def iso(days_ago=0):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def assign(db, user, prop, role):
    db.add(PropertyUser(property_id=prop.id, user_id=user.id, role=role))
    db.commit()


@pytest.fixture
def env(db):
    """Property with an admin-role user (property admin) and its auth headers."""
    prop = make_property(db)
    user = make_user(db, username="padmin", email="padmin@example.com")
    assign(db, user, prop, PropertyUserRole.admin)
    return prop, auth_headers(user)


def valid(**over):
    body = {"delivered_at": iso(3), "liters": 1000}
    body.update(over)
    return body


class TestCrud:
    def test_create_with_liters_only(self, client, env):
        prop, h = env
        r = client.post(url(prop.id), json=valid(), headers=h)
        assert r.status_code == 201
        body = r.json()
        assert float(body["liters"]) == 1000
        assert body["total_price"] is None and body["price_per_liter"] is None

    def test_total_price_derives_price_per_liter(self, client, env):
        prop, h = env
        body = client.post(url(prop.id), json=valid(total_price=950), headers=h).json()
        assert float(body["price_per_liter"]) == pytest.approx(0.95)

    def test_price_per_liter_derives_total(self, client, env):
        prop, h = env
        body = client.post(url(prop.id), json=valid(price_per_liter=0.9549), headers=h).json()
        assert float(body["total_price"]) == pytest.approx(954.90)

    def test_consistent_prices_accepted(self, client, env):
        prop, h = env
        r = client.post(url(prop.id), json=valid(total_price=950, price_per_liter=0.95), headers=h)
        assert r.status_code == 201

    def test_inconsistent_prices_rejected(self, client, env, db):
        prop, h = env
        r = client.post(url(prop.id), json=valid(total_price=950, price_per_liter=1.5), headers=h)
        assert r.status_code == 422
        assert db.query(OilDelivery).count() == 0

    def test_list_newest_first(self, client, env):
        prop, h = env
        client.post(url(prop.id), json=valid(delivered_at=iso(30), liters=500), headers=h)
        client.post(url(prop.id), json=valid(delivered_at=iso(2), liters=700), headers=h)
        client.post(url(prop.id), json=valid(delivered_at=iso(10), liters=600), headers=h)
        r = client.get(url(prop.id), headers=h)
        assert [float(d["liters"]) for d in r.json()] == [700, 600, 500]

    def test_list_only_own_property(self, client, env, db):
        prop, h = env
        other = make_property(db, "Other")
        db.add(OilDelivery(property_id=other.id, liters=100, delivered_at=datetime.now(timezone.utc)))
        db.commit()
        assert client.get(url(prop.id), headers=h).json() == []

    def test_patch_partial(self, client, env):
        prop, h = env
        d = client.post(url(prop.id), json=valid(supplier="Alt"), headers=h).json()
        r = client.patch(url(prop.id, f"/{d['id']}"), json={"liters": 800}, headers=h)
        assert r.status_code == 200
        assert float(r.json()["liters"]) == 800
        assert r.json()["supplier"] == "Alt"

    def test_patch_total_price_rederives_per_liter(self, client, env):
        prop, h = env
        d = client.post(url(prop.id), json=valid(total_price=1000), headers=h).json()
        r = client.patch(url(prop.id, f"/{d['id']}"), json={"liters": 500}, headers=h)
        assert float(r.json()["total_price"]) == 1000
        assert float(r.json()["price_per_liter"]) == pytest.approx(2.0)

    def test_patch_explicit_null_for_required_field_rejected(self, client, env):
        prop, h = env
        d = client.post(url(prop.id), json=valid(), headers=h).json()
        for field in ("liters", "delivered_at"):
            r = client.patch(url(prop.id, f"/{d['id']}"), json={field: None}, headers=h)
            assert r.status_code == 422

    def test_delete_and_recalculation_visible_in_dashboard(self, client, env, db):
        prop, h = env
        meter = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="Tank")
        now = datetime.now(timezone.utc)
        make_reading(db, meter.id, 700.0, now - timedelta(days=10))
        make_reading(db, meter.id, 1600.0, now)
        admin = make_user(db, role=UserRole.admin, username="adm", email="adm@example.com")
        ha = auth_headers(admin)

        def rate():
            return client.get("/api/dashboard/summary", headers=ha).json()["meters"][0]["avg_daily_consumption_365d"]

        assert rate() is None  # jump without delivery: unknown
        d = client.post(url(prop.id), json=valid(delivered_at=iso(5), liters=1000), headers=h).json()
        assert rate() == pytest.approx(10.0, rel=0.02)
        assert client.delete(url(prop.id, f"/{d['id']}"), headers=h).status_code == 204
        assert rate() is None

    def test_delete_unknown_id_404(self, client, env):
        prop, h = env
        assert client.delete(url(prop.id, "/999"), headers=h).status_code == 404

    def test_create_sets_created_by_and_logs_without_free_text(self, client, env, db, caplog):
        prop, h = env
        with caplog.at_level("INFO", logger="app.routers.oil_deliveries"):
            client.post(url(prop.id), json=valid(note="geheimer Freitext", supplier="Lieferant X"), headers=h)
        assert db.query(OilDelivery).one().created_by_id is not None
        text = " ".join(r.getMessage() for r in caplog.records)
        assert "create" in text and str(prop.id) in text
        assert "geheimer Freitext" not in text and "Lieferant X" not in text


class TestValidation:
    @pytest.mark.parametrize("liters", [0, -5, 100001, "abc", [1], {"a": 1}, 1.234])
    def test_bad_liters_rejected(self, client, env, db, liters):
        prop, h = env
        r = client.post(url(prop.id), json=valid(liters=liters), headers=h)
        assert r.status_code == 422
        assert db.query(OilDelivery).count() == 0

    @pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e999"])
    def test_non_finite_and_overflow_liters_rejected(self, client, env, db, raw):
        prop, h = env
        content = ('{"delivered_at": "%s", "liters": %s}' % (iso(1), raw)).encode()
        r = client.post(url(prop.id), content=content, headers={**h, "Content-Type": "application/json"})
        assert r.status_code == 422
        assert db.query(OilDelivery).count() == 0

    @pytest.mark.parametrize("field", ["total_price", "price_per_liter"])
    def test_negative_prices_rejected(self, client, env, field):
        prop, h = env
        assert client.post(url(prop.id), json=valid(**{field: -1}), headers=h).status_code == 422

    def test_absurd_derived_price_rejected(self, client, env):
        prop, h = env
        assert client.post(url(prop.id), json=valid(liters=1, total_price=9_000_000), headers=h).status_code == 422

    def test_date_year_1999_rejected(self, client, env):
        prop, h = env
        assert client.post(url(prop.id), json=valid(delivered_at="1999-12-31T12:00:00Z"), headers=h).status_code == 422

    def test_date_too_far_in_future_rejected(self, client, env):
        prop, h = env
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        assert client.post(url(prop.id), json=valid(delivered_at=future), headers=h).status_code == 422

    def test_date_within_one_day_ahead_accepted(self, client, env):
        prop, h = env
        soon = (datetime.now(timezone.utc) + timedelta(hours=20)).isoformat()
        assert client.post(url(prop.id), json=valid(delivered_at=soon), headers=h).status_code == 201

    def test_note_and_supplier_length(self, client, env, db):
        prop, h = env
        assert client.post(url(prop.id), json=valid(note="x" * 2001), headers=h).status_code == 422
        assert client.post(url(prop.id), json=valid(supplier="x" * 256), headers=h).status_code == 422
        assert client.post(url(prop.id), json=valid(note="x" * 2000, supplier="x" * 255), headers=h).status_code == 201

    def test_unknown_fields_rejected(self, client, env, db):
        prop, h = env
        assert client.post(url(prop.id), json=valid(property_id=str(uuid.uuid4())), headers=h).status_code == 422
        assert client.post(url(prop.id), json=valid(created_by_id=str(uuid.uuid4())), headers=h).status_code == 422
        assert db.query(OilDelivery).count() == 0

    def test_missing_required_fields(self, client, env):
        prop, h = env
        assert client.post(url(prop.id), json={"liters": 100}, headers=h).status_code == 422
        assert client.post(url(prop.id), json={"delivered_at": iso(1)}, headers=h).status_code == 422

    def test_patch_unknown_field_rejected(self, client, env):
        prop, h = env
        d = client.post(url(prop.id), json=valid(), headers=h).json()
        r = client.patch(url(prop.id, f"/{d['id']}"), json={"id": 5}, headers=h)
        assert r.status_code == 422

    def test_patch_invalid_value_rejected_and_nothing_stored(self, client, env, db):
        prop, h = env
        d = client.post(url(prop.id), json=valid(), headers=h).json()
        r = client.patch(url(prop.id, f"/{d['id']}"), json={"liters": -1}, headers=h)
        assert r.status_code == 422
        assert float(db.query(OilDelivery).one().liters) == 1000


# ── US3: probable deliveries and dismissal ────────────────────────────────────

@pytest.fixture
def jump(db, env):
    """Oil meter with a 700 -> 1600 L jump and no delivery."""
    prop, h = env
    meter = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="Tank")
    now = datetime.now(timezone.utc)
    r1 = make_reading(db, meter.id, 700.0, now - timedelta(days=10))
    r2 = make_reading(db, meter.id, 1600.0, now - timedelta(days=1))
    return prop, h, meter, r1, r2


class TestProbable:
    def test_lists_open_entry(self, client, jump):
        prop, h, meter, r1, r2 = jump
        r = client.get(url(prop.id, "/probable"), headers=h)
        assert r.status_code == 200
        (entry,) = r.json()
        assert entry["property_id"] == str(prop.id)
        assert entry["meter_id"] == str(meter.id)
        assert (entry["from_reading_id"], entry["to_reading_id"]) == (r1.id, r2.id)
        assert entry["estimated_liters"] == 900
        assert entry["from_at"] and entry["to_at"]

    def test_creating_a_delivery_makes_the_entry_vanish(self, client, jump):
        prop, h, *_ = jump
        client.post(url(prop.id), json=valid(delivered_at=iso(5), liters=900), headers=h)
        assert client.get(url(prop.id, "/probable"), headers=h).json() == []

    def test_dismiss_is_idempotent_and_stores_one_row(self, client, jump, db):
        from app.models import OilDeliveryDismissal

        prop, h, meter, r1, r2 = jump
        body = {"from_reading_id": r1.id, "to_reading_id": r2.id}
        assert client.post(url(prop.id, "/probable/dismiss"), json=body, headers=h).status_code == 204
        assert client.post(url(prop.id, "/probable/dismiss"), json=body, headers=h).status_code == 204
        assert db.query(OilDeliveryDismissal).count() == 1
        assert client.get(url(prop.id, "/probable"), headers=h).json() == []

    def test_dismissed_jump_stays_out_of_the_average(self, client, jump, db):
        prop, h, meter, r1, r2 = jump
        client.post(url(prop.id, "/probable/dismiss"), json={"from_reading_id": r1.id, "to_reading_id": r2.id}, headers=h)
        admin = make_user(db, role=UserRole.admin, username="adm", email="adm@example.com")
        summary = client.get("/api/dashboard/summary", headers=auth_headers(admin)).json()
        assert summary["meters"][0]["avg_daily_consumption_365d"] is None
        assert summary["oil_probable_deliveries"] == []

    def test_pair_that_is_not_an_open_probable_delivery_404(self, client, jump, db):
        prop, h, meter, r1, r2 = jump
        for body in ({"from_reading_id": r2.id, "to_reading_id": r1.id}, {"from_reading_id": 1, "to_reading_id": 99999}):
            assert client.post(url(prop.id, "/probable/dismiss"), json=body, headers=h).status_code == 404

    def test_pair_from_another_property_404(self, client, jump, db):
        from app.models import OilDeliveryDismissal

        prop, h, meter, r1, r2 = jump
        other = make_property(db, "B")
        user_b = make_user(db, username="b", email="b@example.com")
        assign(db, user_b, other, PropertyUserRole.admin)
        body = {"from_reading_id": r1.id, "to_reading_id": r2.id}
        assert client.post(url(other.id, "/probable/dismiss"), json=body, headers=auth_headers(user_b)).status_code == 404
        assert db.query(OilDeliveryDismissal).count() == 0
        # also after the pair was dismissed by property A
        client.post(url(prop.id, "/probable/dismiss"), json=body, headers=h)
        assert client.post(url(other.id, "/probable/dismiss"), json=body, headers=auth_headers(user_b)).status_code == 404

    def test_dismiss_race_leaves_one_row(self, client, jump, db, monkeypatch):
        """Both requests pass the existence check; the second insert hits the unique key."""
        import app.routers.oil_deliveries as mod
        from app.models import OilDeliveryDismissal

        prop, h, meter, r1, r2 = jump
        body = {"from_reading_id": r1.id, "to_reading_id": r2.id}
        client.post(url(prop.id, "/probable/dismiss"), json=body, headers=h)
        monkeypatch.setattr(mod, "_dismissal_exists", lambda *a, **k: False)
        monkeypatch.setattr(
            mod, "open_probable_deliveries",
            lambda *a, **k: [{"meter_id": meter.id, "from_reading_id": r1.id, "to_reading_id": r2.id}],
        )
        assert client.post(url(prop.id, "/probable/dismiss"), json=body, headers=h).status_code == 204
        assert db.query(OilDeliveryDismissal).count() == 1

    def test_dismiss_validation(self, client, jump):
        prop, h, *_ = jump
        u = url(prop.id, "/probable/dismiss")
        assert client.post(u, json={"from_reading_id": 1}, headers=h).status_code == 422
        assert client.post(u, json={"from_reading_id": "x", "to_reading_id": 2}, headers=h).status_code == 422
        assert client.post(u, json={"from_reading_id": 1, "to_reading_id": 2, "x": 1}, headers=h).status_code == 422
        assert client.post(u, json={"from_reading_id": 0, "to_reading_id": 2}, headers=h).status_code == 422

    def test_reading_deletion_cascades_dismissal(self, client, jump, db):
        from app.models import OilDeliveryDismissal, Reading

        prop, h, meter, r1, r2 = jump
        client.post(url(prop.id, "/probable/dismiss"), json={"from_reading_id": r1.id, "to_reading_id": r2.id}, headers=h)
        db.delete(db.get(Reading, r2.id))
        db.commit()
        assert db.query(OilDeliveryDismissal).count() == 0

    def test_summary_lists_only_readable_properties(self, client, jump, db):
        prop, h, *_ = jump
        other = make_property(db, "B")
        m2 = make_meter(db, other.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="Tank B")
        now = datetime.now(timezone.utc)
        make_reading(db, m2.id, 100.0, now - timedelta(days=5))
        make_reading(db, m2.id, 900.0, now)
        body = client.get("/api/dashboard/summary", headers=h).json()
        assert [e["property_id"] for e in body["oil_probable_deliveries"]] == [str(prop.id)]
        admin = make_user(db, role=UserRole.admin, username="adm", email="adm@example.com")
        body = client.get("/api/dashboard/summary", headers=auth_headers(admin)).json()
        assert {e["property_id"] for e in body["oil_probable_deliveries"]} == {str(prop.id), str(other.id)}
        assert set(body["oil_probable_deliveries"][0]) >= {
            "property_id", "meter_id", "from_reading_id", "to_reading_id", "from_at", "to_at", "estimated_liters",
        }


# ── US5: access matrix ────────────────────────────────────────────────────────

class TestAccess:
    @pytest.fixture
    def world(self, db, client):
        prop = make_property(db, "A")
        other = make_property(db, "B")
        users = {}
        for name, role in (("reader", PropertyUserRole.user), ("writer", PropertyUserRole.manager), ("padmin", PropertyUserRole.admin)):
            u = make_user(db, username=name, email=f"{name}@example.com")
            assign(db, u, prop, role)
            users[name] = auth_headers(u)
        users["norole"] = auth_headers(make_user(db, username="norole", email="norole@example.com"))
        users["foreign"] = auth_headers(make_user(db, username="foreign", email="foreign@example.com"))
        assign(db, db.query(User).filter_by(username="foreign").one(), other, PropertyUserRole.admin)
        users["global"] = auth_headers(make_user(db, role=UserRole.admin, username="glob", email="glob@example.com"))
        users["super"] = auth_headers(make_user(db, role=UserRole.superadmin, username="sup", email="sup@example.com"))
        d = OilDelivery(property_id=prop.id, liters=500, delivered_at=datetime.now(timezone.utc))
        db.add(d)
        db.commit()
        meter = make_meter(db, prop.id, meter_type=MeterType.oil, unit=MeterUnit.liter, name="T")
        now = datetime.now(timezone.utc)
        r1 = make_reading(db, meter.id, 700.0, now - timedelta(days=9))
        r2 = make_reading(db, meter.id, 1600.0, now - timedelta(days=8))
        return SimpleNamespace(prop=prop, other=other, users=users, delivery=d, r1=r1, r2=r2)

    def calls(self, w, client, headers):
        pid, did = w.prop.id, w.delivery.id
        dismiss = {"from_reading_id": w.r1.id, "to_reading_id": w.r2.id}
        return {
            "list": client.get(url(pid), headers=headers),
            "probable": client.get(url(pid, "/probable"), headers=headers),
            "create": client.post(url(pid), json=valid(), headers=headers),
            "patch": client.patch(url(pid, f"/{did}"), json={"liters": 400}, headers=headers),
            "dismiss": client.post(url(pid, "/probable/dismiss"), json=dismiss, headers=headers),
            "delete": client.delete(url(pid, f"/{did}"), headers=headers),
        }

    def test_no_token_401_everywhere(self, client, world):
        codes = {k: v.status_code for k, v in self.calls(world, client, {}).items()}
        assert set(codes.values()) == {401}

    @pytest.mark.parametrize("who", ["norole", "foreign"])
    def test_user_without_role_in_property_gets_404_everywhere(self, client, world, who):
        res = self.calls(world, client, world.users[who])
        assert {k: v.status_code for k, v in res.items()} == dict.fromkeys(res, 404)

    def test_no_access_and_unknown_property_are_indistinguishable(self, client, world):
        h = world.users["norole"]
        real = client.get(url(world.prop.id), headers=h)
        fake = client.get(url(uuid.uuid4()), headers=h)
        assert (real.status_code, real.json()) == (fake.status_code, fake.json())

    def test_read_only_user(self, client, world):
        res = {k: v.status_code for k, v in self.calls(world, client, world.users["reader"]).items()}
        assert res["list"] == 200 and res["probable"] == 200
        assert res["create"] == res["patch"] == res["dismiss"] == res["delete"] == 403

    def test_writer_can_write_but_not_delete(self, client, world):
        res = {k: v.status_code for k, v in self.calls(world, client, world.users["writer"]).items()}
        assert res["list"] == 200 and res["probable"] == 200
        assert res["create"] == 201 and res["patch"] == 200 and res["dismiss"] == 204
        assert res["delete"] == 403

    @pytest.mark.parametrize("who", ["padmin", "global", "super"])
    def test_admins_can_do_everything(self, client, world, who):
        res = {k: v.status_code for k, v in self.calls(world, client, world.users[who]).items()}
        assert res == {"list": 200, "probable": 200, "create": 201, "patch": 200, "dismiss": 204, "delete": 204}

    def test_global_admin_unknown_property_404(self, client, world):
        assert client.get(url(uuid.uuid4()), headers=world.users["global"]).status_code == 404

    def test_delivery_of_property_b_under_property_a_is_404(self, client, db, world):
        foreign_delivery = OilDelivery(property_id=world.other.id, liters=300, delivered_at=datetime.now(timezone.utc))
        db.add(foreign_delivery)
        db.commit()
        h = world.users["padmin"]
        assert client.patch(url(world.prop.id, f"/{foreign_delivery.id}"), json={"liters": 1}, headers=h).status_code == 404
        assert client.delete(url(world.prop.id, f"/{foreign_delivery.id}"), headers=h).status_code == 404
        db.expire_all()
        assert float(db.get(OilDelivery, foreign_delivery.id).liters) == 300

    def test_error_bodies_do_not_leak_other_property_data(self, client, db, world):
        h = world.users["foreign"]
        r = client.delete(url(world.prop.id, f"/{world.delivery.id}"), headers=h)
        assert r.status_code == 404
        assert "500" not in r.text and str(world.delivery.id) not in r.text
