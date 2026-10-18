# Feature Specification: UI fixes from the browser tests, per-property role in the UI

**Feature Branch**: `014-ui-fixes`

**Created**: 2026-10-18

**Status**: Implemented

**Input**: the four observations of the browser suite (feature 013, `e2e/README.md`): oil edit form shows `1500.00`, "empty" text next to an error, add-reading form for read-only users, silent "Weiter" in the scan dialog. Plus: the SPA decided write buttons by the global role, the server decides by the property role.

## User Scenarios & Testing

### User Story 1 - Buttons follow the role in this property (P1)

A user sees write and delete controls exactly where the server would accept them: a property `user` sees no add-reading form and no edit/delete buttons; a `manager` may add and edit; only the property `admin` (or a global admin) deletes. A global *manager* who is only a property *user* is read-only there; a global *user* who is property *admin* may write and delete there.

**Acceptance Scenarios**:
1. `GET /api/properties` and `GET /api/properties/{id}` return `my_role` (`admin|manager|user`): the caller's property role; `admin` for global admin and superadmin.
2. Add-reading form, reading edit, oil delivery create/edit, meter create/edit/bulk import need `my_role` admin or manager; reading delete, oil delivery delete and meter delete need `admin`; property edit needs `admin`; create/delete property stays global admin.
3. The probable-delivery buttons on the dashboard need write on that property.
4. Unknown or failed role lookup = read-only UI. The server stays the authority (no API change in what is allowed).

### User Story 2 - Oil delivery edit form (P2)
Litres and prices show as `1500`, `87.5`, `1.1` (no `.00`/trailing zeros), `.` separator; comma and point input still parse.

### User Story 3 - Foreign or unknown property (P2)
The oil deliveries page shows the error only, not "Noch keine Lieferungen erfasst.".

### User Story 4 - Scan dialog "Weiter" (P2)
Crop step and serial crop step: the button that reads the crop is disabled with a spinner and a short hint until the cropper decoded the image and has a crop box (`ready`); a failed image load shows an error. No silent no-op.

## Requirements
- **FR-001** `PropertyResponse.my_role` as above, computed server side from `PropertyUser` / global role.
- **FR-002** SPA derives `canWrite`/`canDelete` from `my_role` (`utils/propertyRole.ts`) and uses them instead of `isAdmin`/`isManager` for property-scoped controls.
- **FR-003** `formatInputNumber` for the edit form; **FR-004** error page without empty-state text; **FR-005** cropper `ready` gate.
- **FR-006** Tests first; the 25 existing e2e tests keep passing.

## Out of scope
Membership management UI (no such screen; stays property-admin only on the API, F-005); API permission changes.
