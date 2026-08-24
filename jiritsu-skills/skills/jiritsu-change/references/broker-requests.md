# Broker Requests for a Configuration Change

Read this reference only for the integrated proposal workflow.

Run `jiritsu-broker catalog --pretty` first. The live catalog is the authority when a template differs from the installed contract.

## Create

Use paths relative to the user configuration root.

```json
{
  "schema_version": "1.0",
  "request_id": "change-create-1",
  "actor": "codex",
  "operation": "proposal.create",
  "arguments": {
    "proposal_id": "p-example",
    "intent": {
      "summary": "Update one user preference",
      "rationale": "Apply the requested Omarchy customization"
    },
    "actions": [
      {
        "type": "config.mkdir",
        "path": "example",
        "mode": "0700"
      },
      {
        "type": "config.write",
        "path": "example/preferences.conf",
        "content": "enabled=true\n",
        "mode": "0600",
        "expected_sha256": "DIGEST_OF_EXISTING_FILE"
      }
    ]
  }
}
```

If the parent directory exists, omit the `config.mkdir` action.

If the target does not exist, omit `expected_sha256`. If the module can generate the ID, omit `proposal_id`.

## Classify

```json
{
  "schema_version": "1.0",
  "request_id": "change-classify-1",
  "actor": "codex",
  "operation": "proposal.classify",
  "arguments": {
    "proposal_id": "p-example"
  }
}
```

Classification records live machine facts, workload evidence, risk, verification, and recovery.

## Approve

```json
{
  "schema_version": "1.0",
  "request_id": "change-approve-1",
  "actor": "codex",
  "operation": "proposal.approve",
  "arguments": {
    "proposal_id": "p-example",
    "note": "The independent approver reviewed the classified action digest."
  }
}
```

The `actor` field records the requesting agent. It does not identify the independent approver or grant authority.

## Promote

```json
{
  "schema_version": "1.0",
  "request_id": "change-promote-1",
  "actor": "codex",
  "operation": "proposal.promote",
  "arguments": {
    "proposal_id": "p-example"
  }
}
```

Promotion reads current state, creates recovery data, applies the actions, and compares critical workloads.

## Send a request

Save one exact JSON object in a temporary request file. Then run:

```bash
jiritsu-broker request REQUEST.json --pretty
```

If the source repository contains the broker, use `./jiritsu-broker/bin/jiritsu-broker`.

Keep an approval request file unchanged between fingerprinting and the approved retry. A changed byte produces a different request digest.
