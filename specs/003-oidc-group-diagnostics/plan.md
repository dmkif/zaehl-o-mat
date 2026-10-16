# Implementation Plan: OIDC Group Diagnostics in the Operations Log

**Branch**: `003-oidc-group-diagnostics` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-oidc-group-diagnostics/spec.md`

## Summary

Rejected SSO sign-ins get an always-on security event line (reason + sign-in name, never groups). A new switch `OIDC_GROUP_DIAGNOSTICS` (default off) adds one diagnostic line with the delivered and the expected group names; a distinct line covers a missing/empty/unusable group claim; with the switch on, an accepted sign-in logs role and group count. Names are JSON-encoded, truncated and bounded so they cannot inject log lines. Sign-in behavior is unchanged. All logic lives in a new pure module `app/services/oidc_audit.py`, called from `get_or_create_user_from_oidc`. Config: one boolean setting; Compose + Helm (via the existing `backend.env` map) + README table. Release as patch v0.4.1; the cluster release (k3s-gitops) only bumps the chart version and leaves the switch off.

## Technical Context

**Language/Version**: Python 3.12 (FastAPI, pydantic-settings, stdlib `logging`/`json`/`hashlib`)

**Primary Dependencies**: none new

**Storage**: N/A (log output only)

**Testing**: pytest with `caplog` (`backend/tests/test_oidc_audit.py`, extended `test_auth.py`); CI: bandit, gitleaks, Trivy, integration tests

**Target Platform**: container on k3s (apps cluster) and Docker Compose

**Project Type**: web application (backend change only; chart documentation/values comment)

**Performance Goals**: negligible (a handful of string operations per rejected/accepted sign-in)

**Constraints**: one log line per event; no tokens/codes/secrets/e-mail in any line; group names only with the switch on; log injection impossible; behavior of sign-in unchanged

**Scale/Scope**: household app, single-digit sign-ins per day

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I Test-Backed Changes | New pytest tests for every scenario (rejected, no claim, accepted, switch off/on, injection, secrets-free, config parsing); tests written before the code. PASS |
| II Graceful Degradation | Diagnostic is optional and off by default; failure to log must never break sign-in (module catches nothing silently but is pure string formatting). PASS |
| III Secure-by-Default | No secrets/tokens logged; group names gated behind a deliberate switch with a start-up warning. PASS |
| V Deployment Parity | `OIDC_GROUP_DIAGNOSTICS` added to docker-compose.yaml (commented/default), `chart/values.yaml` (documented under `backend.env`), README table — same change. PASS |
| VI Brownfield Respect | Pure logic in `app/services/`, thin call in `app/auth.py`; no schema change. PASS |
| VII A09 Logging | Directly serves A09: login failures always logged; no secrets/tokens in logs. PASS |
| VII A05 Injection (log forging) | Values JSON-encoded, control characters escaped, length/number bounded. PASS |
| X Verifiable Security | Negative tests (malformed claim, hostile group name, secrets absent). PASS |

Result before and after Phase 1: no violations, no complexity tracking needed.

## Project Structure

### Documentation (this feature)

```text
specs/003-oidc-group-diagnostics/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── log-events.md
├── checklists/requirements.md
└── tasks.md              # /speckit-tasks
```

### Source Code (repository root)

```text
backend/app/
├── config.py                    # + oidc_group_diagnostics: bool = False
├── auth.py                      # get_or_create_user_from_oidc: call the audit helper
├── main.py                      # lifespan: start-up warning when the switch is on
└── services/
    └── oidc_audit.py            # NEW: sanitising, user label, event lines
backend/tests/
├── test_oidc_audit.py           # NEW
└── test_auth.py                 # existing OIDC tests must stay green (behavior unchanged)
chart/values.yaml                # documented OIDC_GROUP_DIAGNOSTICS example under backend.env
docker-compose.yaml              # OIDC_GROUP_DIAGNOSTICS (default off)
README.md                        # environment-variable table + short section
```

**Structure Decision**: Existing backend layout; the logic is a pure service module, the HTTP/auth code only calls it. No chart template change: `backend.env` already maps arbitrary variables into the container, so the "chart value" is `backend.env.OIDC_GROUP_DIAGNOSTICS`, documented in `values.yaml`.

## Complexity Tracking

No violations.
