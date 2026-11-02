# Research: Consumption Statistics

Decisions for the open design points of `plan.md`. Each: Decision / Rationale / Alternatives.

## R1 One interval representation for all meter types

- **Decision**: Reuse `Interval` from `app/services/oil_consumption.py` for every meter type. Oil intervals come from `compute_oil_consumption` (unchanged rules of feature 005). Water/electricity intervals are built by a new pure function `intervals_from_readings`: one `Interval` per consecutive reading pair with `days > 0`; `consumption = delta` for `delta > 0`, else `None` (state `known` / `skipped`). All new figures (30-day rate, last-interval rate, month split, year-to-date) are functions of `list[Interval]`.
- **Rationale**: FR-018 requires one calculation; the existing yearly average for water/electricity (sum of positive deltas / days of those intervals) equals "sum of known intervals / known days" over this representation, so the old helper can be replaced without changing values (SC-005, guarded by a regression test written first).
- **Alternatives**: Separate code paths per type (rejected: the exact drift feature 005 removed); changing oil's interval model (rejected: out of scope, risky).

## R2 One place that selects "readings that count"

- **Decision**: New `stats_loader.counting_readings(db, meter_ids, since)` is the only query that selects readings for statistics, ranges and oil consumption; `oil_loader.load_oil_results` calls it instead of querying `Reading` itself. Today it returns every reading of the meters since `since`.
- **Rationale**: Feature 016 introduces pending/rejected readings; with one selection point it needs a one-line filter instead of touching every statistic. Also enables batching (R3).
- **Alternatives**: Filter later in each consumer (rejected: easy to miss one, the exact bug class the spec's SC-011 in 016 guards against).

## R3 Batch loading, no per-meter queries

- **Decision**: `stats_loader.load_intervals(db, meters, now, window_days)` fetches readings for all given meters in one query (`meter_id IN (...) AND read_at >= since`), groups in Python, and computes intervals per meter. Oil meters additionally load deliveries and dismissals per property (as `load_oil_results` does today, but once per property).
- **Rationale**: The current summary issues one readings query per non-oil meter (`_avg_daily_consumption_365d`) plus per-oil-property queries. The new figures must not make that worse (SC-006); batching makes it better.
- **Alternatives**: SQL window functions for intervals (rejected: oil rules are in Python; SQLite/Postgres differences).

## R4 Time zone and month boundaries

- **Decision**: Month boundaries use `zoneinfo.ZoneInfo("Europe/Berlin")` (FR-019). Reading timestamps (UTC, naive from SQLite) are converted with the existing `_utc` helper, then to Berlin local time before assigning days to months. Add `tzdata==2025.2` (exact pin; bump to the then-current release at implementation) to `backend/requirements.txt`.
- **Rationale**: `python:3.12-slim` has no system tz database, so `ZoneInfo` would raise `ZoneInfoNotFoundError` in the container even though it works on a developer machine. A pinned data-only package is the smallest fix and is covered by the Trivy scan (constitution A03; justification recorded here and in the PR).
- **Alternatives**: Install the Debian `tzdata` package in the Dockerfile (works, but couples the fix to the image and misses the test environment); fixed UTC+1/+2 rules by hand (rejected: DST edge cases); UTC months (rejected by FR-019: a reading at 00:30 local on the 1st belongs to the new month).
- **Test note**: Add a test where a reading at 23:30 UTC on the last day of a month falls into the next month in Berlin summer time.

## R5 Splitting intervals across months and coverage states

- **Decision**: An interval's consumption is distributed over the local calendar days it spans, proportionally by time (the interval is split at local midnight boundaries; each month receives `consumption * overlap_seconds / interval_seconds`). Per month the service tracks `covered_seconds` of known intervals. Month state: `complete` if covered seconds equal the month length, `partial` if `0 < covered < month length`, `no_data` if covered is 0 (and the value is `null`, never 0). The current month is therefore `partial` until it ends. `estimated: true` when more than half of the month's value comes from intervals longer than 60 days.
- **Rationale**: Satisfies FR-009 (monthly values of an interval sum to its consumption — verified by a property test), FR-010 and the long-gap edge case. Splitting by time rather than by whole days avoids rounding drift.
- **Alternatives**: Assign the whole interval to its end month (rejected: wrong for monthly readings, a December 1st reading would book November's consumption into December); extrapolate partial months (rejected by spec).

## R6 Comparisons: trend, previous year

- **Decision**:
  - Trend percent = `(recent / yearly - 1) * 100`, rounded to one decimal; `null` if either value is missing or `yearly <= 0`. Classification (increase > +10, decrease < -10, neutral) is done in the frontend helper, using the number the server returns.
  - Previous-year month = same month, 12 months earlier, taken from the same monthly series (24 months loaded). Percent difference only when both months are `complete`; otherwise the previous-year value is shown without a percent.
  - Year-to-date: current calendar year from 1 January to the last counted instant, against the same period of the previous year (same elapsed span from 1 January, computed from the same interval list by clipping at that instant).
- **Rationale**: Keeps the server numeric and the interface rule (10 %) in one place that is easy to change; avoids comparing a partial month with a full one.
- **Alternatives**: Server-side classification flag (rejected: presentation rule); comparing partial months pro rata (rejected: noisy, hides real changes).

## R7 Window length

- **Decision**: Summary figures keep the 365-day window (`WINDOW_DAYS`). Monthly (24 months) and yearly (current + previous calendar year) load up to ~740 days. `compute_oil_consumption` and `load_oil_results` get an optional `window_days: int = WINDOW_DAYS`; default behaviour is byte-for-byte unchanged.
- **Rationale**: The oil jump threshold is derived from the highest level in the window, so a longer window is a deliberate parameter, not a different algorithm.
- **Alternatives**: Always compute 740 days and slice (rejected: would change the threshold and thus the existing yearly numbers, violating FR-017).

## R8 Cost

- **Decision**:
  - Oil: cost of a period = sum over recorded deliveries in the period of `total_price`, or `liters * price_per_liter` when only the unit price exists; deliveries without any price contribute nothing, and if none of the period's deliveries has a price the cost is `null`. This is the money spent, not the value consumed.
  - Water/electricity: consumption x the `PriceEntry.price_per_unit` valid at the end of the month (latest `valid_from <= month end`); `null` if no price entry exists yet. Year cost = sum of monthly costs; months without a price are omitted from the cost, and the response states `cost_complete: false` so the interface can say "partly".
- **Rationale**: Matches how the data exists (deliveries have prices, meters have price entries) and the spec's "no price, no cost" rule (FR-014) without guessing.
- **Alternatives**: Valuing oil consumption at the last delivery price (rejected: invents a price); showing 0 EUR (rejected by SC-004).

## R9 No schema change (confirmation of the spec's assumption)

- **Decision**: Nothing is stored. Every figure is derived per request. No Alembic migration, no new column, no cache table.
- **Rationale**: Data volume is household scale; caching adds invalidation risk with no need. Checked models: `Reading`, `Meter`, `PriceEntry`, `OilDelivery`, `OilDeliveryDismissal` contain everything required.
- **Alternatives**: Materialised monthly table (rejected: invalidation on edit/delete of readings, migration, drift).

## R10 Overall aggregation and threats

- **Decision**:
  - Overall block per meter type over the caller's accessible properties: `total` = sum of per-property rates, `mean_per_property` = total / number of properties that have a rate (properties without data are excluded, `properties_used` returned), only returned when the caller has >= 2 accessible properties and the type has data in >= 1 of them. `mean_per_property` omitted when `properties_used == 1`.
  - Per-property rate of a type = sum of the rates of its active meters of that type (two tanks add up, FR spec scenario 5). Units differ => no total for that type (defensive; today one unit per type).
  - Replaced meters (`replaced_at` not null) are excluded, as in the existing summary.
  - Trend of a group = same formula on the group's 30-day and yearly totals.
- **Threats considered (A06)**: (1) *Inference by differencing*: a user sees total over their accessible set only and `properties_used`, never a global total; there is no endpoint that returns totals over inaccessible properties. (2) *IDOR via scope parameters*: `meter_id`/`property_id` always pass the access helpers before any data is read. (3) *Resource exhaustion*: `months` bounded 1..24; scope limited to accessible properties; computation is linear in readings of a household. (4) *Cost leakage*: costs are returned only under the same read access as the readings.
- **Alternatives**: Weighted mean by number of meters (rejected: obscure); including inaccessible properties in the total for admins only (admins see everything anyway; same rule: the set is "accessible", which for global admins is all).

## R11 "Unusually high" marker and oil range at recent rate

- **Decision**: `unusually_high` is computed on the server per water/electricity meter: `recent >= 2 * yearly` and the span of counted readings is >= 60 days; always `false` for oil. The oil range at the recent rate is `floor(level / recent)` per property, with `level` = sum of the latest level of its active tanks and `recent` = sum of their 30-day rates; shown only if `recent > 0`. The existing `oil_reichweite` is unchanged.
- **Existing bug, fixed here (operator decision 2026-11-02)**: the existing `oil_reichweite` loop overwrites the property's value per tank, so with two tanks in one property the last tank wins instead of summing. The operator's use case is the group "tank 1 + tank 2 = total", so the range becomes `floor(sum of levels / sum of rates)` over the property's active tanks (FR-041). Single-tank properties get the same value as before (regression test). If any tank of the group has no rate, no group range is shown rather than a too-long partial one.
- **Tank group**: today = all active oil meters of a property. The grouping goes through one function `tank_group_key(meter)` returning the property id. Several heating systems per property or user-defined groups would need a group assignment on the meter (schema change, UI) and are a separate future feature; this keeps the later change local.
- **Known limitation**: feature 005 assigns a delivery to the single tank with the largest rise. A delivery into two tanks at once leaves the other tank's rise as a probable delivery. Not changed here.
- **Rationale**: Thresholds are named constants in the service so they can be tuned without touching the API.

## R12 API shape

- **Decision**: Extend `GET /api/dashboard/summary` additively (per-meter `avg_daily_consumption_30d`, `daily_consumption_last_interval`, `trend_30d_vs_365d_pct`, `unusually_high`; per-property `oil_reichweite_30d`; a `statistics` object with `by_property` and `overall`). Add `GET /api/dashboard/monthly` and `GET /api/dashboard/yearly`, both with a scope given by optional `meter_id` or `property_id` (none = all accessible). Details in [contracts/api.md](contracts/api.md).
- **Rationale**: The dashboard already loads the summary once; one extra block avoids a request waterfall. Monthly and yearly are heavier and optional, so they are separate and loaded on demand. One scope convention for both avoids duplicate access code.
- **Alternatives**: One endpoint per scope (rejected: three near-identical routes); embedding monthly series in the summary (rejected: payload and compute on every dashboard load).

## R13 Frontend

- **Decision**: Pure helpers in `utils/consumptionStats.ts` (trend class, signed percent string, month label, "no data" handling) with vitest tests; components `TrendLine.vue` (in the existing `MeterCard` `extra` slot) and `MonthlyConsumption.vue` (apexcharts bar chart + table, scope prop, selectable previous year overlay); dashboard shows property blocks and, with >= 2 properties, an overall block; keys added to `de.ts` and `en.ts`. German text for the dashboard strings, matching existing wording ("Reichweite", "Verbrauch").
- **Rationale**: Reuses the established apexcharts and slot patterns; no new frontend dependency.
