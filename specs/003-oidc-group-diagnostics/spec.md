# Feature Specification: OIDC Group Diagnostics in the Operations Log

**Feature Branch**: `003-oidc-group-diagnostics`

**Created**: 2026-10-08

**Status**: Draft

**Input**: User description: "zaehl-o-mat soll bei einer abgelehnten SSO-Anmeldung (403, Nutzer in keiner erlaubten Gruppe) die von Authentik gelieferten Gruppennamen ins Betriebsprotokoll schreiben, damit Namensabweichungen (z. B. zaehlomat-user statt zaehlomat-users) sofort sichtbar sind. Das Verhalten ist per Konfigurationsschalter (Umgebungsvariable, Chart-Wert) an- und ausschaltbar; Standard: aus."

## Background

When a user signs in via single sign-on and belongs to none of the groups the app maps to a role, the sign-in is rejected with HTTP 403 "not a member of any permitted group". The log shows only the HTTP status, not which groups the identity provider delivered. During the rollout of feature 012 the cause (a configured group name `zaehlomat-user` instead of the real `zaehlomat-users`) could only be found by comparing a screenshot of the identity provider with the configuration. The operator needs a switchable diagnostic that shows delivered versus expected group names without a code change.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - See why an SSO sign-in was rejected (Priority: P1)

An operator turns the diagnostic on. A user whose groups do not match tries to sign in and is rejected. The operator opens the log and sees one line that lists the group names the identity provider delivered next to the group names the app expects for each role, so a spelling or naming difference is visible immediately.

**Why this priority**: This is the whole point of the feature; it replaces guesswork and screenshots.

**Independent Test**: Switch the diagnostic on, sign in with a user in a differently named group, read the log: exactly one line shows delivered and expected names.

**Acceptance Scenarios**:

1. **Given** the diagnostic is on and a user is delivered the groups `zaehlomat-user`, **When** the sign-in is rejected, **Then** one diagnostic log line shows the user, the delivered groups and the three expected group names, and the user still receives the same rejection as before.
2. **Given** the diagnostic is off, **When** the same user is rejected, **Then** one security event line with the user and the rejection reason is written, and no line contains group names.
3. **Given** the diagnostic is on and the identity provider delivers no group information at all (claim missing) or an empty list, **When** the sign-in is rejected, **Then** a distinct log line states that no group information was delivered (hinting at a missing scope or mapping at the identity provider), not a list of names.

---

### User Story 2 - Switch the diagnostic on and off by configuration (Priority: P1)

The operator controls the diagnostic with a configuration switch (environment variable, exposed as a chart value and set in the cluster release), without changing code. The default is off. The cluster rollout keeps it off; the operator enables it only while investigating a rejection.

**Why this priority**: Group names are identity data; the diagnostic must be a deliberate, reversible choice.

**Independent Test**: Start the app with the switch unset, on, and off; check the behavior of Story 1 in each case.

**Acceptance Scenarios**:

1. **Given** the switch is unset, **When** a sign-in is rejected, **Then** behavior equals "off".
2. **Given** the switch is turned on via the chart value, **When** the app restarts, **Then** the diagnostic is active.
3. **Given** the value is not a valid boolean, **When** the app starts, **Then** it fails with a clear configuration error instead of silently choosing a mode.

---

### User Story 3 - Confirm successful mapping without exposing names (Priority: P3)

With the diagnostic on, a successful sign-in writes one short line with the resulting role and the number of delivered groups (no names), so the operator can confirm that mapping works.

**Why this priority**: Helps verify a fix after a rejection, but the rejection case is what hurts today.

**Independent Test**: Switch on, sign in with a permitted user, read the log: one line with role and group count, no names.

**Acceptance Scenarios**:

1. **Given** the diagnostic is on and a permitted user signs in, **When** the sign-in succeeds, **Then** one line shows the user, the assigned role and the number of groups, and no group names.
2. **Given** the diagnostic is off, **When** a permitted user signs in, **Then** no such line is written.

---

### Edge Cases

- A user is delivered many groups: the line lists at most a bounded number of names and says how many were omitted.
- A delivered group name is very long or contains unusual characters (control characters, line breaks): the log line stays one line and the value is shortened and sanitised so it cannot forge additional log lines.
- The identity provider delivers the claim in an unexpected shape (not a list, for example a single string): treated as "no usable group information" and logged as such.
- Group names are compared exactly as today (case-sensitive); the diagnostic does not change matching, it only reveals it.
- Concurrent sign-ins: each rejection writes its own line.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a configuration switch (default off) that enables an OIDC group diagnostic in the operations log.
- **FR-002**: With the switch on, a rejected sign-in due to missing group membership MUST write exactly one log line containing the group names delivered by the identity provider and the configured group names for the admin, manager and user roles.
- **FR-003**: With the switch on, if the group claim is missing, empty or unusable, the system MUST write a distinct log line stating that no group information was delivered, instead of a names list.
- **FR-004**: With the switch off, the system MUST NOT write any log line containing delivered or expected group names.
- **FR-005**: Independent of the switch, every sign-in attempt that is rejected MUST produce one security event line with the rejection reason category and the user (the sign-in name delivered by the identity provider), as required by the project's logging principle for login failures and access-denied responses. This line MUST NOT contain group names.
- **FR-006**: The log lines of this feature MUST NOT contain tokens, authorization codes, client secrets, e-mail addresses, raw identity claims other than the sign-in name and (only with the switch on) group names, or any other data.
- **FR-007**: The user in the log lines is the sign-in name (not the e-mail address, not the internal subject identifier). If the identity provider delivers no sign-in name, a short non-reversible identifier derived from the subject is used instead. The value is sanitised and length-limited (FR-009).
- **FR-013**: Group names are handled as sensitive: they MUST appear in the log only with the switch on, and when the switch is on the application MUST write one warning line at start-up stating that group-name diagnostics are active, so the setting is not left on unnoticed.
- **FR-008**: With the switch on, a successful sign-in MUST write one line with the assigned role and the number of delivered groups, without names (Story 3).
- **FR-009**: Delivered values MUST be sanitised and length-limited before logging so that a hostile or malformed group name cannot inject extra log lines or flood the log; the number of listed names MUST be bounded.
- **FR-010**: The sign-in behavior itself (HTTP 403 for users in no permitted group, role assignment, highest privilege wins) MUST remain unchanged.
- **FR-011**: An invalid switch value MUST fail the application start with a clear configuration error.
- **FR-012**: The switch MUST be configurable through the Helm chart and set in the cluster release, and MUST appear in the project documentation (configuration table).

### Key Entities

- **Group diagnostic setting**: on/off switch; default off; one value per deployment.
- **Security event line** (always on): one line per rejected sign-in with the reason category and the user; never group names.
- **Diagnostic log entry** (only with the switch on): one line per event with the event kind (rejected, no group information, accepted), the user, delivered group names (bounded and sanitised, only for rejection), expected group names (only for rejection), role and group count (only for acceptance).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With the switch on, a rejected sign-in produces exactly one diagnostic line showing delivered and expected group names, and an operator can name the mismatching group from that line alone, without consulting the identity provider.
- **SC-002**: With the switch off or unset, a rejected sign-in produces one security event line with the user and zero lines containing group names.
- **SC-003**: Automated checks over rejected, accepted, missing-claim and malformed-claim sign-ins find zero occurrences of tokens, codes, client secrets or e-mail addresses in the log, and zero group names when the switch is off.
- **SC-004**: A group name containing line breaks produces still exactly one log line.
- **SC-005**: Sign-in outcomes (who is accepted, rejected and with which role) are identical with the switch on and off in every tested case.
- **SC-006**: Turning the diagnostic on or off requires only a configuration change and an application restart, no new release.

## Assumptions

- The configuration switch is an environment variable of the backend; the Helm chart exposes it as a chart value; the cluster release does not enable it by default.
- Group memberships are treated as sensitive information (operator decision): the always-on security event names the user but never groups; group names appear only with the deliberately enabled diagnostic, which announces itself at start-up (FR-013).
- The project's logging principle (security-relevant events logged, no secrets or tokens in logs) takes precedence: a rejection event with the user is always logged (FR-005).
- The diagnostic is meant to be switched on temporarily while investigating; the cluster rollout leaves the switch off by default and the operator turns it on only when needed (changed from "initially on" because of the sensitivity of group names).
- The delivered group claim name is the already configurable one (default `groups`); this feature does not change claim handling.
- The patch release is v0.4.1 of the application and chart; the cluster rollout (setting the value in the release) happens in the k3s-gitops repository.
- Logging goes to the existing application log (standard output); no new log destination or alerting is part of this feature.
