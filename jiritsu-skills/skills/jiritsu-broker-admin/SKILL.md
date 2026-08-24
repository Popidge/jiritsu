---
name: jiritsu-broker-admin
description: Reviews and configures Jiritsu broker policy, external approvals, audit records, principals, and authority. Use for denied requests, approval setup, policy design, or audit investigation. Do not use for ordinary Jiritsu reads, proposal execution, Omarchy customization, or direct module development.
---

# Administer the Jiritsu Broker

The broker maps typed operations to fixed module commands. Ordered policy rules grant the required authority.

The model, skill text, request body, and `actor` field do not grant authority.

## Related skills

- Load `jiritsu-observe` for ordinary read requests.
- Load `jiritsu-change` for an ordinary proposal lifecycle.
- Load `jiritsu-recover` for checkpoint creation or restore.
- Load `omarchy` for Omarchy configuration and supported system procedures.

If the task concerns the broker boundary itself, use this skill. Otherwise, use a related skill.

## Establish the live contract

1. Run `jiritsu-broker catalog --pretty`.
2. Read the request schema, operation schemas, policy source, principal source, and approval directory.
3. Treat the live catalog as the authority for available operations.
4. Do not add an operation through policy text.
5. Run `jiritsu-broker fingerprint REQUEST.json --pretty` for an approval-bound request.
6. Run `jiritsu-broker audit REQUEST_ID --pretty` to examine one request timeline.

If the source repository contains the broker, use `./jiritsu-broker/bin/jiritsu-broker`.

## Design policy

Read [references/policy-and-approvals.md](references/policy-and-approvals.md) before you create or change policy.

1. Keep `default = "deny"`.
2. Match the effective operating-system principal, not the request actor.
3. Put narrow rules before broad rules because the first matching rule wins.
4. Grant only the authorities required by the selected operations.
5. If the user does not request a different trust model, keep approval and promotion under `require_approval`.
6. Preserve unrelated rules and policy fields.
7. Make the policy a regular file with a trusted owner.
8. Remove group and other write permissions.
9. Validate the policy with a read-only catalog or test request before a sensitive request.

Do not edit the packaged policy. Put a user policy under `$XDG_CONFIG_HOME/jiritsu/broker-policy.toml` or select a test policy explicitly.

If the agent can change the active policy, approval, broker executable, or protected state, the same-user setup is not a privilege boundary.

If no protected approval channel exists, report that approval is unavailable. Do not simulate trust with file permissions under agent control.

The first broker contract does not define service-account provisioning. Do not invent an account, ownership model, or service topology.

## Handle external approval

1. Keep the request file byte-for-byte unchanged after fingerprinting.
2. Give the fingerprint result to an independent approver.
3. If you are the requesting agent, do not create the approval file.
4. Make sure that the approval names the exact request ID and SHA-256 digest.
5. Make sure that `expires_at` uses a future UTC timestamp.
6. Make the approval file a regular file with a trusted owner.
7. Remove group and other write permissions from the file and its directory.
8. Send the same request after the approval exists.

The broker rejects an expired approval, a digest mismatch, a symbolic link, or an untrusted owner.

## Read the audit journal

A permitted operation records `request`, `decision`, `action`, and `result` events in that order.

A denied or invalid operation has no `action` event. Do not infer that a machine action occurred.

The journal is append-only through the broker. It is not immutable or cryptographically protected.

CAUTION: The journal records full requests and results. Do not put secrets in broker requests.

## Error handling

If policy denies a request, report the first matching rule and missing authority. Do not bypass the decision with a direct module.

If approval is required, report the fingerprint and trusted approval path. Do not weaken file permissions to make approval easier.

If no independent approver can write the protected path, stop. Report the missing administrator prerequisite.

If policy data is invalid, stop before an action. Correct only the invalid policy that the user selected.
