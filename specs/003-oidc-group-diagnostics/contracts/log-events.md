# Contract: Log lines

Logger `app.oidc_audit`; text lines, `key=value` pairs; list values are JSON arrays. One event = one line (JSON encoding escapes control characters).

## Always on (WARNING)

```text
oidc.signin_rejected reason=no_permitted_group user="alice"
oidc.signin_rejected reason=no_group_information user="alice"
```

No group names, no e-mail, no subject.

## Switch on only

Rejected, groups delivered (WARNING):

```text
oidc.group_diagnostic user="alice" delivered=["zaehlomat-user"] omitted=0 expected={"admin":"default-admins","manager":"zaehlomat-manager","user":"zaehlomat-users"}
```

Group claim missing, empty or unusable (WARNING):

```text
oidc.group_claim_missing user="alice" claim="groups" hint="identity provider delivered no usable group list; check the scope/property mapping"
```

Accepted (INFO):

```text
oidc.signin_accepted user="alice" role=manager groups=2
```

Start-up (WARNING, once):

```text
OIDC group diagnostics are ENABLED (OIDC_GROUP_DIAGNOSTICS=true): group names of rejected sign-ins are written to the log
```

## Never logged

Tokens, authorization codes, client secret, e-mail addresses, raw subject, any claim other than the sign-in name and (switch on, rejection) the group list.
