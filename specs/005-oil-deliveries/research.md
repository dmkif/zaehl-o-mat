# Research: Oil Deliveries and Consumption Average

## R1 - Reproduce the defect on real data
Decision: use the operator's eight readings as the first test. Tile today: sum of rising deltas / their days = 12 L / 1.15 d = 10.4 L/day. Correct: (1374-816) L over 164 d = 3.40 L/day. Rationale: the defect is `invert=False` for oil in the summary plus the "only count one direction" idea itself. Alternative: only flip the flag - rejected, still inflated by noise (falling noise counts, rising noise does not) and ignores deliveries.

## R2 - Calculation: balance per interval, net over the period
Decision: interval consumption = level before - level after + delivered litres inside the interval. Rate = sum of consumption / sum of days over known intervals in the last 365 days. Small negative interval results (noise) are kept so that the sum telescopes to first level - last level + deliveries. The final rate is clamped at >= 0; a rate of 0 gives an empty range. Alternatives: sum of absolute drops (biased upwards by noise), regression line (hides deliveries, harder to explain).

## R3 - Jump rule
Decision: a rise between consecutive readings with no delivery inside the interval is a probable delivery if `rise > max(50 L, 5 % of the highest level in the window)`. Real data: noise 12 L stays below max(50, 68). Example 700 -> 1600: 900 flagged. Interval is "unknown": no litres, no days. Alternatives: fixed 100 L (fails for small tanks), standard deviation based (needs history, unstable with 8 readings).

## R4 - Recorded delivery inside an interval
Decision: interval is (previous reading time, current reading time]; a delivery is assigned by its time to exactly one interval, deliveries outside the first/last reading are ignored for the rate. With a recorded delivery the balance is clamped at >= 0 (spec: never negative). If deliveries from several records fall into one interval they are summed. A delivery recorded for a jump that is larger than the delivered amount leaves the difference as consumption (honest balance). Alternative: always trust reading difference - rejected, would ignore the operator's input.

## R5 - Several oil meters per property
Decision: deliveries belong to the property; a delivery is applied to the oil meter whose interval containing the delivery time shows the largest rise (ties: first by name). Other meters ignore it. Documented, tested with one test; the single-tank case is the default.

## R6 - Dismissed jumps
Decision: new table keyed by the two reading ids (`from_reading_id`, `to_reading_id`) with a unique constraint; dismiss is idempotent. Confirming creates a delivery in the interval, which also removes the notice. If a reading is edited/deleted, the dismissal row cascades away and the jump is re-evaluated. Alternative: store in a JSON column on the meter - rejected, no constraint, no cascade.

## R7 - Optional prices
Decision: make `total_price` and `price_per_liter` nullable; derive the missing one when one is given (total = litres * per-litre, rounded to cents). Migration `0005` is reversible only when no NULL prices exist; the downgrade fills missing prices with 0 and documents it. Alternative: keep NOT NULL and require a price - rejected, the user wants delivery recorded quickly without invoice.

## R8 - Cost forecast
Decision: for oil meters the forecast uses the shared rate (365-day window, same as the tile) instead of the net change over 90 days; other meter types unchanged. The existing 90-day logic is also wrong for tanks (a delivery makes the net change negative).

## R9 - Test strategy
Decision: tests first. (1) `test_oil_consumption.py`: pure cases incl. the eight real readings, noise cancel, jump unknown, recorded delivery, several deliveries, boundary equality, reading order, empty/one reading. (2) API tests for access matrix (no role / read / write / delete / global admin / foreign property / foreign delivery id) and validation (zero, negative, huge litres, future date, NaN, long text, price mismatch). (3) dashboard tests for tile/range/forecast consistency and the notice. (4) migration up/down on throw-away PostgreSQL. Alternative: only API tests - rejected, the calculation needs table-driven tests.

## R10 - OWASP mapping
A01: every route checks property access by level and binds delivery id to property id (404 otherwise). A05: Pydantic types and bounds, ORM queries only. A06: unique dismissal key; creating the same delivery twice is allowed (two real deliveries can look alike) but the notice disappears after the first. A09: info-level log with user id, property id, action, delivery id; no free text. A10: all validation failures 4xx; transactions commit once.
