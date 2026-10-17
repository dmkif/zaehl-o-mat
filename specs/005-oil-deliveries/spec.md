# Feature Specification: Oil Deliveries and Correct Consumption Average

**Feature Branch**: `005-oil-deliveries`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User description: "Heizöl-Lieferungen und korrekter Verbrauchsdurchschnitt: Lieferungen erfassen, Verbrauch als Bilanz berechnen, Sprünge nach oben ohne erfasste Lieferung als vermutliche Lieferung erkennen, Messrauschen ignorieren, Kachel/Reichweite/Kostenprognose mit derselben Berechnung, Tests zuerst."

## Background

The dashboard shows a daily average consumption ("∅ L/Tag") and a range in days ("Reichweite") for the oil tank. The tank meter is a *level* reading, so the value normally falls and only rises when oil is delivered (or when a reading is noisy, for example an automatic photo reading that is a few litres off).

Two problems were found:

1. The daily-average tile counts only *rising* values for the tank. On the operator's real data (eight readings from 2026-04-02 to 2026-09-13, 1374 L down to 816 L) the real consumption is about 3.4 L per day, but the tile shows about 10 L per day, because a +12 L reading error between two automatic readings (1353 L to 1365 L within 28 hours) is counted as consumption. The range uses the opposite direction and is therefore consistent with a falling tank, so the two numbers disagree.
2. A delivery (for example 700 L before, 1600 L after) is not recorded anywhere. A table for deliveries exists but is not used. Without knowing about the delivery, the jump is either ignored (and the days of that interval vanish from the calculation) or, with the wrong direction, counted as consumption. Consumption that happened before the delivery inside the same interval is lost.

This feature lets the operator record deliveries, calculates consumption as a balance (level before − level after + oil delivered in between), recognises unrecorded deliveries from large upward jumps, and makes every place that shows or uses the consumption rate use the same calculation.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The daily average is correct (Priority: P1)

The operator sees a believable "L per day" for the oil tank. Small upward deviations caused by reading errors do not inflate the value, and the tile, the range ("Reichweite") and the cost forecast all rest on the same rate.

**Why this priority**: This is the bug the operator noticed and it makes the tile misleading today. It needs no new data entry.

**Independent Test**: Load the eight real readings (1374, 1353, 1365, 1289, 1207, 1074, 945, 816 L between 2026-04-02 and 2026-09-13) and open the dashboard: the tile shows about 3.4 L per day (not about 10), and the range is the current level divided by that same rate.

**Acceptance Scenarios**:

1. **Given** the eight real readings, **When** the dashboard summary is requested, **Then** the average is between 3.2 and 3.6 L per day.
2. **Given** a tank with one reading that is 12 L higher than the previous one a day earlier (reading error) and otherwise falling values, **When** the average is calculated, **Then** the 12 L rise does not count as consumption and does not change the average by more than the reading error spread over the whole period.
3. **Given** the average for the tank, **When** tile, range and cost forecast are computed, **Then** all three use the same rate (the range equals current level divided by the displayed rate, rounded down to whole days).
4. **Given** fewer than two usable readings, **When** the summary is requested, **Then** no rate and no range are shown (empty, not zero).

---

### User Story 2 - Record a delivery and get the right consumption (Priority: P1)

When oil is delivered, the operator records the delivery for the property (date, litres, optionally price and supplier). The consumption for the period around the delivery is then calculated as a balance, so that "700 L, delivery, 1600 L" is understood as 900 L delivered, not as a mistake and not as negative consumption.

**Why this priority**: The operator expects a delivery very soon and asked explicitly how it would affect the average.

**Independent Test**: Readings 700 L on day 0 and 1600 L on day 10 with a recorded delivery of 1000 L on day 5 give consumption of 700 + 1000 − 1600 = 100 L over 10 days (10 L per day for this interval), and the average over a longer period includes this interval.

**Acceptance Scenarios**:

1. **Given** two consecutive readings (700 L, then 1600 L) and a recorded delivery of 1000 L between them, **When** consumption is calculated, **Then** the interval consumption is 100 L over its days and it is part of the average.
2. **Given** a recorded delivery between two readings where the level rose by exactly the delivered amount, **When** consumption is calculated, **Then** the interval consumption is 0 L (not negative).
3. **Given** several deliveries in the observed period, **When** consumption is calculated, **Then** each delivery is applied only to the interval it falls into.
4. **Given** a delivery recorded on the same day as a reading, **When** consumption is calculated, **Then** the delivery is assigned to exactly one interval (the one containing its recorded time) and never counted twice.
5. **Given** the recorded delivery, **When** the operator views the property, **Then** the delivery appears in a list with date, litres, price per litre and total (if known), supplier, and can be corrected or deleted by authorised users.
6. **Given** a delivery with a negative or zero amount, a date in the far future, or unreasonable litres, **When** it is submitted, **Then** it is rejected with a clear message and nothing is stored.

---

### User Story 3 - Unrecorded deliveries are detected, not miscounted (Priority: P1)

If the tank level jumps up by a large amount between two readings and no delivery is recorded, the system treats it as a *probable delivery*: the jump is not counted as consumption, the consumption for that one interval is treated as unknown (its days are left out of the average), and the operator is offered to confirm it as a delivery with the detected litres pre-filled.

**Why this priority**: The operator's own example (700 L suddenly 1600 L). Without it, forgetting to record a delivery silently distorts the average.

**Independent Test**: Readings 700 L then 1600 L with no recorded delivery: the average is unchanged by this interval, and the dashboard shows a notice "Probable delivery of about 900 L between date A and date B" with a confirm action.

**Acceptance Scenarios**:

1. **Given** an upward jump larger than the plausibility threshold and no recorded delivery in between, **When** consumption is calculated, **Then** that interval contributes neither litres nor days to the average.
2. **Given** such a jump, **When** the dashboard is shown, **Then** a notice names the period and the approximate litres and offers to record it as a delivery.
3. **Given** the operator confirms the notice, **When** the delivery is stored, **Then** the notice disappears and the interval is now calculated as a balance with the confirmed litres (or the corrected litres the operator typed).
4. **Given** the operator dismisses the notice as a reading error, **When** it is dismissed, **Then** the jump is not offered again and still does not count as consumption.
5. **Given** a small upward deviation below the threshold (reading error), **When** consumption is calculated, **Then** no notice appears and the deviation is absorbed (see story 4).

---

### User Story 4 - Reading noise does not distort the average (Priority: P2)

Automatic readings from photos can be a few litres off in either direction. The average must be robust against this: noise in one direction followed by noise in the other direction must cancel, and the result must not depend on how often the operator measured.

**Why this priority**: It is what makes the 10 L per day come out wrong in the first place, but once story 1 is done only residual cases remain.

**Independent Test**: The same real consumption measured with many noisy readings and with only two readings gives averages that differ by no more than the reading error spread over the whole period.

**Acceptance Scenarios**:

1. **Given** readings 1000, 995, 1003, 990 L over four days (noise around a real fall of about 3 L/day), **When** the average is calculated, **Then** it is within 10 % of the value from the first and last reading alone.
2. **Given** noise that makes a later reading higher than an earlier one by less than the threshold, **When** the balance is calculated, **Then** the small negative consumption is allowed to offset neighbouring intervals (net balance), so that the total over the period equals first level − last level + deliveries.
3. **Given** a period with only noise and no real consumption, **When** the average is calculated, **Then** the result is close to zero, not a positive made-up value.

---

### User Story 5 - Access and input are controlled (Priority: P2)

Only people with access to the property can see its deliveries, only users with write access can add or change them, and only property administrators (or global administrators) can delete them. Input is validated.

**Why this priority**: Required by the project's security principles; the existing property permission model applies.

**Independent Test**: A user without access to the property gets 403/404 for list, create, change and delete; a read-only user can list but not create; a writer can create and change but not delete.

**Acceptance Scenarios**:

1. **Given** a user with no role in the property, **When** they list, create, update or delete deliveries of that property, **Then** every request is denied and nothing changes.
2. **Given** a read-only user, **When** they try to create a delivery, **Then** it is denied.
3. **Given** a property manager (write level), **When** they create and change a delivery, **Then** it succeeds; deleting is denied.
4. **Given** a property administrator or global admin, **When** they delete a delivery, **Then** it succeeds and the consumption is recalculated without it.
5. **Given** a delivery id that belongs to a different property than the one in the request, **When** it is accessed, **Then** it is treated as not found.

---

### Edge Cases

- Tank exactly refilled to the same level as before (delivery equals consumption): balance still correct; a recorded delivery without any visible rise must still be applied.
- Delivery recorded but no reading after it yet: consumption cannot be calculated for that open interval; the last known rate stays, no error.
- Readings in the same instant or in the wrong order of entry: sort by reading time; zero-length intervals contribute nothing.
- Delivery recorded earlier than the first reading in the period: ignored for the average (outside the observed period), still listed.
- Replacing the tank meter or a changed unit: only readings of the current oil meter of the property are used; previous meters are not mixed.
- More than one oil meter in a property: deliveries belong to the property; they are applied to the oil meter(s) per documented rule (see Assumptions).
- Very long gaps (months) between readings: the interval is used as is; only the jump rule, not the age of the interval, makes it unknown.
- A large *fall* between two readings is real consumption (or a leak) and is shown as such; it is not treated as an error by this feature.
- Existing data: no deliveries exist today; behaviour for tanks without deliveries must be correct on its own (story 1, 3, 4).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST calculate the oil consumption rate in litres per day from the readings of the oil meter as a balance per interval between consecutive readings: level before − level after + litres delivered inside the interval.
- **FR-002**: The balance MUST be allowed to be slightly negative for an interval when caused by reading noise below the plausibility threshold, so that the sum over a period equals first level − last level + delivered litres.
- **FR-003**: An upward jump between two consecutive readings that exceeds the plausibility threshold and has no matching recorded delivery MUST be treated as a probable delivery: it contributes no consumption and no days to the average.
- **FR-004**: The plausibility threshold for a jump MUST be defined in one place, documented, and chosen so that reading errors of a few percent of the tank content are not flagged while a delivery of several hundred litres always is.
- **FR-005**: The dashboard MUST show a notice for each unconfirmed probable delivery (period, approximate litres) with actions to record it as a delivery (pre-filled, editable) or to dismiss it as a reading error.
- **FR-006**: Authorised users MUST be able to create, list, change and delete oil deliveries of a property with date and time, litres, and optionally price per litre or total price and supplier and note.
- **FR-007**: A delivery MUST be assigned to the interval containing its recorded time, exactly once; deliveries outside the observed period MUST NOT influence the rate.
- **FR-008**: The daily-average tile for the oil tank, the range ("Reichweite") and the cost forecast MUST use the same consumption rate, produced by one shared calculation.
- **FR-009**: With fewer than two usable readings, or when all intervals are unknown, the rate and the range MUST be empty, not zero or infinite.
- **FR-010**: Access MUST follow the existing property permissions: read level lists deliveries, write level creates and changes, delete level (property admin and global admin) deletes; requests for other properties or unknown ids MUST be denied without revealing existence.
- **FR-011**: Input MUST be validated: litres greater than zero and below a sane tank limit, price values non-negative with sensible precision, date not in the future beyond a small tolerance, text lengths bounded; invalid input MUST be rejected with a clear message and no partial write.
- **FR-012**: Prices of a delivery MUST be optional; when only litres and one price form (per litre or total) are given, the other MUST be derived and stored consistently.
- **FR-013**: The calculation MUST NOT change for non-oil meters (water, electricity, gas continue to use rising values).
- **FR-014**: Dismissed probable deliveries MUST stay dismissed and MUST keep being excluded from the average.
- **FR-015**: Tests MUST be written before the implementation and MUST fail on the current behaviour for the cases that expose the defect (real-data case, jump without delivery, delivery between readings).
- **FR-016**: Security-relevant input handling and access control of the new endpoints MUST be covered by regression tests, in line with the project's OWASP Top 10 requirement.
- **FR-017**: Existing data MUST remain valid: no data loss, and a migration, if needed for optional prices or dismissed notices, MUST be reversible and run on a database built from the existing migrations.

### Key Entities

- **Oil delivery**: belongs to a property; time of delivery, litres, optional price per litre and total price, optional supplier and note; created by a user.
- **Probable delivery** (derived, plus a dismissal state): a pair of consecutive readings with an upward jump above the threshold and no recorded delivery; carries period, detected litres, state (open / confirmed / dismissed).
- **Consumption interval** (derived): two consecutive readings of the oil meter, the deliveries inside it, the resulting litres consumed and days, and whether it counts (known) or not (unknown).
- **Consumption rate** (derived): total litres of known intervals divided by total days of known intervals, over the last 365 days.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the operator's eight real readings the dashboard shows between 3.2 and 3.6 L per day (today about 10).
- **SC-002**: Tile, range and cost forecast for the oil tank always derive from the same rate; across the test cases the range equals the current level divided by the displayed rate in 100 % of cases.
- **SC-003**: A delivery of any size, recorded or not, changes the displayed average by less than the reading error spread over the period; in the 700 L to 1600 L example the average with the recorded delivery equals the balance result exactly and without it equals the average without that interval.
- **SC-004**: An unrecorded upward jump of more than the threshold shows a notice on the next dashboard load in 100 % of test cases, and confirming it takes at most three user actions.
- **SC-005**: Recording a delivery takes under one minute for the operator, with date, litres and price.
- **SC-006**: No request of a user without access to a property can read or change its deliveries (0 successes across the access test matrix).
- **SC-007**: All new and changed behaviour is covered by automated tests that were written first; the existing dashboard tests still pass unchanged apart from the oil average case that encoded the defect.

## Assumptions

- Deliveries belong to a property (as in the existing data model); if a property has several oil meters, the delivery is applied to each oil meter's interval by time and litres are split by documented rule, and the default (one oil meter per property) behaves as described. Multi-tank splitting beyond this is out of scope.
- The plausibility threshold is expressed relative to the tank size seen in the data (for example a rise of more than 5 % of the highest level in the last year, but at least 50 L); the exact numbers are a planning decision documented in the plan, and must make the real example cases pass.
- "Reading error" magnitude from automatic photo readings is assumed to be a few percent of the tank content or less, based on the observed +12 L case.
- The rate is calculated over the last 365 days of readings, as today.
- Prices of a delivery are optional on input; if the existing storage requires them, the plan decides how (for example allowing empty values by a reversible migration).
- Fixing the rate for non-oil meters is out of scope; their calculation is unchanged.
- Notifications beyond the dashboard notice (e-mail, push) are out of scope.
- The operator (single household) is the primary user; the permission model stays as it is.
