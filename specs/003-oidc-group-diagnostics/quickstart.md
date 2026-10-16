# Quickstart: Validation

Prerequisites: backend dev environment (`cd backend && source .venv/bin/activate`).

## 1. Unit/integration tests

```bash
cd backend && pytest tests/test_oidc_audit.py tests/test_auth.py -q
```

Expected: green; covers rejected (switch on/off), missing/empty/unusable claim, accepted, hostile group name (one line), no secrets/e-mail, invalid switch value.

## 2. Local run with the switch

```bash
OIDC_GROUP_DIAGNOSTICS=true uvicorn app.main:app   # start-up shows the WARNING
```

Reject a sign-in (user in a differently named group): log shows `oidc.signin_rejected` and one `oidc.group_diagnostic` with delivered and expected names. Unset the variable: only `oidc.signin_rejected`.

## 3. Cluster (after the v0.4.1 rollout)

```bash
kubectl --context apps -n zaehl-o-mat logs deploy/zaehl-o-mat-backend | grep "oidc\."
```

Switch off (default): no `group_diagnostic` lines. To investigate: set `backend.env.OIDC_GROUP_DIAGNOSTICS: "true"` in the release patch, wait for the pod restart, reproduce, read the line, then switch off again.
