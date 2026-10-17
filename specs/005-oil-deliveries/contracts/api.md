# API Contract: Oil Deliveries

All under `/api`, bearer token required. Access: read = any property role; write = property manager/admin; delete = property admin; global admin/superadmin = all. Unknown property or delivery id of another property -> 404.

| Method | Path | Access | Body / result |
|---|---|---|---|
| GET | `/properties/{pid}/oil-deliveries` | read | list, newest first: id, delivered_at, liters, total_price?, price_per_liter?, supplier?, note?, created_at |
| POST | `/properties/{pid}/oil-deliveries` | write | `{delivered_at, liters, total_price?, price_per_liter?, supplier?, note?}` -> 201 delivery; if both prices given and inconsistent (> 1 cent per 100 L) -> 422 |
| PATCH | `/properties/{pid}/oil-deliveries/{id}` | write | partial update, same validation; unknown/extra fields rejected (422) |
| DELETE | `/properties/{pid}/oil-deliveries/{id}` | delete | 204; consumption recalculated |
| GET | `/properties/{pid}/oil-deliveries/probable` | read | open probable deliveries: property_id, meter_id, from_reading_id, to_reading_id, from_at, to_at, estimated_liters |
| POST | `/properties/{pid}/oil-deliveries/probable/dismiss` | write | `{from_reading_id, to_reading_id}` -> 204; idempotent; pair must be a currently open probable delivery of the property else 404 |

Confirm = POST a delivery (client pre-fills from the probable entry). The notice disappears because the interval now contains a delivery.

## Dashboard summary changes

- Oil meters: `avg_daily_consumption_365d` from the shared calculation (never counts rises).
- `oil_reichweite[property_id]` = floor(latest level / rate), empty if rate empty or 0.
- New top-level `oil_probable_deliveries`: list as above (each entry includes `property_id`), restricted to properties the user can read.
- `cost-forecast/{meter_id}` for an oil meter uses the shared rate * 30 per month; other types unchanged.

Errors: 401 no token, 403 insufficient level on a visible property, 404 unknown/foreign, 422 validation.

Frontend: route `/properties/:propertyId/oil-deliveries`; confirming pre-fills the form via query `prefill_at` and `prefill_liters`.
