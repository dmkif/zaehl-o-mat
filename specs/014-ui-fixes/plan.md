# Implementation Plan: UI fixes, per-property role

**Branch**: `014-ui-fixes` | **Spec**: [spec.md](spec.md)

## Summary
Backend: `permissions.property_roles()` + `my_role` in `PropertyResponse`, set in `routers/properties.py` (list, get, create, patch). Frontend: `utils/propertyRole.ts` (`permsFor`), `utils/usePropertyPerms.ts` (loads `GET /properties/{id}`), used by `Meters`, `MeterDetail`, `OilDeliveries`; `Properties` uses `p.my_role`; `Dashboard` builds a role map from `GET /properties`. `OcrCapture` gates both crop steps on the cropper's `ready`/`error` events.

## Constitution Check
I tests first PASS (red evidence in tasks.md); III secure by default PASS (unknown role = read-only, server unchanged); VII OWASP A01 unchanged (UI is display only); IX server-side validation PASS.

## Decisions
- `my_role` is a single field, not a permission matrix: the rules (write = admin|manager, delete = admin) live in one place per side (`permissions.py`, `permsFor`).
- A global admin who also has a lower membership still reports `admin` (server grants full access).
- Property edit/delete buttons: edit for property admin, delete property for global admin only (server: `require_admin`).
- The Cropper component of `vue-advanced-cropper` emits `ready` after decoding and positioning; its `error` event keeps the button disabled and shows `ocr.crop_error`.
- E2E gate test holds back the images' `load` event (init script) to prove the disabled state deterministically.
