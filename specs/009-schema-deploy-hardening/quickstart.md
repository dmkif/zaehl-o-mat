# Quickstart / verification: Schema and Deployment Hardening

## F-014 migration 0008 (PostgreSQL, throw-away container)
```bash
scripts/verify-migration-enums.sh backend/.venv/bin/python
```
Starts `postgres:18-trixie` on a random loopback port, removes it on exit. (a) `alembic upgrade 0006` -> probe inserting every unit x type x integration combination fails with `invalid input value for enum` (reproduces F-014) -> `upgrade head` -> all 36 combinations insert -> downgrade/upgrade again works. (b) `Base.metadata.create_all` + `alembic stamp 0006` + `upgrade head`: `pg_enum` label list identical before and after (no-op). Unit guard: `pytest backend/tests/test_migration_enums.py`.
`down_revision` is `0006`; set it to `0007` when the session-hardening migration is merged.

## F-016 chart
```bash
helm lint chart
helm template r chart | kubeconform -strict -ignore-missing-schemas -      # default: no NetworkPolicy, as before
helm template r chart --set ocr.enabled=true                             # fails: ingressFrom required
helm template r chart -f <values with ocr, ingressFrom, egress.enabled>   # 4-5 policies
```
k3s' Traefik: `ingressFrom.namespaceSelector.matchLabels."kubernetes.io/metadata.name"=kube-system`, `podSelector.matchLabels."app.kubernetes.io/name"=traefik`.

## F-015 Compose
`POSTGRES_PASSWORD=x JWT_SECRET_KEY=y SUPERADMIN_PASSWORD=z podman compose config` (valid; fails with a clear message when a variable is missing). Compose could not be started here (no Docker, no GPU): `read_only`/`cap_drop` on backend/ocr/frontend and the healthchecks are untested at runtime; if a service fails to start, remove its `read_only` first.
