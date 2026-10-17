# Contract: Report Format (`security/REPORT.md`)

1. **Summary**: counts per severity and status; list of fixed critical/high findings with PR links.
2. **Coverage matrix**: components × A01–A10; each cell = methods + result.
3. **Findings**: one section per finding with the fields from `data-model.md`; ordered by severity.
4. **Experiments**: table of experiment IDs, result, repetitions and failure rate for races.
5. **Tooling results**: bandit, pip-audit, Trivy (fs/images), gitleaks, eslint-security, npm audit, chart/Compose checks; triaged false positives.
6. **Not verifiable from the repository**: items depending on the live cluster, ingress, identity provider or gateway.
7. **Decisions**: accepted risks with reason and owner.

No secrets, tokens, real personal data or real images appear anywhere in the report.
