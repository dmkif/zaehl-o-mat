# Data Model: Consumption Statistics

**No persistent entities are added or changed** (research R9). All structures below are computed per request and exist only in the service layer and the API responses. Existing tables used read-only: `readings`, `meters`, `properties`, `property_users`, `price_entries`, `oil_deliveries`, `oil_delivery_dismissals`.

## Derived structures (service layer, `app/services/consumption_stats.py`)

### Interval (existing, reused from `oil_consumption.py`)

| Field | Meaning |
|-------|---------|
| `from_at`, `to_at` | UTC instants of the two readings |
| `days` | length in days (> 0) |
| `consumption` | litres / m³ / kWh, `None` = unknown (probable delivery, dismissed jump, decreasing water/electricity value) |
| `state` | `known`, `probable_delivery`, `dismissed_jump` (water/electricity decrease: `skipped`) |

Water/electricity intervals are built from consecutive readings (R1). Rule: only a positive delta is consumption; a zero or negative delta is unknown (`skipped`), exactly like the existing yearly average (SC-005).

### MeterRates (per active meter)

| Field | Rule |
|-------|------|
| `yearly` | existing: net known consumption / known days over 365 days (unchanged value) |
| `recent_30d` | net consumption / days of the known intervals whose `to_at` is within the last 30 days, crossing interval counted whole, clamped >= 0; `null` if none |
| `last_interval` | rate of the newest interval if known; `null` otherwise |
| `trend_pct` | `(recent_30d / yearly - 1) * 100`, 1 decimal; `null` if either missing or `yearly <= 0` |
| `unusually_high` | water/electricity: `recent_30d >= 2 * yearly` and data span >= 60 days; oil: always false |

### MonthValue (per meter or aggregated scope, per calendar month in Europe/Berlin)

| Field | Rule |
|-------|------|
| `month` | `YYYY-MM` |
| `value` | sum of interval shares in the month, `null` when `no_data` |
| `state` | `complete` / `partial` / `no_data` (R5) |
| `estimated` | true if the month's value is mostly from intervals > 60 days |
| `previous_year` | `{value, state}` for month - 12, or `null` |
| `delta_pct` | `(value / previous_year.value - 1) * 100` when both `complete` and previous > 0, else `null` |
| `cost` | R8, `null` if no price known |

Invariant (tested): for every interval, the sum of its shares over all months equals its consumption (rounding tolerance 1e-6).

### YearSummary (per meter type, scope-wide)

| Field | Rule |
|-------|------|
| `year_to_date` | consumption 1 Jan .. last counted instant of the current year |
| `previous_year_same_period` | same elapsed span of the previous year |
| `delta_pct` | as above, `null` when previous <= 0 or missing |
| `cost`, `cost_complete` | R8 |

### GroupStatistics (per meter type, for a property and for the overall scope)

| Field | Rule |
|-------|------|
| `unit` | the type's unit |
| `yearly`, `recent_30d`, `trend_pct` | sums of the meters' rates, trend recomputed on the sums |
| `meter_count` | active meters of the type |
| overall only: `properties_used`, `mean_per_property` | R10; `mean_per_property` omitted when `properties_used == 1` |

## Relationships

```
Property 1──* Meter 1──* Reading ──(pairs)──> Interval ──> MeterRates / MonthValue / YearSummary
Property 1──* OilDelivery        ──(oil only, in compute_oil_consumption)──┘
Meter 1──* PriceEntry            ──> cost (water/electricity)
User ──(PropertyUser)──> Property   decides the scope: GroupStatistics.overall is over the user's accessible set
```

## Validation rules (inputs of the new endpoints)

- `months`: integer 1..24, default 12.
- `meter_id`, `property_id`: UUID; not both at once (422).
- `year`: not a parameter; "current" and "previous" calendar year are fixed by the server clock.
- All inputs validated by Pydantic/FastAPI types before any query (Principle IX).

## State transitions

None. (Pending/rejected reading states belong to feature 016; the single reading selection in R2 is the integration point.)
