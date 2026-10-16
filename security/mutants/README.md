# Mutants (SC-006)

Each `<name>.patch` is a small, realistic weakening of `backend/app`, relative to the repo root (`a/backend/...`
`b/backend/...`, apply with `patch -p1`). `scripts/security-rig.sh --mutant <name>` applies it to a temp copy of
`backend/` only; the working tree is never touched. A mutant counts as detected when at least one of the listed
experiments fails against it and passes against the unpatched backend.

| Mutant | Change | Must fail (experiment ID: tests) |
|---|---|---|
| `access-check-removed` | `permissions.has_property_access` no longer filters on `PropertyUser.user_id`: any user with a membership anywhere passes every property check | E-A01-002: `cross_tenant_access_by_id_denied`, `image_filename_guessing_and_reference_attack`, `hint_rescan_does_not_process_foreign_images`; E-A01-003: `role_matrix_property_levels`; E-A01-005: `meter_patch_cannot_reference_foreign_property_meter`; E-UP-001: `reading_cannot_adopt_foreign_upload` |
| `no-unique-retry` | `get_or_create_user_from_oidc`: IntegrityError/rollback/re-read handling removed, a concurrent first sign-in surfaces the unique violation as 500 | E-A06-001: `parallel_first_signin_one_user` (also E-A06-003 `role_change_vs_signin` may flake) |
| `alg-none-accepted` | `_jwt = JsonWebToken([alg, "none"])`: unsigned tokens with a valid `exp` verify (checked: `decode_access_token` returns the claims) | E-A04-001: `alg_none_rejected` (all alg spellings), `tampered_token_rejected` |
| `size-check-removed` | the 10 MB limit removed in `/ocr/scan` (file and serial_file) and `/ocr/bulk-scan` | E-UP-001: `size_limit_enforced` |
| `gateway-output-trusted` | `ocr_pipeline._llm_fallback`: reading and serial taken from the model verbatim (no type check, no digit cleaning, no "reading equals serial" guard) | E-A08-001: `gateway_hostile_response_validated` (modes `hostile_huge`, `hostile_nested`, `hostile_types`, `reading_equals_serial`), `hostile_output_never_stored` |
| `group-substring` | no patch: real bug in `get_or_create_user_from_oidc`, `settings.oidc_*_group in groups` is a substring test when the claim is a string | E-A07-003: `group_claim_shapes` (run unpatched; fails while the bug exists) |

Test names are given without the `test_<ID>_` prefix; find them with `grep -n "def test_E_A06_001" security/experiments/*.py`.
Expected detections were derived by reading the experiments; T025 must record the actual result per mutant in
`security/REPORT.md` section 4. Patches were dry-run with `patch -p1` against a copy of `backend/` and the patched
files pass `py_compile`; no rig run was done.
