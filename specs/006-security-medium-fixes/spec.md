# Feature Specification: Security Review Follow-up (medium findings)

**Feature Branch**: `006-security-medium-fixes` (called "004b")

**Created**: 2026-10-17

**Status**: Draft

**Input**: "mach feature 04b auch" - fix the medium findings of the security review (feature 004) that are small and well understood. Source: `security/REPORT.md`.

## Background

Feature 004 fixed the three high findings. Thirteen medium findings remain, listed with a recommendation. This follow-up fixes the six that are small and protect tenant isolation, privileges, secrets and availability: F-004, F-005, F-006, F-009, F-010, F-011. Larger findings (token revocation F-007, token in URL F-008, bulk-scan memory F-012, NetworkPolicy default F-016, enum drift F-014, Compose hardening F-015) stay separate.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Uploaded images stay inside their property (F-004, P1)

A user can only attach, read, OCR and delete images that belong to their own property. Knowing another property's file name gives no access.

**Independent Test**: With two properties and a known foreign file name: attaching it to an own reading is rejected, reading it via an own reading is refused, hint-rescan with it is refused, deleting an own reading never removes a file still referenced elsewhere.

**Acceptance Scenarios**:
1. **Given** an upload of property B, **When** a writer of property A creates a reading with that `image_path`, **Then** 4xx and nothing is stored.
2. **Given** a stored image referenced by readings of one property, **When** a user of another property requests it, **Then** 404.
3. **Given** `hint-rescan` with an image path not belonging to the meter's property, **Then** 4xx and no call to the AI gateway.
4. **Given** a file still referenced by another reading, **When** one reading is deleted, **Then** the file stays.
5. **Given** a path that is not of the form `uploads/<uuid>.<ext>` (traversal, absolute, other extension), **Then** 422.

### User Story 2 - Property roles cannot escalate (F-005, P1)

Only a property administrator (or global admin) can add, remove and change members and edit the property. A manager keeps read and write access to meters and readings.

**Acceptance Scenarios**:
1. A manager adding a member, changing a role (including their own) or editing the property gets 403.
2. The last property admin cannot be removed or demoted.
3. Property admin and global admin can do all of it as before.

### User Story 3 - The break-glass account stays intact (F-006, P1)

Nobody but the superadmin can change or delete the superadmin account; a normal admin cannot demote it. The superadmin role is re-asserted on each superadmin login.

**Acceptance Scenarios**:
1. Admin changing or deleting the superadmin: 403.
2. The last superadmin cannot be demoted or deleted, not even by itself.
3. After a database-side role change, `superadmin-login` restores the role.

### User Story 4 - Weak secrets are refused at startup (F-009, P1)

The application refuses to start with a JWT secret shorter than 32 characters or on a deny-list, with a superadmin password shorter than 12 characters or on a deny-list, or with only one of superadmin user/password set. Shipped Compose and README no longer contain working weak values.

**Acceptance Scenarios**:
1. Each weak case fails settings validation with a clear message that never prints the value.
2. A strong configuration starts. Existing cluster secrets must satisfy the rules before release (checked, see plan).
3. `docker compose up` without the required variables fails with a clear message instead of using a known secret.

### User Story 5 - Security events are logged, secrets are not (F-010, P2)

Failed and successful logins, rejected SSO logins (reason, no group names), superadmin login outcome, denied access decisions, role changes, user deletion and membership changes are logged as structured events with actor id, target id and source IP. The callback `code` and `state` never appear in access logs.

**Acceptance Scenarios**:
1. Each event listed above produces exactly one log line with fields event, outcome, actor, target, ip.
2. Logs never contain tokens, codes, passwords, e-mail addresses or group names.
3. The access log line for the OIDC callback has its query string removed.

### User Story 6 - Huge images cannot exhaust memory (F-011, P2)

Images larger than a pixel limit are rejected before being stored or decoded fully; decompression bombs and huge dimensions give 413/422. The OCR service enforces the same limit and caps its upscale.

**Acceptance Scenarios**:
1. PNG 20000x20000, GIF 65535x65535, BMP 40000x40000 headers: rejected, no file stored.
2. A normal photo still works; limit configurable (default 40 megapixels, side 12000).
3. `already_cropped=true` cannot cause an upscale beyond a fixed output size.

### Edge Cases
- Legacy readings with `image_path` values from before this change must keep working (reading/serving existing files); validation applies to new/changed values.
- Cluster secrets that are weaker than the new rules must be rotated before the release (never start-fail production).
- The seed superadmin in the experiment rig must satisfy the new password rule.

## Requirements *(mandatory)*

- **FR-001** (F-004): New or changed `image_path`/`serial_image_path` MUST match `uploads/<uuid>.<ext>`, refer to an existing file recorded as uploaded for the same property (or unattached and uploaded by the caller), and otherwise be rejected.
- **FR-002** (F-004): Serving, hint-rescan and delete MUST consider all readings referencing a file; serving requires access to a referencing reading's property, deletion never removes a file that other readings reference.
- **FR-003** (F-005): Membership changes and property edit require property-admin or global admin; last property admin protected.
- **FR-004** (F-006): Only a superadmin may change/delete a superadmin; last superadmin protected; superadmin login restores the role.
- **FR-005** (F-009): Startup validation as in story 4; messages never echo secret values; Compose uses required variables.
- **FR-006** (F-010): Structured security-event logger; access log redacts `code`/`state`; no secrets/PII/group names in any event.
- **FR-007** (F-011): Pixel/dimension limit checked from the image header before storing; OCR service same limit and capped upscale.
- **FR-008**: Every fix has a regression test that fails before and passes after (project principle I); the security rig experiments for these findings pass.
- **FR-009**: Schema changes by reversible migration; Compose and Helm in parity.

## Success Criteria
- **SC-001**: Experiments E-A01-002/003/005, E-UP-001, E-A02-001, E-A09-001 for these findings pass in the security rig.
- **SC-002**: No regression: full backend test suite green.
- **SC-003**: Production starts with the new validation on the first rollout (secrets verified beforehand).

## Assumptions
- The upload ownership record is a small new table (file name, property, uploader, time); older files without a record stay accessible through the readings that reference them.
- OIDC role handling and token revocation are out of scope (F-007).
