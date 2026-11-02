# Quickstart: Validating Consumption Statistics

Prerequisites: backend dev environment (`pip install -r backend/requirements.txt`, includes `tzdata`), frontend dependencies (`npm ci` in `frontend/`). No migration, no new environment variable.

## 1. Automated checks

```bash
cd backend && python -m pytest tests/test_consumption_stats.py tests/test_dashboard_statistics.py tests/test_dashboard.py tests/test_oil_consumption.py tests/test_oil_deliveries.py -q
cd ../frontend && npx vitest run
```

Expected: all green; the existing yearly-average tests pass unchanged (SC-005).

## 2. Heating season scenario (Story 1, SC-001)

Seed (test fixture or UI): oil tank with about 1 L/day for 100 days, then about 8 L/day over the last 20 days, no deliveries.
- `GET /api/dashboard/summary`: the tank entry has `avg_daily_consumption_30d` clearly above `avg_daily_consumption_365d`, `trend_30d_vs_365d_pct` large and positive, `unusually_high: false` (oil).
- Dashboard: the meter card shows "30 Tage", the percentage in red, and "seit letzter Ablesung".

## 2b. Two tanks (Story 3, FR-024)

Property with tank A (300 L) and tank B (500 L), together 4 L/day.
- `summary.oil_reichweite[property] == 200` (800 / 4); with only tank A having a rate the group range is absent.
- A single-tank property returns the same range as on `main` (regression test).

## 3. Monthly view (Story 2, SC-002/003)

Readings across 14 months, one reading roughly every 3 weeks.
- `GET /api/dashboard/monthly?meter_id=<id>`: 12 entries, oldest first; recent months carry `previous_year` and `delta_pct`; the current month has `state: "partial"`; gaps show `no_data`.
- Check: the sum of one interval's month shares equals its consumption (automated invariant test).
- UI: meter detail page shows chart and table; "Vorjahr" overlay toggles.

## 4. Several properties (Story 6, SC-007/008)

Two properties, water meters at 0.2 and 0.6 m³/day.
- `summary.statistics.by_property` has both; `overall.water.yearly == 0.8`, `mean_per_property.yearly == 0.4`, `properties_used == 2`.
- Log in as a user who is member of only one: `overall` absent, only that property in `by_property`; `GET /api/dashboard/monthly?property_id=<other>` returns 403/404 without data.

## 5. Costs and year view (Story 4)

Oil property with two priced deliveries this year; water meter with a `PriceEntry`.
- `GET /api/dashboard/yearly`: `cost` set for oil and water; remove the price entry -> water `cost: null`, consumption still shown.

## 6. Time zone (FR-019)

Reading at 23:30 UTC on the last day of a month in summer time: counted into the next month in `/monthly` (automated test).

## 7. Performance (SC-006)

Seed 10 meters x 5 years of readings (script in tests); `summary` response time is not slower than on `main` (compare the number of SQL statements: the new loader issues one readings query for all meters).

## 8. Security negatives (Principle X)

Run the negative tests listed in [contracts/api.md](contracts/api.md); they are part of `tests/test_dashboard_statistics.py`.
