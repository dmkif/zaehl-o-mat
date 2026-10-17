# Implementation Plan: Oil Deliveries and Correct Consumption Average

**Branch**: `005-oil-deliveries` | **Date**: 2026-10-17 | **Spec**: [spec.md](spec.md)

## Summary

One pure calculation module (`app/services/oil_consumption.py`) turns the oil meter's readings, the recorded deliveries and the dismissed jumps into per-interval balances, a consumption rate (L/day) and a list of probable deliveries. The dashboard tile, the range ("Reichweite") and the cost forecast all call it, replacing the three divergent ad-hoc calculations. New endpoints let authorised users record, correct and delete deliveries and confirm or dismiss probable deliveries. A reversible migration makes prices optional and adds a table for dismissed jumps. The frontend gets a notice on the dashboard and a small deliveries view per property. Tests come first; the first test encodes the operator's eight real readings (about 3.4 L/day).

## Technical Context

**Language/Version**: Python 3.12 (FastAPI, SQLAlchemy 2, Alembic), TypeScript/Vue 3 (frontend)

**Primary Dependencies**: existing only; no new dependency

**Storage**: PostgreSQL (existing table `oil_deliveries`, one new table, two columns made nullable)

**Testing**: pytest (SQLite test DB as today) for calculation and API; migration verified up/down on a throw-away PostgreSQL 18 container; vitest for the new frontend pieces where the project already has component tests

**Target Platform**: Compose and Helm (parity, no config change expected)

**Project Type**: web application (backend + frontend)

**Performance Goals**: dashboard summary stays one pass over readings per oil meter; deliveries per property are few (tens per year)

**Constraints**: no change for water/electricity/gas calculations; no data loss; migration reversible; server-side validation authoritative (Principle IX)

**Scale/Scope**: one calculation module (~150 lines), 5 endpoints, 1 migration, 2 frontend changes

## Constitution Check

| Principle | Assessment |
|---|---|
| I Test-Backed Changes | Tests first, including the real-data case; the oil average test encoding the old defect is replaced. PASS |
| II Graceful Degradation | Calculation returns empty rate with fewer than two usable readings; no optional subsystem involved. PASS |
| III Secure-by-Default Access | New endpoints use the central property permission helpers (read/write/delete). PASS |
| IV Schema Changes via Migrations | Migration `0005` (reversible), verified on PostgreSQL from the existing migrations. PASS |
| V Deployment Parity | No env/config/chart change. PASS (re-checked after design) |
| VI Brownfield Respect | Follows router/service/schema split, reuses `permissions.py`; replaces `_avg_daily_consumption_365d` for oil only. PASS |
| VII OWASP Top 10 (2025) | A01 property scoping on every endpoint and cross-property id binding; A05 typed/bounded input, ORM only; A06 unique dismissal key so concurrent dismiss/confirm cannot double-apply; A09 audit log lines for create/update/delete (no personal data); A10 validation errors 4xx, no partial writes. PASS |
| IX Input Validation | Pydantic bounds for litres, prices, date window, text length. PASS |
| X Verifiable Security | Negative tests for access and input run in normal CI. PASS |

No violations; complexity tracking not needed.

## Project Structure

### Documentation (this feature)

```text
specs/005-oil-deliveries/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/api.md
├── checklists/requirements.md
└── tasks.md            # /speckit-tasks
```

### Source Code

```text
backend/
├── app/
│   ├── services/oil_consumption.py        # NEW: pure calculation
│   ├── routers/oil_deliveries.py          # NEW: CRUD + suggestions
│   ├── schemas/oil_deliveries.py          # NEW: request/response models
│   ├── routers/dashboard.py               # CHANGED: oil tile, range, forecast use the service
│   ├── models.py                          # CHANGED: nullable prices, OilDeliveryDismissal
│   └── main.py                            # CHANGED: include router
├── alembic/versions/0005_oil_deliveries.py   # NEW
└── tests/
    ├── test_oil_consumption.py            # NEW: pure calculation (first)
    ├── test_oil_deliveries.py             # NEW: API, access, validation
    └── test_dashboard.py                  # CHANGED: oil average/Reichweite/forecast cases
frontend/src/
├── views/Dashboard.vue                    # CHANGED: probable-delivery notice
├── views/OilDeliveries.vue                # NEW: list, form, correct, delete
├── router, i18n                           # CHANGED: route and texts (de/en)
```

**Structure Decision**: Calculation lives in a service module without database access (takes plain lists), so it is testable with plain data and reusable by tile, range and forecast.

## Complexity Tracking

No violations.
