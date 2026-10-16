# Quickstart: Validation

1. Run the new calculation tests (written first, red before implementation):
   `cd backend && pytest tests/test_oil_consumption.py tests/test_oil_deliveries.py tests/test_dashboard.py -q`
2. Real-data check: the first test loads the eight readings (1374, 1353, 1365, 1289, 1207, 1074, 945, 816 L; 2026-04-02 to 2026-09-13) and expects 3.2-3.6 L/day.
3. Migration: start a throw-away PostgreSQL 18, `alembic upgrade head`, then `alembic downgrade 0004`, `upgrade head` again; check that existing deliveries survive.
4. Manual: record a delivery of 1000 L between two readings in the UI, check the tile and range; remove it and check the notice "probable delivery" reappears.
5. Full suite: `pytest -q` (all green) and the pre-commit hooks.

## Notes

Run with `cd backend && ~/zaehl-o-mat/backend/.venv/bin/python -m pytest <paths> -q` (SQLite test DB, no setup needed).

### Red runs (before implementation)

- US1 (T008): `tests/test_oil_consumption.py` failed at collection (`ImportError: cannot import name 'compute_oil_consumption'`). The four new dashboard tests failed on the old defect: tile `10.3597` instead of 3.2-3.6, Reichweite 232 instead of 78 days, cost forecast `-66.724` instead of `310.791` (rate*30), type aggregate `570.0` instead of `558`. Baseline before the work: 280 passed.
- US2 (T015): 43 failed / 19 passed. Service tests: delivery ignored (`0.0 == 100.0`), `ImportError: assign_deliveries`; API tests: `404 == 201` / `404 == 422` (routes missing).
- US3 (T022): 13 failed (probable list empty, dismiss 404, jump counted as consumption: `0.0 is None`), the rest green.
- US4 (T026): all cases passed on first run, as expected by T027 (the net-balance rule from US1 telescopes); no code change.
- US5 (T028): all passed on first run because the router was written with the access rules in US2. Sanity check by mutation: changing the delete level to `write` makes `test_writer_can_write_but_not_delete` fail, original restored.

### Real-data result

Eight real readings (1374 ... 816 L, 2026-04-02 to 2026-09-13, `now` = 2026-09-14): 558 L / 163.9 d = **3.40 L/day** (old tile: 10.36 L/day). Reichweite 816 L -> 240 days.

### Migration check (T005)

Throw-away `docker.io/library/postgres:18-trixie` via podman on a random loopback port, `DATABASE_URL=postgresql://t:t@127.0.0.1:<port>/t`:

```
alembic upgrade 0004; insert property + delivery (1000 L, 950 EUR)
alembic upgrade head            # 0004 -> 0005
insert delivery without prices  # accepted (NULL, NULL)
alembic downgrade 0004          # price-less row gets 0.00 / 0.0000, NOT NULL restored, dismissal table gone
alembic upgrade head            # both deliveries survive (count 2)
```

Autogenerate drift check against the models: no difference on the touched tables except the pre-existing `created_at` nullability. Container removed afterwards.

### Test counts

Full backend suite: 391 passed (280 before). New: `test_oil_consumption.py` 49 tests, `test_oil_deliveries.py` 57, `test_dashboard.py` +5.

### Deviations

- Unknown property and "user without any role" both return 404 with the same body (no existence leak); 403 only for users who can read but lack the level.
- Concurrent dismiss is tested deterministically (existence check forced to miss, unique key raises `IntegrityError`, handled) instead of real threads, because the test DB is one shared SQLite session.
- Added a `RequestValidationError` handler in `main.py`: FastAPI's default echoes the input, which cannot be serialized for NaN/Infinity (500) and reflected client data. Responses now carry `type/loc/msg` only.
- `property_id` is part of every probable-delivery entry (frontend request); extra module `app/services/oil_loader.py` holds the DB glue so the calculation stays pure.
- Money and litres are serialized as strings (pydantic Decimal), like `ReadingResponse.value`.
