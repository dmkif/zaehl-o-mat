# Feature Specification: Full Security Review of the Application

**Feature Branch**: `004-security-review`

**Created**: 2026-10-08

**Status**: Draft

**Input**: User description: "mach ein weiteres feature auf, dass den kompletten Code einem Sicherheitsreview unterziehen soll. Nicht nur statische Analyse, gerne kann auch ein Versuchsaufbau gemacht werden, um Race Conditions oder andere Fehler zu finden. OWASP Top 10 sollen geprüft werden."

## Background

The application manages utility meters, readings, property access and uploaded meter photos. It is now reachable on the internet-facing ingress with single sign-on, hands uploaded images to an AI model through a gateway and stores personal consumption data. The project constitution requires every change to be checked against the OWASP Top 10 (2025), but no systematic review of the whole code base has been done. One concrete weakness was already noticed while writing feature 003: group matching uses a substring test when the identity provider sends a single string instead of a list.

This feature reviews the complete code base and its deployment against the OWASP Top 10 (2025), combines reading the code with tests against a running, isolated copy of the application (including concurrency and fault-injection experiments), and delivers a prioritised findings report. Confirmed serious findings are fixed with regression tests.

## Clarifications

### Session 2026-10-08

- Q: Sollen die Fixes für kritische und hohe Funde in diesem Feature bleiben oder zunächst nur berichtet werden? → A: Fixes für kritische und hohe Funde bleiben in diesem Feature (mit Regressionstest); mittlere/niedrige Funde werden berichtet und getrackt.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Know where the application is weak (Priority: P1)

The operator receives a findings report that covers every component (backend, frontend, optional OCR service, container images, Helm chart, Compose file, CI workflows, migration scripts) and every OWASP Top 10 (2025) category. Each category states what was checked, how (reading, automated tool, experiment), and the result, including "no finding" with the evidence.

**Why this priority**: Without the report nothing can be prioritised; it is the core deliverable.

**Independent Test**: Open the report; every one of the ten categories and every listed component has an entry with method and result; every finding has severity, location, reproduction steps and a recommendation.

**Acceptance Scenarios**:

1. **Given** the review is complete, **When** the report is read, **Then** each of A01–A10 has a coverage statement for each component it applies to.
2. **Given** a finding, **When** it is read, **Then** it names location, impact, likelihood, severity (critical/high/medium/low/informational), proof (test or reproduction) and a fix recommendation.
3. **Given** a category without findings, **When** it is read, **Then** it states which checks support that conclusion, not just "none found".

---

### User Story 2 - Prove weaknesses by experiment, not by guessing (Priority: P1)

A test setup that runs an isolated copy of the application with synthetic data lets the reviewer attack it: concurrent requests (for example parallel first sign-ins, parallel creation of the same reading, parallel uploads of the same image), access to other users' objects, forged or expired tokens, malformed and oversized uploads, hostile input in every field, and failure of dependencies (database, identity provider, AI gateway, OCR service) during a request. Results are reproducible by re-running the experiments.

**Why this priority**: Race conditions and logic flaws are not found by reading alone; the operator explicitly asked for experiments.

**Independent Test**: Start the test setup from a clean checkout with one command, run the experiment suite, and get a pass/fail report per experiment; a deliberately weakened build makes the matching experiment fail.

**Acceptance Scenarios**:

1. **Given** the isolated setup, **When** many identical first sign-ins arrive at the same moment, **Then** exactly one account results and no request fails with an unhandled error.
2. **Given** two users with different property access, **When** one requests or modifies the other's meters, readings or images by guessing identifiers, **Then** every attempt is denied and nothing changes.
3. **Given** the AI gateway or the database becomes unavailable mid-request, **When** the request is processed, **Then** the application fails closed with a clean error and leaves no half-written data.
4. **Given** a weakened build with a known flaw, **When** the experiment suite runs, **Then** the corresponding experiment fails (the experiments can actually detect problems).

---

### User Story 3 - Fix what is serious, track the rest (Priority: P2)

Confirmed critical and high findings are fixed in this feature, each with a regression test that fails before and passes after the fix. Medium, low and informational findings are listed with a recommendation and a tracking entry, without blocking the release.

**Why this priority**: A report without fixes leaves the application exposed; but fixes depend on what the review finds.

**Independent Test**: For every critical/high finding there is a regression test that fails on the pre-fix code and passes on the fixed code; the report marks each finding fixed, accepted or tracked.

**Acceptance Scenarios**:

1. **Given** a confirmed high finding, **When** the fix is merged, **Then** its regression test fails on the pre-fix commit and passes after.
2. **Given** a medium finding, **When** the report is finalised, **Then** it has a tracking entry and a documented decision (fix later or accept with reason).

---

### User Story 4 - Keep the review repeatable (Priority: P3)

The experiment suite and the checklist of what was reviewed stay in the repository, so a later change can re-run the experiments and the review does not need to start from scratch.

**Why this priority**: Value over time; the immediate need is the first review.

**Independent Test**: A new contributor follows the written instructions and runs the suite successfully.

**Acceptance Scenarios**:

1. **Given** a clean checkout, **When** the documented command is run, **Then** the isolated setup and the experiment suite run without manual steps.

---

### Edge Cases

- The experiments must never touch the production cluster, the production database or real uploaded images; all data is synthetic and the setup is local or in a throw-away namespace with a separate database.
- A finding is only counted as confirmed when reproduced; suspected issues are labelled as such.
- Experiments that depend on timing (races) may not fail on every run; each is repeated enough times and reports how often it failed.
- Components that are optional (OCR service, AI gateway) must be reviewed in both configured and unconfigured states.
- Fixes must not change documented behavior except where the old behavior was the flaw; such changes are called out.
- Third-party code (dependencies, base images) is reviewed for known vulnerabilities and pinning, not rewritten.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The review MUST cover every component in the repository: backend, frontend, optional OCR service, container images, Helm chart, Compose file, CI workflows, database migrations and operational scripts.
- **FR-002**: The review MUST check every category of the OWASP Top 10 (2025) as named in the project constitution (A01 to A10) and record method and result per category and component.
- **FR-003**: The review MUST combine code reading with automated analysis (static analysis, dependency and secret scanning, image scanning) and with experiments against a running isolated copy of the application.
- **FR-004**: The system under test MUST be an isolated copy with synthetic data; experiments MUST NOT run against production systems or data.
- **FR-005**: The experiments MUST include concurrency tests for at least: first sign-in of the same identity, creation of the same reading or duplicate image, concurrent edits of the same object, rate-limit counting under parallel requests, and token expiry/rotation boundaries.
- **FR-006**: The experiments MUST include access-control tests across roles and properties (horizontal and vertical privilege escalation, guessing identifiers) for every endpoint, including streaming and file-serving endpoints.
- **FR-007**: The experiments MUST include input attacks on every external input: injection attempts, malformed and oversized uploads (content type versus content, path tricks, decompression/size bombs), hostile values in fields, and unexpected shapes in data coming from the identity provider, the AI gateway and the OCR service.
- **FR-008**: The experiments MUST include request-forgery checks for every outbound request that uses a configured or user-influenced address, and verify that responses of the AI and OCR components are validated before being stored.
- **FR-009**: The experiments MUST include fault injection: loss of the database, identity provider, AI gateway or OCR service during a request, verifying fail-closed behavior and absence of partial writes.
- **FR-010**: The experiments MUST be repeatable from the repository with documented steps, and a deliberately weakened variant MUST demonstrate that at least the key experiments can detect a flaw.
- **FR-011**: The findings report MUST give each finding an identifier, severity, location, description, impact, reproduction, evidence and recommendation, and state whether it was confirmed.
- **FR-012**: Every confirmed critical or high finding MUST be fixed in this feature with a regression test that fails before the fix and passes after it.
- **FR-013**: Medium, low and informational findings MUST be recorded with a decision (fix, track, or accept with reason).
- **FR-014**: The review MUST include configuration and deployment (headers, defaults, secrets handling, container and chart hardening, network exposure) as seen in the repository, and document what could not be verified from the repository alone.
- **FR-015**: The review MUST verify the logging requirements (security events logged, no secrets, tokens or personal data leaking into logs) with experiments, not only by reading.
- **FR-016**: The previously noticed group-matching weakness (single string instead of list) MUST be assessed and recorded as a finding.
- **FR-017**: Experiment results and the report MUST NOT contain real secrets, real personal data or real uploaded images.

### Key Entities

- **Finding**: identifier, category (A01–A10), component, severity, status (suspected/confirmed/fixed/tracked/accepted), location, description, impact, reproduction, evidence, recommendation.
- **Experiment**: identifier, category, goal, setup, steps, expected result, observed result, repeat count, failure rate (for races).
- **Review coverage matrix**: components × OWASP categories, each cell with method(s) and result.
- **Test environment**: isolated application copy with its own database and synthetic data, including stand-ins for the identity provider, the AI gateway and the OCR service.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The coverage matrix has an entry for 100 % of component × category pairs that apply, each with method and result.
- **SC-002**: 100 % of confirmed critical and high findings are fixed with a regression test that fails before and passes after; none remain open at the end.
- **SC-003**: Every external input and every endpoint of the application is exercised by at least one experiment for access control or input handling, shown by a coverage list in the report.
- **SC-004**: Each race-condition experiment is repeated at least 100 times and reports its failure rate; after fixes the rate for confirmed races is 0 in 100 repetitions.
- **SC-005**: From a clean checkout, one documented command runs the isolated setup and all experiments in under 30 minutes on a developer machine.
- **SC-006**: The weakened variant makes at least the access-control, injection, upload and race experiments fail, proving the experiments detect flaws.
- **SC-007**: A final scan shows no new critical or high issues from dependency, image or secret scanning, and no secret, token or personal data appears in the logs written during the experiment run.
- **SC-008**: The operator can read the report and decide for every finding within one sitting what to do, because each finding names impact, evidence and recommendation.

## Assumptions

- The review covers the code and configuration in this repository at the current state of the main branch (version 0.4.0 with feature 003 pending); the live cluster, the identity provider's own configuration and the gateway are out of scope except where the repository defines how the application uses them.
- The isolated test environment runs locally in containers (the project already ships a Compose setup) and uses stand-ins for the identity provider, AI gateway and OCR service; no experiment touches production.
- OWASP Top 10 (2025) as numbered in the project constitution is the reference.
- Fixes for critical and high findings are in scope of this feature; larger redesigns discovered by the review become separate follow-up features.
- Dependencies and base images are assessed for known vulnerabilities and pinning, not audited line by line.
- The review is performed by the engineering assistant together with the operator; no external penetration-test provider is involved.
