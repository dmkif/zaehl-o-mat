# Contract: Experiment Catalogue (initial, extended during the review)

Each experiment is a pytest test marked `security`, named `test_<ID>_<slug>`, and listed here with its expected secure result. The report links findings to these IDs.

| ID | Category | Attack | Expected (secure) |
|---|---|---|---|
| E-A01-001 | A01 | every endpoint without token | 401 |
| E-A01-002 | A01 | user B reads/changes/deletes user A's property, meter, reading, upload by id/filename | 403/404, nothing changed |
| E-A01-003 | A01 | role matrix: user/manager/admin on admin endpoints | only permitted roles succeed |
| E-A01-004 | A01 | path traversal / absolute path on file serving | 400/404, no file outside upload dir |
| E-A01-005 | A01 | PATCH with extra fields (role, owner, ids) | ignored or rejected |
| E-A01-006 | A01 | configured outbound URLs pointing to loopback/metadata addresses | rejected or never user-controlled |
| E-A02-001 | A02 | start with default JWT secret / default DB URL | refuses to start |
| E-A02-002 | A02 | response headers, `/docs`, error bodies | no debug info, expected headers via ingress config documented |
| E-A04-001 | A04 | tokens: `alg=none`, wrong key, expired, tampered, algorithm confusion | 401 |
| E-A05-001 | A05 | SQL/NoSQL/header/CRLF/filename payloads in every field | no 5xx, no data leak, stored values inert |
| E-A05-002 | A05 | stored XSS round trip (names, notes) | encoded on render (frontend review + API returns raw text only) |
| E-A06-001 | A06 | 100 parallel first sign-ins of one identity | exactly one user, no 5xx |
| E-A06-002 | A06 | parallel creation of the same reading / same image hash | one stored or defined conflict response, no duplicates |
| E-A06-003 | A06 | concurrent role change and sign-in of the same user | consistent final state |
| E-A06-004 | A06 | OIDC `state` replay / reuse / expired | rejected |
| E-A07-001 | A07 | brute force `superadmin-login` in parallel | limit holds under concurrency |
| E-A07-002 | A07 | PKCE missing/mismatched verifier | rejected |
| E-A07-003 | A07 | group claim shapes: string, `xadminx`, nested, huge list | not privileged; behavior documented |
| E-A08-001 | A08 | hostile gateway/OCR responses (huge, nested, wrong types, reading == serial) | validated, not stored blindly |
| E-A09-001 | A09 | run suite at DEBUG; grep logs | no token, code, secret, e-mail; security events present |
| E-A10-001 | A10 | stop PostgreSQL mid-request | clean 5xx without detail, no partial write |
| E-A10-002 | A10 | identity provider / gateway / OCR down or slow | fail closed, bounded wait |
| E-UP-001 | A01/A05 | uploads: wrong type vs magic bytes, polyglot, > limit, decompression bomb, EXIF abuse | rejected before processing, limits enforced |

Mutants (SC-006): one patch per row group (access check removed, unique/retry removed, `alg=none` accepted, size check removed, gateway output trusted).
