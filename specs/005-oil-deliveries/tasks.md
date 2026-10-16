# Tasks: Oil Deliveries and Correct Consumption Average

**Input**: `specs/005-oil-deliveries/` (spec.md, plan.md, research.md, data-model.md, contracts/api.md, quickstart.md)

**Tests**: REQUIRED and written first (FR-015). Each test task must be run and seen FAILING before its implementation task starts.

**Organization**: By user story. Backend tests use the existing helpers in `backend/tests/conftest.py` and `test_dashboard.py` (`make_meter`, `make_reading`).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on open tasks)
- Paths relative to the repository root `~/zaehl-o-mat-005-oil-deliveries`

---

## Phase 1: Setup

- [x] T001 Read `backend/tests/conftest.py`, `backend/tests/test_dashboard.py`, `backend/app/permissions.py`, `backend/app/schemas/readings.py` to confirm helper names and the permission API used below (no code change)
- [x] T002 [P] Create empty module files so imports resolve: `backend/app/services/oil_consumption.py`, `backend/app/routers/oil_deliveries.py`, `backend/app/schemas/oil_deliveries.py`

---

## Phase 2: Foundational (schema, blocks all stories)

- [x] T003 Update `backend/app/models.py`: make `OilDelivery.total_price` and `price_per_liter` nullable, add `created_by_id` (FK users, set null); add model `OilDeliveryDismissal` (`oil_delivery_dismissals`: id, meter_id, from_reading_id, to_reading_id, dismissed_by_id, created_at, UNIQUE(from_reading_id, to_reading_id)) per `data-model.md`
- [x] T004 Write migration `backend/alembic/versions/0005_oil_deliveries.py` (down_revision `0004`): alter columns nullable, add `created_by_id`, create dismissal table; downgrade drops them, fills NULL prices with 0, restores NOT NULL
- [x] T005 Verify migration on a throw-away PostgreSQL 18 container (podman): `alembic upgrade head`, insert a delivery, `downgrade 0004`, `upgrade head`; record the commands and result in `specs/005-oil-deliveries/quickstart.md` notes

**Checkpoint**: models import, test DB creates tables, migration reversible.

---

## Phase 3: User Story 1 - Correct daily average (P1) 🎯 MVP

**Goal**: tile, range and forecast use one correct rate, no deliveries needed yet.
**Independent Test**: eight real readings give 3.2-3.6 L/day on the dashboard summary.

### Tests first

- [x] T006 [P] [US1] `backend/tests/test_oil_consumption.py`: table-driven tests for `compute_oil_consumption(readings, deliveries, dismissed, now)`: (a) the eight real readings (1374, 1353, 1365, 1289, 1207, 1074, 945, 816 L, 2026-04-02 to 2026-09-13) -> 3.2-3.6 L/day, (b) the +12 L noise reading does not inflate, (c) fewer than two readings -> rate None, (d) identical timestamps contribute no days, (e) unsorted input, (f) readings older than 365 days ignored, (g) only noise -> rate close to 0 and range empty
- [x] T007 [P] [US1] Add to `backend/tests/test_dashboard.py`: oil tile (`avg_daily_consumption_365d`) for the real-data case = 3.2-3.6; Reichweite = floor(level / displayed rate); cost forecast for an oil meter uses the same rate*30; non-oil meters unchanged (existing tests stay); replace any assertion that encodes the old oil behaviour
- [x] T008 [US1] Run T006/T007 against current code and confirm they fail for the right reason (record the failing output summary in `specs/005-oil-deliveries/quickstart.md` notes)

### Implementation

- [x] T009 [US1] Implement `backend/app/services/oil_consumption.py`: dataclasses `Interval`, `ProbableDelivery`, `ConsumptionResult`; per-interval balance (before - after + delivered), net sum, rate clamped >= 0 over the last 365 days, jump threshold `max(50 L, 5 % of highest level in window)` as module constants with docstring (research R2, R3)
- [x] T010 [US1] Change `backend/app/routers/dashboard.py`: for `MeterType.oil` use the service for `avg_daily_consumption_365d`, `oil_reichweite` (floor(level/rate), empty if rate empty/0) and `cost_forecast` (rate * 30); remove the oil use of `_avg_daily_consumption_365d`; also use the service for the oil case in `property_type_aggregates`
- [x] T011 [US1] Run T006/T007 and the full backend suite; all green

---

## Phase 4: User Story 2 - Record a delivery (P1)

**Goal**: deliveries can be recorded and change the balance correctly.
**Independent Test**: 700 L, delivery 1000 L, 1600 L gives 100 L consumption for the interval.

### Tests first

- [x] T012 [P] [US2] Extend `backend/tests/test_oil_consumption.py`: recorded delivery between 700 and 1600 L -> interval 100 L; delivery equals rise -> 0, never negative; several deliveries each in their own interval; delivery at exactly a reading time assigned once to the interval ending there; delivery before first / after last reading ignored; two deliveries in one interval summed; two oil meters -> delivery goes to the meter with the larger rise
- [x] T013 [P] [US2] `backend/tests/test_oil_deliveries.py`: CRUD happy path (create with litres only, with total only -> per-litre derived, with per-litre only -> total derived, inconsistent prices 422); list order newest first; PATCH partial; DELETE and recalculation visible in the dashboard summary
- [x] T014 [P] [US2] Validation tests in `backend/tests/test_oil_deliveries.py`: litres 0, negative, NaN, inf, 100001, 1e999; negative prices; date year 1999, date > now + 1 day; note > 2000 chars; supplier > 255; extra/unknown fields; wrong types -> 422 and nothing stored
- [x] T015 [US2] Run T012-T014 and confirm they fail (routes missing / behaviour missing)

### Implementation

- [x] T016 [P] [US2] `backend/app/schemas/oil_deliveries.py`: `OilDeliveryCreate`, `OilDeliveryUpdate`, `OilDeliveryResponse` (strict, `extra="forbid"`, bounds per `data-model.md`, price derivation validator)
- [x] T017 [US2] `backend/app/routers/oil_deliveries.py`: GET/POST/PATCH/DELETE under `/properties/{property_id}/oil-deliveries` with `require_property_access` levels read/write/delete, delivery id bound to property id (404 otherwise), set `created_by_id`, info log line (user id, property id, action, delivery id; no free text); register router in `backend/app/main.py`
- [x] T018 [US2] Make the service use deliveries: in `dashboard.py` load the property's deliveries and pass them to `compute_oil_consumption`; extend the service for delivery assignment and clamping (research R4, R5)
- [x] T019 [US2] Run T012-T014 and full suite; all green

---

## Phase 5: User Story 3 - Detect unrecorded deliveries (P1)

**Goal**: big jumps without delivery are flagged, excluded, and can be confirmed or dismissed.
**Independent Test**: 700 -> 1600 L without delivery: average unaffected and an open probable delivery of about 900 L is reported.

### Tests first

- [x] T020 [P] [US3] Extend `backend/tests/test_oil_consumption.py`: jump above threshold without delivery -> interval unknown (no litres, no days) and listed as probable; jump just below threshold absorbed; dismissed pair stays excluded and not listed; threshold scales with tank size (small tank 200 L, rise 60 L flagged; large 2000 L, rise 90 L not flagged); a recorded delivery removes the probable entry
- [x] T021 [P] [US3] `backend/tests/test_oil_deliveries.py`: `GET .../probable` lists the open entry; `POST .../probable/dismiss` idempotent (twice -> 204 both, one row), pair not an open probable delivery -> 404, pair from another property -> 404, concurrent dismiss (two threads) leaves one row; creating a delivery makes the entry vanish; dashboard summary contains `oil_probable_deliveries` only for readable properties
- [x] T022 [US3] Run T020/T021 and confirm failing

### Implementation

- [x] T023 [US3] Service: probable-delivery detection and dismissal input; `ConsumptionResult.probable` list with estimated litres (rise rounded to whole litres)
- [x] T024 [US3] Router: add `GET /oil-deliveries/probable` and `POST /oil-deliveries/probable/dismiss` (write level, unique-constraint-safe insert via get-or-create handling `IntegrityError`); dashboard summary gets `oil_probable_deliveries`
- [x] T025 [US3] Run T020/T021 and full suite; all green

---

## Phase 6: User Story 4 - Noise robustness (P2)

**Goal**: noisy readings do not change the average noticeably.

- [x] T026 [P] [US4] Extend `backend/tests/test_oil_consumption.py`: 1000/995/1003/990 over four days within 10 % of first-last value; same consumption sampled densely with +-8 L noise vs two readings differ by at most error/period; telescoping property test (random noise below threshold: rate equals (first - last)/days within 1e-6)
- [x] T027 [US4] Fix any failing case in `backend/app/services/oil_consumption.py` (expected: none if T009 follows research R2); document the net-balance rule in the module docstring

---

## Phase 7: User Story 5 - Access and input control (P2)

**Goal**: all access rules proven by tests.

- [x] T028 [P] [US5] `backend/tests/test_oil_deliveries.py`: access matrix on all six routes for: no token (401), user without role in the property, read-only user, writer (create/update ok, delete 403), property admin (delete ok), global admin, user of a different property; delivery id of property B used under property A -> 404; error bodies do not reveal existence
- [x] T029 [US5] Fix any gap found in `backend/app/routers/oil_deliveries.py` until T028 passes; confirm the full suite is green

---

## Phase 8: Frontend

- [x] T030 [P] Add i18n texts (de, en) in `frontend/src/locales/` for deliveries list/form, probable-delivery notice, confirm, dismiss, validation messages
- [x] T031 [P] `frontend/src/views/OilDeliveries.vue`: list per property, form (date/time, litres, optional total or per-litre price, supplier, note), edit, delete with confirmation; hide write/delete controls by property role; wire route in `frontend/src/router/index.ts` and a link from Meters/Properties
- [x] T032 `frontend/src/views/Dashboard.vue`: show `oil_probable_deliveries` notice with period and approximate litres, buttons "Als Lieferung erfassen" (opens form pre-filled) and "Als Lesefehler verwerfen"
- [x] T033 Frontend checks: `npm run lint`, `npm run build`, eslint-security warnings unchanged; manual check per `quickstart.md` step 4 with the local stack

---

## Phase 9: Polish

- [ ] T034 [P] Update `README.md` (feature description, new endpoints) and, if the project keeps one, the API docs
- [ ] T035 Run the full backend suite with coverage gate, pre-commit hooks, Trivy/bandit as in CI; fix findings
- [ ] T036 Record in `specs/005-oil-deliveries/quickstart.md` notes: the real-data result number, migration check, final test count
- [ ] T037 Release note and version bump decision (minor); chart `appVersion` if the repo convention requires; no k3s-gitops change except the chart version bump afterwards

---

## Dependencies & Order

- Phase 1 -> Phase 2 -> Phase 3 (MVP) -> Phase 4 -> Phase 5 -> Phases 6/7 (parallel) -> Phase 8 (needs Phase 4/5 endpoints) -> Phase 9.
- Within a story: tests -> confirm failing -> implementation -> green.
- `dashboard.py` and `oil_consumption.py` are touched by US1-US3 sequentially; do not parallelize those implementation tasks.

## Parallel Example

```text
Phase 3 tests: T006 + T007 (different files)
Phase 4 tests: T012 + T013 + T014 (T013/T014 same file but independent test groups; write in one pass if conflicts)
Phase 8:       T030 + T031, then T032
Phase 6/7:     T026 + T028
```

## Implementation Strategy

- MVP = Phase 1-3: the wrong 10 L/day fixed without any new UI.
- Next ship Phase 4+5 (deliveries and detection) before the real oil delivery arrives.
- Phases 6-8 follow; frontend can ship together with the backend release.
- Do not commit or push without operator request.
