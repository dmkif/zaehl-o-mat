# Feature Specification: Consumption Statistics (Recent Rate, Monthly View, Trends)

**Feature Branch**: `015-consumption-statistics`

**Created**: 2026-11-02

**Status**: Draft

**Input**: User description: "Dashboard-Statistik 'aktueller Verbrauch': zusätzlich zum Durchschnitt über 365 Tage bzw. vorhandene Daten pro Zähler den Verbrauch der letzten ~30 Tage und seit der letzten Ablesung anzeigen, plus Trend gegenüber dem Jahresdurchschnitt (Prozent). Hintergrund: im Sommer verbraucht die Heizung nur Warmwasser, im Herbst steigt der Ölverbrauch wieder, was der Jahresdurchschnitt verdeckt. Gilt für alle Zählertypen (Öl-Tank mit Lieferungen/Sprung-Logik aus Feature 005, Wasser, Strom). Unbekannte Intervalle zählen nicht. Ohne Daten im Fenster: nichts anzeigen. Zusätzlich: prüfen, welche Statistiken sinnvoll sind (z. B. Verbrauch pro Monat), und diese mit einarbeiten."

## Background

The dashboard shows one number per meter: the average daily consumption over the last 365 days (or over the available data). That number is stable but slow. The operator's oil heating shows the problem: in summer it only heats water and uses very little oil, so the yearly average is low. When the cold season starts and the heating runs again, the real consumption rises sharply, but the yearly average barely moves and the operator does not notice the increase.

The same blind spot exists for water and electricity, and the dashboard offers no view of *when* consumption happens (per month, compared with the same month last year). This feature adds statistics that answer the questions an operator of a house actually has:

- "How much am I using **right now** compared with my normal?"
- "How much did I use **per month**, and is this month higher than the same month last year?"
- "What did the year cost me so far and how long will the oil last at the current pace?"

Oil tank consumption keeps using the balance rules of feature 005 (deliveries, probable deliveries, reading noise). Where a statistic needs a figure for oil, it uses that calculation; an interval that is unknown there (probable delivery, dismissed jump) is unknown here too.

### Multiple properties (checked 2026-11-02)

Every dashboard figure is per meter, and the type aggregate ("Verbrauch nach Zählerart") is per property. There is **no** figure across properties. With a second property, the operator sees two separate blocks and no overall number. Story 6 closes this. Who may see or enter what in which property (roles, members, release of readings) is handled by feature `016-roles-and-release`; this feature only uses the existing access rules.

## Research: Which statistics are useful

Common practice in home energy and water monitoring, reduced to what fits this data (manual and photo meter readings at irregular intervals, three meter types, optional prices):

| Statistic | Why it is useful | Included |
|-----------|------------------|----------|
| Recent rate (last 30 days) vs. yearly average, with trend in percent | Shows a change in behaviour or season early (the heating case) | Yes (P1) |
| Rate since the last reading | Fastest feedback after a new reading; noisy, so shown as secondary figure | Yes (P1) |
| Consumption per calendar month, last 12 months | Shows seasonality; basis for every other comparison | Yes (P2) |
| Same month compared with the previous year (absolute and percent) | Removes the seasonal effect, so "higher than last winter" is visible | Yes (P2) |
| Consumption per year (calendar year, year to date vs. same period last year) | Annual bill, long-term trend | Yes (P3) |
| Cost per month/year (readings x known price; oil from recorded deliveries) | Money is what the operator ultimately cares about | Yes (P3, only where a price is known) |
| Oil range ("Reichweite") also at the recent rate | At winter pace the tank lasts shorter than the yearly average says | Yes (P2) |
| Highlight of unusual consumption (recent rate far above average) | Leak or defect detection for water and electricity | Yes (P3, a plain marker, no notifications) |
| Figures per property **and** across all accessible properties | Compares houses, gives the household total | Yes (P2) |
| Heating-degree-day normalisation, weather data | Fairest comparison for heating, but needs an external weather source | No (out of scope) |
| Per-person or per-area figures | No such data exists in the app | No (out of scope) |
| Push notifications / alerts | Separate feature; this one only shows information | No (out of scope) |

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See the current pace next to the yearly average (Priority: P1)

For every meter on the dashboard the operator sees, besides the yearly average, the average daily consumption of the last 30 days, the percentage difference to the yearly average, and the rate since the last reading.

**Why this priority**: This is the problem that triggered the feature: the heating season starts and nothing on the dashboard changes.

**Independent Test**: Meter with readings that show about 1 L/day for 100 days and then about 8 L/day over the last 20 days: the dashboard shows a 30-day rate clearly above the yearly average and a large positive percentage.

**Acceptance Scenarios**:

1. **Given** a meter with readings in the last 30 days, **When** the dashboard is opened, **Then** the 30-day daily rate, the percentage difference to the yearly average (sign shown) and the rate since the last reading are shown with the meter.
2. **Given** a 30-day rate more than 10 % above the average, **When** shown, **Then** the difference is visually marked as an increase; more than 10 % below as a decrease; within 10 % it is shown neutral.
3. **Given** the last reading is older than 30 days, **When** shown, **Then** no 30-day rate is shown (no stale number presented as current); the yearly average stays.
4. **Given** the last interval is unknown for an oil tank (probable delivery or dismissed jump), **When** shown, **Then** no "since last reading" rate is shown for it, and unknown intervals never contribute to the 30-day rate.
5. **Given** reading noise on an oil tank (a small rise between two readings), **When** the 30-day rate is calculated, **Then** the noise is absorbed by the same balance rule as the yearly rate, and the rate is never negative.
6. **Given** a reading interval that starts before the 30-day window and ends inside it, **When** the 30-day rate is calculated, **Then** that interval counts as a whole, so sparse readings (one per month) still give a rate.
7. **Given** fewer than two usable readings, **When** shown, **Then** none of the new figures is shown (empty, not zero).

---

### User Story 2 - Consumption per month and against the same month last year (Priority: P2)

On the meter detail page the operator sees a chart and a table of the consumption per calendar month for the last 12 months (up to 24 if data exists), with the same month of the previous year next to it and the difference in percent.

**Why this priority**: Seasonality is the main reason the yearly average is misleading; the monthly view makes it visible and gives a fair year-over-year comparison.

**Independent Test**: Readings spanning 14 months: the monthly table lists each month with its consumption; the most recent months show the previous year's value and a percentage; months without enough data are marked as such.

**Acceptance Scenarios**:

1. **Given** readings over several months, **When** the monthly view is opened, **Then** each calendar month shows its consumption in the meter's unit.
2. **Given** a reading interval that spans a month boundary, **When** monthly values are calculated, **Then** the interval's consumption is split across the months in proportion to the days in each, so the monthly values add up to the interval total.
3. **Given** a month that has no reading interval covering it (data gap), **When** shown, **Then** it is marked "no data" and not shown as 0.
4. **Given** the same month exists in the previous year, **When** shown, **Then** the previous-year value and the percentage difference are shown.
5. **Given** an oil tank with a delivery or an unknown interval, **When** monthly values are calculated, **Then** delivered litres are not counted as consumption and unknown intervals leave their days as "no data" for that part of the month.
6. **Given** a month only partly covered by data (for example the current month), **When** shown, **Then** it is marked as partial and its value is the consumption so far, not extrapolated.

---

### User Story 3 - Oil range at the current pace (Priority: P2)

For the oil tank the dashboard shows the range in days not only at the yearly rate but also at the 30-day rate, so the operator sees how long the oil lasts if the cold weather continues.

**Why this priority**: The range at a summer-dominated yearly average is far too optimistic when the heating runs.

**Independent Test**: Tank with 600 L, yearly rate 3 L/day (range 200 days), 30-day rate 12 L/day: both ranges are shown, 200 and 50 days.

**Acceptance Scenarios**:

1. **Given** a 30-day rate above zero, **When** the oil tile is shown, **Then** the range at the 30-day rate is shown next to the existing range.
2. **Given** no 30-day rate or a rate of zero, **When** shown, **Then** only the existing range is shown.
3. **Given** both ranges, **When** shown, **Then** each is labelled with its basis so they cannot be confused.
4. **Given** a property with two oil tanks (300 L and 500 L, together 4 L/day), **When** the oil tile is shown, **Then** the range is 800 / 4 = 200 days for the group, not the range of one tank, and each tank's own level and rate remain visible.
5. **Given** one tank of the group has no rate yet (too little data), **When** the range is calculated, **Then** the group range is not shown (a partial sum would give a wrong, too long range) and the reason is indicated.

---

### User Story 4 - Year totals and costs (Priority: P3)

The operator sees, per meter type and property, the consumption of the current calendar year so far against the same period of last year, and the cost where a price is known.

**Why this priority**: Useful for the annual bill and budgeting, but the monthly view already carries most of the insight.

**Independent Test**: Property with oil deliveries that have prices and water readings with a price entry: year-to-date consumption and cost are shown, with last year's value for the same period.

**Acceptance Scenarios**:

1. **Given** readings in the current and in the previous year, **When** the year view is shown, **Then** year-to-date consumption and the same period of the previous year are shown with the percentage difference.
2. **Given** an oil property with delivered litres and prices, **When** cost is shown, **Then** it is based on the recorded deliveries of the period (litres x price); without prices no cost is shown.
3. **Given** a meter type with a recorded price per unit, **When** cost is shown, **Then** it is consumption x the price valid for the period; without a price no cost is shown.
4. **Given** missing price data, **When** shown, **Then** consumption is still shown and the cost is omitted (not 0 €).

---

### User Story 5 - Unusual consumption marker (Priority: P3)

For water and electricity, a 30-day rate far above the yearly average (a threshold well above the normal seasonal swing) is marked on the meter card as unusual, as a hint for a leak or a device left running.

**Why this priority**: Cheap to add once story 1 exists, but depends on a sensible threshold and is a hint only.

**Independent Test**: Water meter with 0.4 m³/day average and 1.5 m³/day over the last 30 days: the card shows the unusual marker.

**Acceptance Scenarios**:

1. **Given** a water or electricity meter whose 30-day rate is at least double the yearly average, **When** the dashboard is shown, **Then** the card carries a visible "unusually high" marker.
2. **Given** an oil tank, **When** the 30-day rate is high, **Then** no unusual marker is shown (higher oil consumption in cold weather is expected), only the trend of story 1.
3. **Given** less than 60 days of data, **When** shown, **Then** no marker is shown (not enough basis).

---

### User Story 6 - Statistics per property and across all properties (Priority: P2)

With more than one property, the operator sees every statistic in two scopes: **per property** (each house on its own) and **overall** (all properties the user may access, combined). Overall means the sum of consumption per meter type (for example all oil of all houses) and, additionally, the mean per property so the operator can compare a house with the typical house.

**Why this priority**: A second property exists now. Without this, the numbers of both houses are never put side by side and no household total exists.

**Independent Test**: Two properties, each with a water meter (0.2 and 0.6 m³/day): per property the dashboard shows 0.2 and 0.6, overall shows 0.8 m³/day total and 0.4 m³/day mean per property, each with its own 30-day rate and trend.

**Acceptance Scenarios**:

1. **Given** two or more accessible properties with meters of the same type, **When** the dashboard is opened, **Then** an overall block per meter type shows the total daily rate (yearly and 30-day), the trend in percent and the mean per property.
2. **Given** the same data, **When** the dashboard is opened, **Then** each property still shows its own figures by meter type (yearly rate, 30-day rate, trend, consumption of the last 365 days).
3. **Given** a user who may access only some properties, **When** overall figures are calculated, **Then** only the accessible properties are included, and no figure reveals data of another property (including via totals that could be subtracted).
4. **Given** a meter type that exists in only one property, **When** shown, **Then** the overall figure equals that property's figure and the mean-per-property is omitted.
5. **Given** two meters of the same type in one property (for example two oil tanks), **When** property and overall figures are calculated, **Then** their rates are added (the tank pair is one heating system), and the per-meter figures remain visible.
6. **Given** a property whose meters have no usable data, **When** the mean per property is calculated, **Then** that property is left out of the mean (not counted as 0) and the number of properties used is shown.
7. **Given** different units for the same type (not expected), **When** overall figures are calculated, **Then** no total is shown for that type.
8. **Given** the monthly view (story 2) and the year view (story 4), **When** the user selects a property or "all properties", **Then** the same views are shown for the selection.

---

### Edge Cases

- Meter with a single reading, or two readings with identical timestamps: no new figures, no error.
- A meter replaced during the period (replacement date set): only readings of the active meter are used; values before the replacement are not mixed in.
- Water/electricity value that decreases (correction or reset): that interval is skipped, as in the existing yearly average.
- Very long gap (months) between two readings: the interval is spread evenly over its days; monthly values mark months as estimated from a long interval when the interval is longer than 60 days.
- Time zone: month boundaries follow the operator's local calendar (Europe/Berlin), not UTC.
- Yearly average is zero or missing: the percentage trend is not shown (no division by zero), the 30-day rate still is.
- Property without accessible meters for the user: nothing is shown and no data of other properties is exposed.
- One property only: the overall block is not shown (it would repeat the property block).
- A member is removed while viewing: the next request returns no data for that property; no stale figures from a cached view are shown.
- Overall totals when the user has access to one property only: never shown as "overall".
- Existing data and existing figures: the existing yearly average, range and type aggregate keep their values unchanged.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The dashboard MUST show, per meter, the average daily consumption of the last 30 days in addition to the existing yearly average.
- **FR-002**: The dashboard MUST show, per meter, the percentage difference of the 30-day rate to the yearly average, only when both values exist and the average is above zero.
- **FR-003**: The dashboard MUST show, per meter, the daily rate of the most recent reading interval, only when that interval is known.
- **FR-004**: The 30-day rate MUST include every known reading interval that ends inside the last 30 days, counting an interval that crosses the window start as a whole, and MUST show no value when no such interval exists.
- **FR-005**: For oil tanks, all new figures MUST use the same balance rules as the existing rate (recorded deliveries, probable deliveries and dismissed jumps as unknown intervals, noise absorption, never negative).
- **FR-006**: For water and electricity, all new figures MUST count only increasing intervals, like the existing yearly average.
- **FR-007**: Trend differences MUST be marked as increase above +10 %, decrease below -10 %, otherwise neutral.
- **FR-008**: The meter detail page MUST show consumption per calendar month for at least the last 12 months, as chart and table, in the meter's unit.
- **FR-009**: Monthly values MUST split intervals that span month boundaries proportionally by days, so the monthly values of an interval add up to the interval consumption.
- **FR-010**: Months without a covering interval MUST be marked "no data", partially covered months MUST be marked partial; neither is shown as 0 or extrapolated.
- **FR-011**: The monthly view MUST show, where available, the value of the same month of the previous year and the percentage difference.
- **FR-012**: The oil tile MUST show the range at the 30-day rate in addition to the existing range, both labelled with their basis, when the 30-day rate is above zero.
- **FR-013**: The system MUST provide year-to-date consumption per meter type and property, compared with the same period of the previous year.
- **FR-014**: The system MUST show cost for year and month views only where a price is known (oil: recorded delivery prices; other types: recorded price per unit) and MUST omit cost otherwise.
- **FR-015**: Water and electricity meters MUST show an "unusually high" marker when the 30-day rate is at least twice the yearly average and at least 60 days of data exist; oil tanks MUST NOT show this marker.
- **FR-016**: All new figures MUST respect the existing property access rules: a user sees statistics only for properties they may access.
- **FR-017**: Existing figures (yearly average, range, type aggregate, cost forecast) MUST keep their current values and behaviour, with one deliberate exception: the oil range of a property with several tanks (FR-024).
- **FR-024**: All active oil tanks of a property form one *tank group* (one heating supply): the oil range is the sum of the current levels divided by the sum of the tanks' rates, for both the yearly and the 30-day rate. For a property with one tank the value is unchanged. The per-tank figures stay visible. Splitting tanks into several separate groups is out of scope (see Assumptions).
- **FR-018**: All new figures MUST be calculated by one shared calculation per meter type, so dashboard, detail page and range cannot disagree.
- **FR-019**: Month boundaries MUST follow the Europe/Berlin calendar.

- **FR-020**: The dashboard MUST show every new figure of FR-001 to FR-003 per property and by meter type, and, when the user may access two or more properties, overall per meter type: total rate (yearly and 30-day), trend, and mean per property.
- **FR-021**: Overall figures MUST include only properties the user may access, and MUST NOT be shown when only one property is accessible.
- **FR-022**: Properties without usable data MUST be left out of the mean per property, and the number of properties used MUST be shown.
- **FR-023**: Monthly (FR-008) and year (FR-013) views MUST be selectable per property and for all accessible properties.

### Key Entities

- **Consumption interval**: The span between two consecutive readings of one meter, with its days and its consumption (known, or unknown for probable deliveries and dismissed jumps). Already exists for oil from feature 005; the same notion applies to water and electricity.
- **Recent rate**: Average daily consumption over the known intervals ending in a recent window (30 days), derived from consumption intervals; not stored.
- **Monthly consumption**: Consumption of one meter in one calendar month, with a coverage state (complete, partial, no data) and optionally the previous-year value; derived, not stored.
- **Overall figure**: A statistic combined over all properties the current user may access, per meter type; derived per request, never stored, and different per user.
- **Year-to-date summary**: Consumption (and cost where known) of the current calendar year per meter type and property against the same period of the previous year; derived, not stored.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the operator's real oil data from the transition from summer to heating season, the 30-day rate is at least twice the yearly average once the heating consumption is in the readings, and the dashboard shows a clearly marked increase.
- **SC-002**: For any meter, the monthly consumption values of a reading interval sum to that interval's consumption within rounding (one decimal of the meter's unit).
- **SC-003**: The operator can answer "is this month higher than the same month last year, and by how much?" from the meter detail page in one view, without calculation.
- **SC-004**: No new figure is ever shown as 0 or negative because data is missing; missing data always shows as empty or "no data" (verified for each acceptance scenario with missing data).
- **SC-005**: The existing yearly average, range and type aggregates are identical before and after the change for all existing test data.
- **SC-006**: The dashboard summary still loads in the same time as today for a property with 10 meters and 5 years of readings (no noticeable delay for the operator).

- **SC-007**: With two properties, the operator sees per-property and overall figures on one dashboard, and the overall total equals the sum of the per-property figures (verified with a test).
- **SC-008**: No overall or per-property figure ever contains data of a property the user may not access (verified with a test using a user who has access to only one of two properties).

## Assumptions

- The 30-day window and the +/-10 % trend band are fixed defaults; making them configurable is not part of this feature.
- The factor 2 and the 60-day minimum for the "unusually high" marker are defaults chosen to avoid noise from the normal seasonal swing; they can be tuned later without a spec change.
- "Last reading" means the most recent reading of the active meter, regardless of how it was entered (manual or photo).
- Costs reuse prices that already exist in the app (oil deliveries, price entries per unit); no new price input is added here.
- "Overall" means the sum of rates per meter type over the accessible properties; the mean per property is the second view of the same data. Mixing meter types (for example oil litres and water m³) is never summed.
- Oil tanks of a property are always one group (summed consumption and range). Separate groups (two heating systems in one property) or grouping by choice are out of scope; if needed later, a "tank group" assignment on the meter would be a separate feature with its own schema change. All group calculations go through one function `tank_group_key(meter)` (today the property id) so that change stays small.
- A single oil delivery is assigned to the one tank that shows the largest rise (existing rule of feature 005). Delivery into two tanks at once is a known limitation: the second tank's rise shows up as a probable delivery that the operator confirms or splits manually.
- No new notifications are sent; the feature is display-only.
- Statistics use the readings that count. Today that is every reading; feature `016-roles-and-release` may mark readings as pending or rejected, and from then on only released readings count. To keep this a one-place change, all new figures take their readings from one shared selection (the same one the existing rate uses).
- All figures are derived on request from existing readings, deliveries and prices; nothing new is stored, so no data migration is needed (to be confirmed in planning).
- The meter detail page and the dashboard are the only places that change; existing chart and table components are reused where possible.
