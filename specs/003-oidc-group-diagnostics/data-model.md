# Data Model: OIDC Group Diagnostics

No persistent data. One setting and log line shapes.

## Setting

| Name | Env | Type | Default | Rule |
|---|---|---|---|---|
| `oidc_group_diagnostics` | `OIDC_GROUP_DIAGNOSTICS` | bool | `false` | invalid value → start-up error; `true` → start-up WARNING |

## Derived values (per event)

| Value | Source | Rule |
|---|---|---|
| user label | `preferred_username` → `sub:<sha256[:8]>` → `unknown` | sanitised, ≤ 64 chars; never e-mail or raw `sub` |
| delivered groups | claim `settings.oidc_groups_claim` | list of strings; each ≤ 64 chars; max 20 listed; JSON-encoded |
| expected groups | `oidc_admin_group`, `oidc_manager_group`, `oidc_user_group` | configured names |
| group count | length of the usable list | integer |

## Events

| Event | Always on | Contains group names |
|---|---|---|
| `oidc.signin_rejected` | yes | no |
| `oidc.group_diagnostic` (rejected) | only with switch | yes (delivered + expected) |
| `oidc.group_claim_missing` | only with switch | no (claim name only) |
| `oidc.signin_accepted` | only with switch | no (role + count) |
