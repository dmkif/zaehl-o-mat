# Implementation Plan: Consumption Statistics (Recent Rate, Monthly View, Trends)

**Branch**: `015-consumption-statistics` | **Date**: 2026-11-02 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/015-consumption-statistics/spec.md`

## Summary

Add derived consumption statistics to the dashboard and meter detail page: 30-day rate, rate since the last reading and trend against the yearly average per meter; consumption per calendar month with previous-year comparison; oil range at the recent rate; year-to-date totals and costs; an "unusually high" marker; and per-property plus overall (cross-property) figures.

Approach: everything is **derived on request** from existing readings, deliveries and prices. No schema change, no new table, no new setting. One new pure service module (`consumption_stats.py`) works on the `Interval` list that feature 005 already produces for oil; water and electricity get the same `Interval` representation through a small builder, so all meter types share one calculation (FR-018). A single loader fetches readings for many meters in batch queries (keeps the dashboard as fast as today, SC-006) and is the one place where "readings that count" is chosen (so feature 016 can later restrict it to released readings). The summary endpoint gets additive fields; two new read-only endpoints serve the monthly and yearly views. The frontend adds a trend line to the meter card, a statistics block with property/overall scope on the dashboard, and a monthly chart + table on the meter detail page.

## Technical Context

**Language/Version**: Python 3.12 (backend image `python:3.12-slim`), TypeScript / Vue 3.5 (frontend)

**Primary Dependencies**: FastAPI 0.135, SQLAlchemy 2.0, Pydantic 2 (existing); vue-i18n, Pinia, vue3-apexcharts (existing). One new pinned backend dependency: `tzdata` (see research R4).

**Storage**: PostgreSQL in production, SQLite in tests. **No schema change.**

**Testing**: pytest (`backend/tests/`, pure-function tests plus API tests with the existing fixtures), vitest (`frontend/src/**/__tests__`), existing Playwright E2E untouched.

**Target Platform**: Self-hosted web app (Compose and Helm), browser UI incl. mobile.

**Project Type**: Web application (FastAPI backend + Vue frontend).

**Performance Goals**: Dashboard summary not slower than today for 10 meters x 5 years of readings (SC-006); monthly/yearly endpoints well under one second for 24 months of data of one property.

**Constraints**: Existing figures unchanged (FR-017); additive API changes only; month boundaries in Europe/Berlin (FR-019); no data of inaccessible properties in any figure, including totals (FR-016, SC-008).

**Scale/Scope**: A household: a few properties, up to tens of meters, thousands of readings. No pagination needed.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design (below).*

| Principle | Assessment |
|-----------|------------|
| I. Test-Backed Changes | PASS. Tests first: pure-function tests for the stats service (rates, month split, coverage states, overall aggregation), API tests per endpoint incl. access, vitest for the formatting helpers and components. Existing tests (incl. `avg_daily_consumption_365d`) must stay green. |
| II. Graceful Degradation | PASS. No OCR/LLM dependency. |
| III. Secure-by-Default Access | PASS. Every new endpoint authorizes server-side with the existing helpers (`get_meter_with_access`, `require_property_access`, accessible ids). |
| IV. Schema via Migrations | PASS, not applicable: nothing stored (confirmed in R9). |
| V. Deployment Parity | PASS, not applicable: no new env var, secret or service. The new `tzdata` package is a Python requirement, shipped by the image build for both Compose and Helm. |
| VI. Brownfield Respect | PASS. Router/service split kept; existing response fields keep name and type and, with one justified exception, their value (oil range with several tanks was wrong: last tank won; now the tank group sum, FR-041, to be stated in the PR description); new fields are additive; `avg_daily_consumption_365d` is produced by the new shared code but verified identical by regression tests (SC-005). |
| VII. OWASP Top 10 | PASS with mapping below. |
| VIII. Authenticated Communication | PASS. No unauthenticated endpoint; no outbound calls. |
| IX. Server-Side Validation | PASS. Query parameters validated by Pydantic/FastAPI types (UUID, enum, bounded ints). |
| X. Verifiable Security | PASS. Negative tests: unauthenticated, other-property, malformed parameters, subtraction-leak test for totals. |

No violations; Complexity Tracking not needed.

### OWASP Top 10 (2025) mapping

- **A01 Broken Access Control**: Scope resolution is the main risk. `meter_id` scope: meter loaded, then `require_property_access(read)` on its property; unknown and foreign meters get the same answer (404 vs 403 follows the existing `meter_consumption` behaviour: 404 unknown, 403 foreign — kept for consistency; the new endpoints use the existing helpers so the behaviour is identical to today). `property_id` scope: `require_property_access`. No scope: only the caller's accessible property ids (global admins: all). Overall totals are computed only from the accessible set, and the number of properties used is returned, so a total can never be differenced against data the user cannot see. No outbound URL handling (no SSRF surface).
- **A02 Security Misconfiguration**: no new configuration, headers unchanged.
- **A03 Supply Chain**: one new dependency `tzdata`, pinned `==`, data-only package from PyPI (the IANA tz database); needed because `python:3.12-slim` ships no system zoneinfo; covered by the existing Trivy scan. No frontend dependency added.
- **A04 Cryptographic Failures**: not affected.
- **A05 Injection**: ORM queries only; no raw SQL; month keys and scope values are validated types, never interpolated; frontend renders numbers/strings through Vue bindings (no `v-html`).
- **A06 Insecure Design**: threat notes in research R10 (cross-property inference via totals, cost data exposure, expensive requests). Costs follow the same property read access as readings.
- **A07 Authentication Failures**: no change; endpoints sit behind `get_current_user`.
- **A08 Data Integrity**: no LLM/OCR input is trusted here; statistics are computed from stored, already validated readings. Unknown oil intervals are never counted.
- **A09 Logging**: read-only endpoints; access-denied responses go through the existing handlers/logging. No secrets in logs.
- **A10 Exceptional Conditions**: missing data returns `null`/"no_data", never 0 or an exception (SC-004); division by zero guarded; invalid parameters return 422; fail closed on access.

## Project Structure

### Documentation (this feature)

```text
specs/015-consumption-statistics/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── api.md
├── checklists/requirements.md
└── tasks.md             # created by /speckit-tasks
```

### Source Code (repository root)

```text
backend/
├── requirements.txt                     # + tzdata==<pinned>
├── app/
│   ├── services/
│   │   ├── oil_consumption.py           # + optional window_days parameter (default 365, unchanged behaviour)
│   │   ├── oil_loader.py                # readings via the shared selection; + window_days
│   │   ├── consumption_stats.py         # NEW, pure: intervals for non-oil, recent/last rate, trend,
│   │   │                                #   month split + coverage, year-to-date, overall aggregation, flags
│   │   └── stats_loader.py              # NEW, DB glue: batch readings ("counting readings"), intervals per meter,
│   │                                    #   prices; the only place that selects which readings count
│   ├── routers/dashboard.py             # summary: additive fields + `statistics`; new /monthly and /yearly
│   └── schemas/dashboard.py             # NEW: Pydantic response models for the new endpoints
└── tests/
    ├── test_consumption_stats.py        # NEW, pure functions
    ├── test_dashboard_statistics.py     # NEW, API: summary fields, monthly, yearly, access, negatives
    └── test_dashboard.py                # existing; regression for unchanged values

frontend/src/
├── utils/consumptionStats.ts            # NEW: trend classification/format, month labels (+ __tests__)
├── components/
│   ├── TrendLine.vue                    # NEW: 30-day rate, trend %, since-last-reading, unusual marker
│   ├── MonthlyConsumption.vue           # NEW: chart + table, props: scope (meter | property | all)
│   └── MeterCard.vue / StatCard.vue     # unchanged API, slot used
├── views/Dashboard.vue                  # trend line, property + overall blocks, 30-day range, scope switch
├── views/MeterDetail.vue                # monthly block
└── locales/de.ts, en.ts                 # new keys
```

**Structure Decision**: Existing web-application layout; pure logic in `app/services/`, HTTP in `app/routers/`, as Principle VI requires. A separate `stats_loader.py` (not folded into `oil_loader.py`) because it serves all meter types and owns the "which readings count" decision; `oil_loader` delegates its reading query to it so there is exactly one such place.

## Phases

- **Phase 0**: [research.md](research.md) — decisions on interval reuse, single reading selection, window length for monthly/yearly, timezone, month split and coverage, comparisons, overall aggregation, thresholds, API shape, no-migration check.
- **Phase 1**: [data-model.md](data-model.md) (derived structures, no tables), [contracts/api.md](contracts/api.md), [quickstart.md](quickstart.md).

## Post-Design Constitution Re-check

Design keeps all gates green: no stored data (IV, V n/a), access via existing helpers (III, VII-A01), additive API only (VI), one dependency with pin (VII-A03). Open risk carried into tasks: the regression guarantee for the yearly average (SC-005) — tasks must add the equality test before refactoring the old helper.

## Complexity Tracking

No constitution violations to justify.
