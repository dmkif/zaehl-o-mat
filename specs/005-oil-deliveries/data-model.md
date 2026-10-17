# Data Model: Oil Deliveries

## OilDelivery (existing table `oil_deliveries`, changed)

| Field | Type | Rule |
|---|---|---|
| id | bigint PK | |
| property_id | uuid FK properties (cascade) | access scope |
| liters | numeric(10,2) | > 0 and <= 100000 |
| total_price | numeric(10,2) | **now nullable**, >= 0 |
| price_per_liter | numeric(8,4) | **now nullable**, >= 0; derived from total or vice versa when one given |
| delivered_at | timestamptz | not later than now + 1 day; not earlier than 2000-01-01 |
| supplier | varchar(255) | optional |
| note | text | optional, max 2000 chars at the API |
| created_by_id | uuid FK users (set null) | **new**, nullable |
| created_at | timestamptz | existing |

## OilDeliveryDismissal (new table `oil_delivery_dismissals`)

| Field | Type | Rule |
|---|---|---|
| id | bigint PK | |
| meter_id | uuid FK meters (cascade) | |
| from_reading_id | bigint FK readings (cascade) | previous reading |
| to_reading_id | bigint FK readings (cascade) | later reading |
| dismissed_by_id | uuid FK users (set null) | |
| created_at | timestamptz | |
| UNIQUE(from_reading_id, to_reading_id) | | idempotent dismiss |

## Derived (not stored)

- **ConsumptionInterval**: from_reading, to_reading, days, delivered_litres, consumption_litres (may be slightly negative from noise), state `known | probable_delivery | dismissed_jump`.
- **ProbableDelivery**: meter, from/to reading, period, estimated litres (rise, rounded), state `open`.
- **ConsumptionRate**: litres_per_day (nullable), known_days, interval list; consumed by tile, range (floor(level / rate)) and forecast (rate * 30 per month).

## Migration 0005

Upgrade: alter two columns to nullable, add `created_by_id`, create dismissal table. Downgrade: drop dismissal table and `created_by_id`, fill NULL prices with 0, set columns NOT NULL again.
