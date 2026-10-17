# Rerun 2 (profile `--quick`, after the feature-006 fixes F-004, F-005, F-006, F-009, F-010, F-011)

Command: `scripts/security-rig.sh --quick` (RIG_RACE_ROUNDS=20, RIG_RACE_SIGNINS=30, limiter off rig-wide). No `backend/` file was changed by the rerun work, only `security/` (tests and rig).

| | Rerun-1 (before any fix) | First run after the fixes | Rerun-2 (final) |
|---|---|---|---|
| Tests | 246 | 246 | 247 (one new experiment) |
| Passed | 151 | 202 | **206** |
| Failed | 94 | 43 | **40** (39 stable + E-A10-001, intermittent) |
| Skipped | 1 | 1 | 1 (A02 needs `SEC_RIG_APP_DEBUG=false`) |
| Runtime (pytest) | 7 min 06 s | 7 min 25 s | **7 min 44 s** |
| TEST/RIG-BUG failures | 0 | 3 (+3 found on review) | **0** |

Per file (pass / fail): A01 38/2, A02 34/5 (+1 skip), A04 52/1, A05 6/4, A06 5/5, A07 42/0, A08 13/10, A09 1/0, A10 3/10 (counting E-A10-001), uploads 12/3.

## Test and rig defects found and fixed (all caused by the stricter rules)

1. **E-A01-002 `image_filename_guessing_and_reference_attack`**: used `uploads/victim-<uuid>.png` and an unregistered file. Now the file is `<uuid>.png` and registered for prop-b in `upload_files` (new helpers `register_upload_row` / `unregister_upload_row` in `sec_common.py`, plain SQL with the rig `DATABASE_URL`). All strict assertions kept (owner reads, unrelated tenant refused, reference either refused or not readable, file survives deletion of the attacker's reading).
2. **E-A01-003 `role_matrix_property_levels`**: expected manager may PATCH the property. F-005 rule: property PATCH and membership changes need property admin or global admin; the matrix has a new level `patch` (admin only) and now also probes `POST/DELETE /properties/<id>/users` with an unknown user (403 when not permitted, 404 when permitted). Consistent with `backend/tests/test_role_escalation.py`. `property_manager_cannot_grant_or_escalate_roles` unchanged and passing.
3. **E-A07-001 `limit_not_bypassable_by_forwarded_for`**: two rig causes. (a) the rig peer is loopback, which is in the default `TRUSTED_PROXIES`: `up.sh` now sets `TRUSTED_PROXIES=10.255.255.0/24` (written to `backend.env`, override with `SEC_RIG_TRUSTED_PROXIES`). (b) the rig started uvicorn with proxy headers on, so uvicorn rewrote `request.client` from X-Forwarded-For before the app saw it; the production image runs `--no-proxy-headers`. `up.sh` and the `restart_backend` fixture now start uvicorn with `--no-proxy-headers`. Documented in `rig/CONTRACT.md`. New experiment `E-A07-001 trusted_proxy_rightmost_untrusted_entry_is_the_key`: restart with `TRUSTED_PROXIES=127.0.0.0/8`, 20 guesses with a different spoofed leftmost entry and one fixed real client on the right (at most 5 evaluated, at least 15 answered 429), a second real client on the right gets its own bucket (401, not 429), garbage rightmost entry never ends in 5xx.
4. **E-A05-002 `stored_xss_roundtrip_api_returns_raw_json`** (found on review): put the markup strings into `image_path`; F-004 now correctly refuses that (422). The reading is created with the markup in `note` (still verbatim in JSON), and a second request with the markup as `image_path` must be refused with 4xx (never 5xx, never stored).
5. **E-A06-002 `parallel_same_image_hash` x2** (found on review): wrote `race-<hex>.png` files, which F-004 rejects with 422 before the race starts. `_write_image` now writes `<uuid>.png` and registers the file for the property. The experiment is back to testing the race and still fails for the right reason (F-022).

## Remaining failures: all REAL (open findings, none touched by feature 006)

| IDs | Finding | Verdict |
|---|---|---|
| A01_005 meter patch with foreign `replaced_by_id` | F-037 | REAL |
| A01_006 `integration_config` meter create answers 500 (`unit` `kWh` on a migration-built DB, SSRF half not reached) | F-014 (enum drift), F-023 | REAL |
| A02_002 docs x2, health detail, authenticated error bodies (500s), X-Request-ID reflection | F-031, F-023, F-046 | REAL |
| A04_001 unusual headers (`zip: DEF` accepted) | F-034 | REAL (residual of F-034; the other header, `exp` and `sub` variants pass now) |
| A05_001 json fields (NUL, `1e999`, lone surrogate), query/path (callback `state` NUL, offset overflow), multipart (hint-rescan NUL), enum members (`kWh`, `smart_meter`) | F-023, F-014 | REAL |
| A06_002 same image hash x2 (2 to 4 x 201 for one hash), identical reading (20 identical rows) | F-022 (identical rows: design decision, no uniqueness rule) | REAL |
| A06_003 privilege kept after IdP removal | F-007 | REAL |
| A06_004 state not bound to browser | F-017 | REAL |
| A08_001 gateway huge, OCR huge/nested/types/reading==serial/garbage, hostile output never stored, orphan file x2, bulk stream | F-029, F-028 | REAL |
| A10_001 postgres stopped mid-request (intermittent: partial write when a request hits the window) | F-035 | REAL, intermittent |
| A10_002 IdP slow 500, cold-cache login 500, orphan file x4, OCR garbage 500, unconfigured subsystems orphan x2 | F-026, F-028, F-029 | REAL |
| UP_001 truncated/broken images stored, EXIF GPS kept, EXIF date free text | F-032, F-033, F-029 | REAL |

## Newly passing

* Fixed findings: E-A01-002 (all 10), E-A01-003 (all 22), E-A02-001 (all 24), E-A09-001, E-UP-001 `decompression_bomb_and_huge_dimensions` and `reading_cannot_adopt_foreign_upload`.
* Side effect, F-036 (unattached scan readable by other users): `unattached_scan_upload_not_readable_by_other_user` passes because of the upload registry. F-036 stays `proposed` until the operator closes it.
* Side effect, F-001 to F-003 regression experiments (E-A07-003 all 36, E-A07-001 all 3, E-A06-001) pass.

## Observations

* No new application bug found. The 500s that remain (NUL bytes to the database, numeric overflow, enum labels on a migration-built DB, hint-rescan NUL path) are the already recorded F-023 / F-014 class; F-004 did not add any.
* E-A10-001 failed in this final run and passed in the previous run of identical code (known timing dependency, F-035).
* The mutant patches in `security/mutants/` were not re-run; `access-check-removed` and others touch files that changed in feature 006 and may need re-basing before the next mutant run.
