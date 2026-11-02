---

description: "Task list for feature 015 - consumption statistics"
---

# Tasks: Consumption Statistics (Recent Rate, Monthly View, Trends)

**Input**: Design documents from `/specs/015-consumption-statistics/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md, quickstart.md

**Tests**: REQUIRED by constitution Principle I and X (tests first, negative tests for access and input). Write each test task before the implementation task it covers and see it fail.

**Organization**: Grouped by user story. US1 is the MVP (it solves the heating-season blind spot). Paths are relative to the repository root `/home/dmkif/zaehl-o-mat`.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1..US6 as numbered in spec.md
- Test helpers available in `backend/tests/conftest.py`: `make_user`, `make_property`, `make_meter`, `make_reading`, `auth_headers`

---

## Phase 1: Setup

- [X] T001 Add `tzdata` (exact `==` pin to the current PyPI release) to `backend/requirements.txt`; verify `zoneinfo.ZoneInfo("Europe/Berlin")` loads in a clean `python:3.12-slim` (research R4)
- [X] T002 [P] Create empty test modules `backend/tests/test_consumption_stats.py` and `backend/tests/test_dashboard_statistics.py` with the shared imports/fixtures used by `backend/tests/test_dashboard.py`
- [X] T003 [P] Create `backend/app/schemas/dashboard.py` with Pydantic response models for `/monthly` and `/yearly` and the `statistics` block of the summary, matching `specs/015-consumption-statistics/contracts/api.md` (models only, no logic)

---

## Phase 2: Foundational (blocks all user stories)

**Purpose**: One interval representation, one reading selection, a regression net around existing values (FR-017, FR-018, SC-005).

- [X] T004 Write a regression test class in `backend/tests/test_dashboard.py` that pins today's values for water, electricity and oil: `avg_daily_consumption_365d`, `oil_reichweite` (single tank), type aggregates, cost forecast. Run it on the unchanged code and keep it green through all later tasks
- [X] T005 [P] Write pure tests in `backend/tests/test_consumption_stats.py` for `intervals_from_readings` (positive delta = known, zero delta = known with 0, negative delta = unknown/skipped, identical timestamps ignored, one reading = empty) and for equality with the old `_avg_daily_consumption_365d` result on the same readings
- [X] T006 Create `backend/app/services/consumption_stats.py` with `intervals_from_readings(readings) -> list[Interval]` reusing `Interval` from `backend/app/services/oil_consumption.py`, and `yearly_rate(intervals)`; make T005 pass
- [X] T007 [P] Write tests in `backend/tests/test_oil_consumption.py` that `compute_oil_consumption(..., window_days=365)` equals the current result and that a longer `window_days` includes older readings; then add the optional `window_days: int = WINDOW_DAYS` parameter in `backend/app/services/oil_consumption.py` (default behaviour unchanged, research R7)
- [X] T008 Create `backend/app/services/reading_selection.py` with `counting_readings(db, meter_ids, since)` and `backend/app/services/stats_loader.py` with `load_intervals`; `counting_readings` lives in its own module so `oil_loader` and `stats_loader` both use it without a circular import. Original: `counting_readings(db, meter_ids, since)` (the single selection of readings that count, research R2; today every reading) and `load_intervals(db, meters, now, window_days)` doing one readings query for all meters, oil meters through `compute_oil_consumption` with deliveries/dismissals once per property, others through `intervals_from_readings` (research R3)
- [X] T009 Change `backend/app/services/oil_loader.py` so `load_oil_results` obtains its readings from `stats_loader.counting_readings` and forwards `window_days`; confirm `backend/tests/test_oil_deliveries.py`, `backend/tests/test_oil_consumption.py` and T004 stay green
- [X] T010 Replace the per-meter query helper `_avg_daily_consumption_365d` in `backend/app/routers/dashboard.py` by the batch loader (`stats_loader.load_intervals` + `consumption_stats.yearly_rate`); T004 and the existing `backend/tests/test_dashboard.py` must stay green with identical values
- [X] T011 [P] Add `tank_group_key(meter)` (returns the property id) and `group_rates(...)` helpers to `backend/app/services/consumption_stats.py` with tests in `backend/tests/test_consumption_stats.py` (two tanks sum, one tank unchanged, different units yield no total)

**Checkpoint**: backend values identical to before, one loader, one interval type.

---

## Phase 3: User Story 1 - Current pace next to the yearly average (Priority: P1) MVP

**Goal**: Per meter: 30-day rate, trend in percent, rate since the last reading (FR-001..007, FR-016, FR-018).

**Independent Test**: Oil tank with ~1 L/day for 100 days then ~8 L/day for 20 days: summary shows `avg_daily_consumption_30d` far above the yearly average and a large positive `trend_30d_vs_365d_pct`; the meter card shows it in red.

### Tests for US1 (write first)

- [X] T012 [P] [US1] Pure tests in `backend/tests/test_consumption_stats.py` for `recent_rate`: interval crossing the window start counts whole (spec 1.6), last reading older than 30 days gives `None` (1.3), oil noise cannot make it negative (1.5), unknown oil interval skipped (1.4), fewer than two readings gives `None` (1.7), heating-season scenario above
- [X] T013 [P] [US1] Pure tests for `last_interval_rate` (known, unknown, none) and `trend_pct` (`None` when yearly missing or <= 0, one decimal, sign) in `backend/tests/test_consumption_stats.py`
- [X] T014 [P] [US1] API tests in `backend/tests/test_dashboard_statistics.py`: summary meters carry `avg_daily_consumption_30d`, `daily_consumption_last_interval`, `trend_30d_vs_365d_pct` for water and oil; single reading gives all three `null`; replaced meter excluded; unauthenticated 401; user without membership sees none (negative tests, Principle X)
- [X] T015 [P] [US1] Vitest tests in `frontend/src/utils/__tests__/consumptionStats.test.ts` for `trendClass` (> +10 increase, < -10 decrease, else neutral, `null` neutral) and `formatTrend` (signed, German decimal comma)

### Implementation for US1

- [X] T016 [US1] Add `recent_rate`, `last_interval_rate`, `trend_pct` and the constants `RECENT_DAYS = 30` in `backend/app/services/consumption_stats.py` (data-model MeterRates); make T012/T013 pass
- [X] T017 [US1] Extend the per-meter entries of `GET /api/dashboard/summary` in `backend/app/routers/dashboard.py` with the three new fields (contracts section 1); make T014 pass; existing fields unchanged (T004 green)
- [X] T018 [P] [US1] Create `frontend/src/utils/consumptionStats.ts` with `trendClass`, `formatTrend`; make T015 pass
- [X] T019 [P] [US1] Add i18n keys (`stats.last30`, `stats.sinceLastReading`, `stats.vsAverage`, ...) in `frontend/src/locales/de.ts` and `frontend/src/locales/en.ts`
- [X] T020 [US1] Create `frontend/src/components/TrendLine.vue` (30-day rate with unit, colored percentage, "seit letzter Ablesung"; nothing when values are `null`) and render it in the `extra` slot of `MeterCard` in `frontend/src/views/Dashboard.vue`; add a component test `frontend/src/components/__tests__/TrendLine.test.ts`

**Checkpoint**: US1 deliverable on its own (shippable MVP).

---

## Phase 4: User Story 2 - Consumption per month and against last year (Priority: P2)

**Goal**: Monthly series with previous-year comparison (FR-008..011, FR-019).

**Independent Test**: Readings over 14 months: `GET /api/dashboard/monthly?meter_id=` returns 12 months with `previous_year`, `delta_pct`, current month `partial`, gaps `no_data`.

### Tests for US2 (write first)

- [X] T021 [P] [US2] Pure tests in `backend/tests/test_consumption_stats.py` for `monthly_series`: proportional split across a month boundary; invariant that an interval's month shares sum to its consumption (1e-6); gap month `no_data` with value `None` not 0; current month `partial`; `complete` only when fully covered; `estimated` for intervals > 60 days; oil delivery/unknown interval not counted (spec 2.5); water/electricity decrease skipped
- [X] T022 [P] [US2] Pure test for Europe/Berlin boundaries: a reading at 23:30 UTC on the last day of a summer-time month falls into the next month (research R4); winter-time counterpart
- [X] T023 [P] [US2] Pure tests for previous-year pairing and `delta_pct` (only when both months `complete`, previous > 0)
- [X] T024 [P] [US2] API tests in `backend/tests/test_dashboard_statistics.py` for `GET /api/dashboard/monthly`: meter scope, property scope, `months` bounds (0 and 25 give 422), `meter_id` plus `property_id` gives 422, non-UUID 422, unauthenticated 401, foreign meter/property 403/404 without data, exactly `months` entries oldest first

### Implementation for US2

- [X] T025 [US2] Implement `monthly_series` (split at local midnight, coverage states, estimated flag, previous-year pairing) in `backend/app/services/consumption_stats.py`; make T021..T023 pass
- [X] T026 [US2] Implement a shared scope resolver (meter / property / all accessible) in `backend/app/routers/dashboard.py` reusing `get_meter_with_access`, `require_property_access` and the accessible-property helper from `backend/app/permissions.py`; no query before the access check
- [X] T027 [US2] Implement `GET /api/dashboard/monthly` in `backend/app/routers/dashboard.py` using `stats_loader.load_intervals` with a 24-month window; make T024 pass
- [X] T028 [P] [US2] Add month label helper and tests in `frontend/src/utils/consumptionStats.ts` / `frontend/src/utils/__tests__/consumptionStats.test.ts`; add i18n keys for monthly view ("Vorjahr", "keine Daten", "teilweise", "geschätzt")
- [X] T029 [US2] Create `frontend/src/components/MonthlyConsumption.vue` (apexcharts bar chart with previous-year overlay toggle, table with value, previous year and percent, states shown as text) with a props-driven scope; add a component test in `frontend/src/components/__tests__/MonthlyConsumption.test.ts`
- [X] T030 [US2] Show `MonthlyConsumption` in `frontend/src/views/MeterDetail.vue` (meter scope)

---

## Phase 5: User Story 3 - Oil range at the current pace (Priority: P2)

**Goal**: Range at the 30-day rate and correct range for a tank group (FR-012, FR-024).

**Independent Test**: Tanks of 300 L and 500 L at 4 L/day together: `oil_reichweite` is 200; with a 12 L/day 30-day rate `oil_reichweite_30d` is 66.

### Tests for US3 (write first)

- [X] T031 [P] [US3] Pure tests for `group_range` in `backend/tests/test_consumption_stats.py`: sum of levels / sum of rates floored; one tank equals old value; a tank without rate gives `None`; zero rate gives `None`
- [X] T032 [P] [US3] API tests in `backend/tests/test_dashboard_statistics.py`: two tanks in one property give the group range (the previous last-tank-wins behaviour is gone); single-tank property unchanged (T004); `oil_reichweite_30d` present only when every tank has a 30-day rate and the sum is > 0

### Implementation for US3

- [X] T033 [US3] Implement `group_range` in `backend/app/services/consumption_stats.py`; switch `oil_reichweite` in `backend/app/routers/dashboard.py` to the group range and add `oil_reichweite_30d` (contracts section 1); T004 stays green for single tanks
- [X] T034 [US3] Show both ranges with labels ("Reichweite (Jahresschnitt)", "Reichweite (30 Tage)") in the oil section of `frontend/src/views/Dashboard.vue`, per-tank levels and rates remain visible; add i18n keys; extend a component/view test where one exists

---

## Phase 6: User Story 6 - Per property and across properties (Priority: P2)

**Goal**: `statistics.by_property` and `overall` (FR-020..023, FR-016, SC-007/008).

**Independent Test**: Two properties with water 0.2 and 0.6 m³/day: `overall.water.yearly == 0.8`, `mean_per_property.yearly == 0.4`, `properties_used == 2`; a member of only one property gets no `overall`.

### Tests for US6 (write first)

- [X] T035 [P] [US6] Pure tests for `aggregate_overall` in `backend/tests/test_consumption_stats.py`: sum and mean per property; properties without data excluded and counted in `properties_used`; `mean_per_property` omitted when `properties_used == 1`; trend recomputed on sums; mixed units give no total; two tanks add up
- [X] T036 [P] [US6] API tests in `backend/tests/test_dashboard_statistics.py`: totals equal the sum of the per-property figures; user with access to one of two properties never sees data or totals of the other and gets no `overall` (SC-008); global admin sees all; user without any property gets empty `statistics`
- [X] T037 [P] [US6] API tests: `/monthly?property_id=` and `/monthly` (all accessible) group series by meter type and include only accessible properties; foreign `property_id` gives 403/404

### Implementation for US6

- [X] T038 [US6] Implement `aggregate_overall` and `group_statistics` in `backend/app/services/consumption_stats.py`; make T035 pass
- [X] T039 [US6] Add the `statistics` block to `GET /api/dashboard/summary` in `backend/app/routers/dashboard.py` computed only from the caller's accessible properties; make T036 pass
- [X] T040 [US6] Support the property and all-accessible scopes in `GET /api/dashboard/monthly` aggregation (series per meter type, summed over meters); make T037 pass
- [X] T041 [P] [US6] Dashboard UI: per-property statistic blocks and, only with two or more properties, an overall block (total, mean per property with the number of properties used, trend) in `frontend/src/views/Dashboard.vue`; add i18n keys
- [X] T042 [US6] Add a scope selector (property / all) and the monthly view to the dashboard using `MonthlyConsumption` (scope props) in `frontend/src/views/Dashboard.vue`

---

## Phase 7: User Story 4 - Year totals and costs (Priority: P3)

**Goal**: `GET /api/dashboard/yearly` with year-to-date, previous year same period and cost (FR-013, FR-014).

**Independent Test**: Oil property with two priced deliveries and a water meter with a `PriceEntry`: yearly shows consumption and cost; removing the price makes cost `null` but consumption stays.

### Tests for US4 (write first)

- [ ] T043 [P] [US4] Pure tests in `backend/tests/test_consumption_stats.py` for `year_summary` (year-to-date, same elapsed span of previous year by clipping, `delta_pct` null when previous <= 0) and for cost rules (oil from delivery `total_price` or `liters * price_per_liter`, unpriced deliveries ignored, `cost_complete`; water/electricity price valid at month end, no price gives `None`)
- [ ] T044 [P] [US4] API tests in `backend/tests/test_dashboard_statistics.py` for `GET /api/dashboard/yearly`: scopes, 401, foreign scope 403/404, 422 on ambiguous parameters, cost omitted without price (never 0)

### Implementation for US4

- [ ] T045 [US4] Implement `year_summary` and cost calculation in `backend/app/services/consumption_stats.py` (price lookup in `backend/app/services/stats_loader.py`); make T043 pass
- [ ] T046 [US4] Implement `GET /api/dashboard/yearly` in `backend/app/routers/dashboard.py`; make T044 pass; add `cost` per month to `/monthly` where a price is known
- [ ] T047 [P] [US4] Year totals and cost display (year to date vs. previous year, percent, cost or nothing) in `frontend/src/views/Dashboard.vue`; i18n keys

---

## Phase 8: User Story 5 - Unusual consumption marker (Priority: P3)

**Goal**: `unusually_high` for water and electricity (FR-015).

**Independent Test**: Water meter 0.4 m³/day yearly, 1.5 m³/day over 30 days: card shows the marker; same data on an oil tank shows none.

### Tests for US5 (write first)

- [ ] T048 [P] [US5] Pure tests for `unusually_high` (factor 2 boundary, 60-day data minimum, oil always false) in `backend/tests/test_consumption_stats.py`; API test that the summary meter entry carries the flag in `backend/tests/test_dashboard_statistics.py`
- [ ] T049 [P] [US5] Vitest assertion in `frontend/src/components/__tests__/TrendLine.test.ts` that the marker renders only when `unusually_high` is true

### Implementation for US5

- [ ] T050 [US5] Implement `unusually_high` with named constants (`UNUSUAL_FACTOR = 2`, `UNUSUAL_MIN_DAYS = 60`) in `backend/app/services/consumption_stats.py`; add it to the summary meter entries in `backend/app/routers/dashboard.py`
- [ ] T051 [US5] Render the marker in `frontend/src/components/TrendLine.vue`; i18n keys

---

## Phase 9: Polish and cross-cutting

- [ ] T052 [P] Performance check (SC-006): add a test in `backend/tests/test_dashboard_statistics.py` that counts SQL statements for the summary with 10 meters and asserts the number does not grow with meter count; compare to `main`
- [ ] T053 [P] Security review pass against plan.md OWASP mapping: all new endpoints behind `get_current_user`, parameters validated, no raw SQL, no `v-html`; run `bandit` and `eslint` as in `.github/workflows/test.yaml`
- [ ] T054 [P] Update `README.md` dashboard feature line (statistics, monthly view, overall figures) and the API notes if present; no new env variables
- [ ] T055 Run the full suites (`cd backend && python -m pytest -q`, `cd frontend && npx vitest run && npm run build`) and the walkthrough in `specs/015-consumption-statistics/quickstart.md`
- [ ] T056 Prepare the PR description: summary, the deliberate change of the oil range with several tanks (FR-024, justification), the new `tzdata` dependency (supply-chain justification), OWASP mapping, tests added

---

## Dependencies and order

- Phase 1 -> Phase 2 -> user stories. Phase 2 (T004..T011) blocks everything; T004 first.
- US1 (P1) is independent after Phase 2 and is the MVP.
- US2 and US3 are independent of each other; both need only Phase 2 (US3 also uses T011).
- US6 needs US1 (rates) and T011; its monthly part (T040, T042) needs US2.
- US4 needs Phase 2; its cost per month (T046) touches the US2 endpoint, so do it after US2.
- US5 needs US1 (recent rate).
- Polish last.

Within a story: tests first (must fail), then service, then router, then frontend.

## Parallel examples

- After T008: T012, T013, T014, T015 can be written in parallel (four different files or test classes).
- US2 tests T021..T024 are independent; T028 and T029 can run in parallel with T027 once the contract is fixed.
- US3 (Phase 5) and US2 (Phase 4) can be developed by different people after Phase 2.

## Implementation strategy

1. MVP: Phases 1, 2, 3 (US1) -> ship the 30-day rate and trend (solves the heating-season problem).
2. Increment 2: US3 (range fix) and US2 (monthly view).
3. Increment 3: US6 (properties and overall), then US4, US5.
4. Each increment ends green on `pytest`, `vitest`, `bandit`, `eslint`, and the regression test T004.

## Task summary

56 tasks: Setup 3, Foundational 8, US1 9, US2 10, US3 4, US6 8, US4 5, US5 4, Polish 5.
