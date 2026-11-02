# API Contract: Consumption Statistics

All endpoints require authentication (`Authorization: Bearer`), are read-only and rate-limited like the existing dashboard endpoints. Authorization: scope is checked server-side with the existing helpers (Principle III / VII-A01). Numbers are JSON numbers (not strings) unless noted; missing data is `null`, never `0` (SC-004). Unit = the meter type's unit (`m³`, `kWh`, `L`).

## 1. `GET /api/dashboard/summary` (extended, additive)

Existing fields unchanged. New fields:

**Per entry of `meters[]`**

| Field | Type | Meaning |
|-------|------|---------|
| `avg_daily_consumption_30d` | number\|null | FR-001/004 |
| `daily_consumption_last_interval` | number\|null | FR-003 |
| `trend_30d_vs_365d_pct` | number\|null | FR-002, one decimal, signed |
| `unusually_high` | boolean | FR-015; always false for oil |

**New top-level fields**

```json
{
  "oil_reichweite_30d": { "<property_id>": 50 },
  "statistics": {
    "by_property": {
      "<property_id>": {
        "name": "Haus",
        "types": {
          "water": { "unit": "m³", "meter_count": 2,
                     "yearly": 0.2, "recent_30d": 0.3, "trend_pct": 50.0 }
        }
      }
    },
    "overall": {
      "water": { "unit": "m³", "properties_used": 2,
                 "yearly": 0.8, "recent_30d": 1.1, "trend_pct": 37.5,
                 "mean_per_property": { "yearly": 0.4, "recent_30d": 0.55 } }
    }
  }
}
```

- `oil_reichweite_30d`: only for properties with a 30-day rate > 0 (R11).
- `overall` is `null`/absent unless the caller has >= 2 accessible properties (FR-021). `mean_per_property` absent when `properties_used == 1` (FR-022).
- Only properties and meters the caller may read are included.

## 2. `GET /api/dashboard/monthly`

Query: `meter_id` (UUID, optional) **or** `property_id` (UUID, optional) — not both; neither = all accessible properties. `months` (int 1..24, default 12).

```json
{
  "scope": { "kind": "meter|property|all", "id": "<uuid>|null" },
  "series": [
    {
      "meter_type": "oil", "unit": "L",
      "months": [
        { "month": "2026-10", "value": 41.2, "state": "partial", "estimated": false,
          "previous_year": { "value": 12.0, "state": "complete" },
          "delta_pct": null, "cost": null }
      ]
    }
  ]
}
```

- One series per meter type in scope (one series for `kind: meter`). Months oldest first; always exactly `months` entries; months without data have `value: null`, `state: "no_data"`.
- Errors: `404` unknown meter/property (existing behaviour), `403` no access, `422` invalid/ambiguous parameters, `401` unauthenticated.

## 3. `GET /api/dashboard/yearly`

Query: `meter_id` | `property_id` | none, as above.

```json
{
  "scope": { "kind": "all", "id": null },
  "as_of": "2026-11-02T10:00:00Z",
  "series": [
    {
      "meter_type": "oil", "unit": "L",
      "year_to_date": 612.0,
      "previous_year_same_period": 540.0,
      "delta_pct": 13.3,
      "cost": 640.0, "cost_complete": true
    }
  ]
}
```

- Same errors as `/monthly`. `cost` is `null` when no price is known; `cost_complete: false` when some periods lacked a price (R8).

## Compatibility

- Additive only: no existing field is renamed, retyped or removed; `avg_daily_consumption_365d`, `oil_reichweite`, type aggregates and cost forecast keep their values (FR-017, SC-005).
- No new unauthenticated endpoint (Principle VIII).

## Negative tests required (Principle X)

For each new endpoint/field: unauthenticated -> 401; user without access to the property/meter -> 403/404 with no data; `meter_id` + `property_id` together -> 422; `months=0`, `months=25`, non-UUID -> 422; a user with access to one of two properties never receives data or totals of the other (SC-008).
