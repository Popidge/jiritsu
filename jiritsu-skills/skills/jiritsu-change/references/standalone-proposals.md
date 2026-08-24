# Standalone Proposal Workflow

If `jiritsu-broker` is not installed, use this workflow.

The direct module stores durable intent, but it does not provide the broker authority boundary.

## Create and classify

Create a JSON definition with `schema_version`, `intent`, `origin`, and `actions`. Record the real origin kind and actor.

```json
{
  "schema_version": "1.0",
  "intent": {
    "summary": "Create one user preference",
    "rationale": "Apply the requested configuration change"
  },
  "origin": {
    "kind": "agent",
    "actor": "codex",
    "request_id": "standalone-change-1"
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
      "mode": "0600"
    }
  ]
}
```

```bash
jiritsu-proposals create DEFINITION.json --pretty
jiritsu-proposals classify PROPOSAL_ID --actor AGENT_ID --pretty
```

Read the complete classified proposal:

```bash
jiritsu-proposals show PROPOSAL_ID --pretty
```

Present the action digest, target paths, risk, safeguards, verification, and recovery to a separate approver.

## Approve and promote

Do not run approval until the user explicitly approves the classified action set.

Record the real approver identity. Do not use an agent identity as a substitute for a human identity.

```bash
jiritsu-proposals approve PROPOSAL_ID --actor APPROVER_ID --note "APPROVAL_NOTE" --pretty
jiritsu-proposals promote PROPOSAL_ID --actor AGENT_ID --pretty
```

After promotion, run these commands:

```bash
jiritsu-proposals show PROPOSAL_ID --pretty
jiritsu-proposals history PROPOSAL_ID --pretty
```

If the source repository contains the module, use `./jiritsu-proposals/bin/jiritsu-proposals`.

## Stop conditions

- If the lifecycle rejects an operation for the current state, stop.
- If the action digest differs from the approved digest, stop.
- If optimistic target preconditions no longer match, stop.
- If the terminal state is `failed`, stop unrelated work.
