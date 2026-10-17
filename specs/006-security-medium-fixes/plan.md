# Implementation Plan: Security Review Follow-up (medium findings)

**Branch**: `006-security-medium-fixes` | **Spec**: [spec.md](spec.md)

## Summary
Six independent, small fixes from `security/REPORT.md` (F-004, F-005, F-006, F-009, F-010, F-011), each test-first. File ownership is split into three work packages so they can be implemented in parallel:

| Package | Findings | Owns |
|---|---|---|
| A uploads | F-004, F-011 | `backend/app/routers/{readings,uploads,ocr}.py`, `backend/app/schemas/readings.py`, `backend/app/services/{ocr_pipeline,llm_client}.py`, `backend/app/models.py` (upload table), migration `0006`, `ocr-service/`, tests for these |
| B roles | F-005, F-006 | `backend/app/routers/{properties,admin}.py`, `backend/app/permissions.py`, `backend/app/schemas/{properties,admin}.py`, tests |
| C secrets+logging | F-009, F-010 | `backend/app/config.py`, `backend/app/main.py` (access-log redaction), `backend/app/auth.py`, `backend/app/routers/auth.py` (events, superadmin role restore), `backend/app/security_log.py`, `docker-compose.yaml`, `README.md`, `chart/values.yaml` docs, tests |

Packages A and B call `app.security_log.log_event(...)` (already present) for role/membership/delete/denied events; package C owns auth-related events.

## Constitution Check
I tests first PASS; III secure-by-default (F-009) PASS; IV reversible migration (0006) PASS; V Compose/Helm parity (F-009 variables, F-011 limits) PASS; VII OWASP: A01 (F-004/5/6), A02+A07 (F-009), A09 (F-010), A05/A10 (F-011) PASS; IX server-side validation PASS.

## Decisions
- F-004: new table `upload_files(id, filename UNIQUE, property_id nullable, uploaded_by_id, created_at)` written by scan/bulk-scan/upload endpoints; new/changed `image_path` must match `^uploads/[0-9a-f-]{36}\.(jpg|jpeg|png|webp|gif|bmp)$` and exist in `upload_files` with the same property (or property NULL and uploaded by the caller). Legacy readings keep working: serving falls back to "any referencing reading the user can read". Delete unlinks only when no other reading references the name.
- F-009 rules: JWT >= 32 chars and not in a deny-list; superadmin password >= 12 and not in deny-list; user and password both or neither; error messages never contain values. Compose uses `${VAR:?message}`; README documents `openssl rand -base64 48`.
- F-010: access log filter redacts `code=`/`state=` query values; events: login ok/failed (reason code), sso rejected (reason, no groups), superadmin login ok/failed, 401/403 denials in `get_current_user`/permission helpers, role change, user delete, membership add/remove/change.
- F-011: `MAX_IMAGE_PIXELS` default 40 000 000 and max side 12 000 (settings `MAX_IMAGE_PIXELS`, `MAX_IMAGE_SIDE`), checked from the header (`PIL.Image.open` lazily) before writing to disk; OCR service same check and caps the upscaled size at 4000 px side.
