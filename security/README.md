# Security review rig (feature 004)

Isolated copy of the application with synthetic data, used to attack the backend
(races, access control, injection, uploads, forgery, fault injection).

**Safety rules**: loopback only, throw-away Postgres container, generated secrets,
synthetic data. Never point it at a cluster, a production database or real images.
`rig/up.sh` refuses anything that is not 127.0.0.1.

See `rig/CONTRACT.md` for the rig interface, `REPORT.md` for findings,
`CHECKLIST.md` for the A01-A10 x component review, and
`specs/004-security-review/` for spec and plan.

Run everything: `scripts/security-rig.sh` (details finished in T038).
