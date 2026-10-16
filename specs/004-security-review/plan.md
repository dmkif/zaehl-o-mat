# Implementation Plan: Full Security Review of the Application

**Branch**: `004-security-review` | **Date**: 2026-10-08 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/004-security-review/spec.md`

## Summary

A repeatable security review in three layers: (1) a review checklist per OWASP Top 10 (2025) category and component, answered by reading the code and by automated analysis (bandit, dependency and image scanning, secret scanning, eslint-security, hadolint/kubeconform-style checks of chart and Compose); (2) an experiment suite (`security/` with pytest) that runs the real backend with a real PostgreSQL in containers, against stand-ins for the identity provider, the AI gateway and the OCR service, and attacks it (races, access control, input, forgery, fault injection); (3) a findings report `security/REPORT.md` with a coverage matrix. Critical/high findings are fixed in this feature with regression tests in the normal suite; the rest is tracked. One command (`scripts/security-rig.sh`) builds the rig and runs the suite.

## Technical Context

**Language/Version**: Python 3.12 (backend, experiments), TypeScript/Vue (frontend review), Bash (rig script)

**Primary Dependencies**: existing stack plus review tooling used from the dev environment only (pytest, httpx, bandit, pip-audit; trivy/gitleaks/hadolint as containers via podman); no new runtime dependency

**Storage**: PostgreSQL 18 (container) for the rig, synthetic data only

**Testing**: pytest marker `security` (experiments, opt-in, not in the default CI run); regression tests for fixes in the normal `backend/tests/` suite

**Target Platform**: developer machine with podman (no Docker), Linux

**Project Type**: web application review (backend, frontend, optional OCR service, chart, Compose, CI)

**Performance Goals**: full experiment run under 30 minutes (SC-005); race experiments ≥ 100 repetitions

**Constraints**: never touches production (separate database container, loopback ports, synthetic data); no real secrets in the repo, logs or report; `podman-compose` is unreliable on this host, so the rig uses plain `podman run` and `uvicorn`

**Scale/Scope**: ~35 HTTP endpoints in 8 routers, ~18k lines in the repository (most of it tests and tooling), 1 frontend, 1 optional service

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Assessment |
|---|---|
| I Test-Backed Changes | Every fix gets a regression test that fails before the fix; experiments are tests. PASS |
| II Graceful Degradation | Experiments verify optional subsystems (OCR, gateway) unconfigured and failing. PASS |
| III Secure-by-Default Secrets | Rig uses generated throw-away secrets; nothing real committed; gitleaks runs. PASS |
| V Deployment Parity | Fixes touching configuration are applied to Compose and Helm in the same change. PASS (checked per fix) |
| VI Brownfield Respect | Fixes follow the router/service split; the rig lives in `security/` and `scripts/`, not in `app/`. PASS |
| VII OWASP Top 10 (highest priority) | This feature *is* the verification; the plan maps A01–A10 to concrete checks (see research R2). PASS |
| X Verifiable Security (CI gates) | New experiments are opt-in locally; the regression tests join the CI suite; scanners stay green. PASS |

Result before and after Phase 1: no violations; complexity tracking not needed.

## Project Structure

### Documentation (this feature)

```text
specs/004-security-review/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── experiment-catalogue.md   # experiment IDs, categories, expected results
│   └── report-format.md          # finding and coverage-matrix format
├── checklists/requirements.md
└── tasks.md                      # /speckit-tasks
```

### Source Code (repository root)

```text
security/
├── README.md                    # how to run, what is in scope
├── REPORT.md                    # findings + coverage matrix (deliverable)
├── CHECKLIST.md                 # A01–A10 × component review checklist (answered)
├── rig/
│   ├── up.sh / down.sh          # podman: postgres, uvicorn, stand-ins
│   ├── standins/                # fake_oidc.py, fake_gateway.py, fake_ocr.py
│   └── seed.py                  # synthetic users, properties, meters, readings
├── experiments/
│   ├── conftest.py              # rig fixtures, client factory, repeat helper
│   ├── test_a01_access_control.py
│   ├── test_a02_misconfiguration.py
│   ├── test_a04_crypto_tokens.py
│   ├── test_a05_injection.py
│   ├── test_a06_races.py        # concurrency (insecure design / logic)
│   ├── test_a07_authentication.py
│   ├── test_a08_integrity.py    # LLM/OCR response validation
│   ├── test_a09_logging.py
│   ├── test_a10_exceptional.py  # fault injection, fail closed
│   └── test_uploads.py          # type/size/path/bomb
├── mutants/                     # deliberately weakened variants (patches) for SC-006
scripts/security-rig.sh          # one command: rig up → experiments → rig down
backend/tests/                   # regression tests for fixed findings
```

**Structure Decision**: A top-level `security/` directory keeps review artefacts and the rig out of the application package. Experiments are pytest tests selected by marker; the weakened variants are small patch files applied to a temporary copy, never committed into `backend/app`.

## Complexity Tracking

No violations.
