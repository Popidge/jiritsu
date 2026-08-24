# Broker Policy and External Approvals

Read this reference only for broker administration.

## Policy model

The broker reads ordered TOML rules. The first matching principal and operation supplies the decision.

Each rule contains its granted authorities. The broker denies a request when the rule lacks a required authority.

Use this minimal policy shape:

```toml
schema_version = "1.0"
default = "deny"

[[rules]]
id = "agent-read-state"
principals = ["uid:1000"]
operations = ["state.query"]
decision = "allow"
authorities = ["machine_state.read"]
```

Use `require_approval` for a sensitive matching rule. Do not use a request field as approval evidence.

The standard user policy is `$XDG_CONFIG_HOME/jiritsu/broker-policy.toml`.

The policy must satisfy these conditions:

- It is a regular file.
- A trusted identity owns it.
- A group cannot write it.
- Other users cannot write it.
- `default` is `deny`.
- Rule IDs are unique.
- Decisions and authorities match the live catalog and broker contract.

## Principal and actor

The broker gets the principal from the effective operating-system user.

The request `actor` records provenance only. It cannot select a principal or grant authority.

If the user needs a real operating-system boundary, run the agent under a separate service account.

## Approval file

Calculate the exact digest and path:

```bash
jiritsu-broker fingerprint REQUEST.json --pretty
```

An independent approver creates this JSON object at the reported path:

```json
{
  "schema_version": "1.0",
  "request_id": "change-promote-1",
  "request_sha256": "DIGEST_FROM_FINGERPRINT",
  "approved_by": "human:reviewer",
  "expires_at": "2026-08-24T20:00:00Z"
}
```

The requesting agent must not create this file. The approval is trustworthy only when that agent cannot write it.

The approval directory normally exists under `$XDG_STATE_HOME/jiritsu/broker/approvals`.

## Request reuse

If a request returns `approval_required`, add the trusted approval and send the same request ID again.

If a request returns a terminal result, do not send that request ID again. The replay guard rejects it.
