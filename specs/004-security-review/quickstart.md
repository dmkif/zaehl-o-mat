# Quickstart: Run the Security Rig

Prerequisites: `podman`, project venv (`backend/.venv`), network access once to pull `postgres:18-alpine` (and scanner images for the tooling step).

```bash
scripts/security-rig.sh                 # rig up → experiments (pytest -m security) → rig down
scripts/security-rig.sh --keep          # leave the rig running for manual probing
scripts/security-rig.sh --mutant no-property-check   # run with a weakened variant (matching experiment must fail)
scripts/security-rig.sh --only E-A06    # subset by experiment id prefix
```

Expected: a result table per experiment; races show repetitions and failure rate; all green on the fixed code, and the chosen experiment red with a mutant.

Static tooling (separate step): `scripts/security-scan.sh` runs bandit, pip-audit, npm audit, Trivy, gitleaks via podman and writes a summary under `security/tooling/` (no secrets).

Report: `security/REPORT.md`; checklist: `security/CHECKLIST.md`.
