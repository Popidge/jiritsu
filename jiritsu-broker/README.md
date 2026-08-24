# `jiritsu-broker`

`jiritsu-broker` gives agents a small and controlled interface to Jiritsu.

The broker maps typed requests to fixed module operations. A deterministic policy permits or denies each operation.

## Goals

The module has four goals:

- It gives agents a small and stable tool surface.
- It grants only the authority that an approved operation requires.
- It records requests, decisions, actions, and results.
- It keeps policy enforcement deterministic and outside the model.

## First version

The integrated version provides these functions:

- It exposes eleven semantic operations through one JSON request contract.
- It gets the caller principal from the effective operating-system user.
- It grants only the authority that the matching policy rule contains.
- It reads sensitive approvals from files outside the request.
- It records each request, decision, action, and result in an audit journal.
- It starts module commands with argument arrays and does not use a shell.

The result schema and request schema have version `1.0`.

## Tool surface

| Operation | Effect | Required authority | Provider |
| --- | --- | --- | --- |
| `state.query` | Read machine facts | `machine_state.read` | `jiritsu-stated`, then a baseline provider |
| `workload.assess` | Assess workload contracts | `workload.assessment.read` | `jiritsu-workload` |
| `proposal.create` | Record durable intent | `proposal.intent.write` | `jiritsu-proposals` |
| `proposal.classify` | Record machine, workload, risk, and recovery evidence | `proposal.classification.write`, state and workload reads | `jiritsu-proposals` |
| `proposal.approve` | Record a policy-authorized approver for the classified actions | `proposal.approval.write` | `jiritsu-proposals` |
| `proposal.query` | Read one proposal | `proposal.read` | `jiritsu-proposals` |
| `proposal.list` | List proposals | `proposal.read` | `jiritsu-proposals` |
| `proposal.promote` | Apply an approved proposal | `user_config.write` | `jiritsu-proposals` |
| `checkpoint.inspect` | Inspect recovery providers | `checkpoint.read` | `jiritsu-checkpoints` |
| `checkpoint.query` | Read one checkpoint | `checkpoint.read` | `jiritsu-checkpoints` |
| `checkpoint.list` | List checkpoints | `checkpoint.read` | `jiritsu-checkpoints` |

Run `catalog` to get the current argument contract:

```bash
./jiritsu-broker/bin/jiritsu-broker catalog --pretty
```

The catalog returns JSON Schema-compatible request and argument definitions.

The catalog is static. It does not probe the machine or start another module.

## Send a request

A request contains five fields:

```json
{
  "schema_version": "1.0",
  "request_id": "state-example-1",
  "actor": "codex",
  "operation": "state.query",
  "arguments": {
    "selectors": ["system.hostname", "system.omarchy.version"],
    "timeout_seconds": 5
  }
}
```

Save the request as `request.json`. Then send it to the broker:

```bash
./jiritsu-broker/bin/jiritsu-broker request request.json --pretty
```

You can also send one request through standard input:

```bash
./jiritsu-broker/bin/jiritsu-broker request --pretty < request.json
```

The `actor` field records provenance. It does not grant authority.

The broker gets the policy principal from the effective user ID. Request text cannot change this principal.

### Create a proposal

The create operation accepts an intent and the typed actions from `jiritsu-proposals`:

```json
{
  "schema_version": "1.0",
  "request_id": "proposal-example-1",
  "actor": "codex",
  "operation": "proposal.create",
  "arguments": {
    "proposal_id": "p-example",
    "intent": {
      "summary": "Create an example directory",
      "rationale": "Demonstrate durable agent intent."
    },
    "actions": [
      {
        "type": "config.mkdir",
        "path": "jiritsu-example",
        "mode": "0700"
      }
    ]
  }
}
```

The broker supplies `origin.kind`, `origin.actor`, and `origin.request_id`. The request cannot claim a human origin.

This operation creates only a draft. Use `proposal.classify` to collect the integrated evidence.

`proposal.approve` and `proposal.promote` each require a request-bound external approval under the packaged policy. The approval operation records the trusted file's `approved_by` identity in the proposal. It does not record the requesting agent as the approver.

Promotion uses the proposal module's complete path. It reads current state, assesses workloads, creates a linked user-configuration checkpoint, applies the typed actions, verifies the result, and compares critical workloads before commit. If the checkpoint module is absent or fails, proposal-local backups preserve standalone recovery.

## Read a result

Each result contains these fields:

| Field | Meaning |
| --- | --- |
| `status` | Broker result: `ok`, `denied`, `approval_required`, or `error` |
| `decision` | Rule, principal, required authority, granted authority, and policy source |
| `action` | Operation, effect, adapter, authority, and shell status |
| `result` | Provider, source, fallback errors, and module data |
| `errors` | Stable broker or provider errors |

Module status stays inside `result.data`. For example, a degraded workload assessment is a successful broker operation.

## Policy

The broker uses ordered TOML rules. The first rule that matches the principal and operation supplies the decision.

Each rule contains its permitted authorities. The broker denies the request when a required authority is absent from the matching rule.

The policy default must be `deny`. Thus, an unmatched operation cannot get authority.

The packaged policy has these decisions:

- Read operations are permitted.
- Proposal creation and classification are permitted because they record intent and evidence without changing the target configuration.
- Proposal approval requires external approval.
- Proposal promotion requires external approval.
- All operations that do not match a rule are denied.

This example permits one state operation:

```toml
schema_version = "1.0"
default = "deny"

[[rules]]
id = "agent-state"
principals = ["uid:1000"]
operations = ["state.query"]
decision = "allow"
authorities = ["machine_state.read"]
```

Set `JIRITSU_BROKER_POLICY` to select a policy. You can also use `--policy` for one command.

The standard user policy is `$XDG_CONFIG_HOME/jiritsu/broker-policy.toml`. The broker uses the packaged policy when this file is absent.

Unknown fields, duplicate rule IDs, and unknown decisions make the policy invalid. An invalid policy stops the request before an action.

The policy must be a regular file with a trusted owner. It must not be writable by a group or other users.

## External approval

A `require_approval` rule does not trust a value from the request. It reads `<request_id>.json` from the approval directory.

Run this command to calculate the exact request digest and approval path:

```bash
./jiritsu-broker/bin/jiritsu-broker fingerprint request.json --pretty
```

An approval file has this structure:

```json
{
  "schema_version": "1.0",
  "request_id": "promote-example-1",
  "request_sha256": "<digest from fingerprint>",
  "approved_by": "human:jamie",
  "expires_at": "2026-08-24T18:00:00Z"
}
```

The broker rejects an expired approval. It also rejects a mismatched digest, a symbolic link, or an untrusted owner.

The approval file and directory must not be writable by a group or other users.

The default approval directory is `$XDG_STATE_HOME/jiritsu/broker/approvals`. A policy can set an `approval_directory` value.

If approval is absent, the broker returns `approval_required`. Add the trusted approval and send the same request ID again.

An approval file is trustworthy only when the agent cannot write that file. The same condition applies to the policy and broker executable.

## Audit journal

The broker appends JSON lines to `$XDG_STATE_HOME/jiritsu/broker/audit.jsonl`.

A permitted operation produces four event types in order:

1. `request`
2. `decision`
3. `action`
4. `result`

A denied operation has no `action` event. An invalid typed operation also has no `action` event.

Read the full journal:

```bash
./jiritsu-broker/bin/jiritsu-broker audit --pretty
```

Read the records for one request:

```bash
./jiritsu-broker/bin/jiritsu-broker audit state-example-1 --pretty
```

The broker sets directory mode `0700` and journal mode `0600`. It locks the journal during each read and append operation.

The journal is append-only through the broker. It is not an immutable or cryptographic log.

CAUTION: Do not put secrets in broker requests. The audit journal records the full request and result.

After a terminal result, the broker rejects the same request ID. This rule prevents an accidental action replay.

## Providers and fallbacks

The broker finds each Jiritsu command in this order:

1. The module-specific `JIRITSU_BROKER_*_COMMAND` variable.
2. The command in `PATH`.
3. The sibling development command in this repository.

`state.query` uses `jiritsu-stated` by default. If that command is unavailable or invalid, the broker uses supported Omarchy and Linux interfaces.

The baseline state provider reports these facts:

- `system.hostname`
- `system.os`
- `system.kernel`
- `system.omarchy.version`

The baseline provider reports an unsupported selector as a structured error. A query can return partial data with successful facts.

The workload, proposal, and checkpoint operations have no equivalent baseline contract. If a module is absent, only the dependent operation returns an error.

Proposal promotion has its own internal fallbacks. It uses baseline Omarchy/Linux machine facts when stated is unavailable. It skips workload comparison when workload is unavailable. It uses action-local recovery when checkpoints are unavailable.

A missing optional module does not stop broker startup. It also does not block `catalog`, `audit`, or the standalone state provider.

Each operation result names the selected provider and source. It also includes errors from a failed preferred provider.

## Authority boundary

The broker never uses `sudo` and never sends a request through a shell. Each adapter builds an argument array for one known command.

The broker removes loader and Python path variables from child environments. It passes only the environment values that supported modules need.

The first version runs with the permissions of its operating-system process. It does not create a privilege boundary inside one user account.

Run the broker through a separate service account when an agent has shell access. Protect the policy, approvals, state, and executable from that agent.

Skills, web content, command output, and request arguments cannot add a tool or authority. Only code and local policy define these values.

## Exit status

| Exit status | Meaning |
| --- | --- |
| `0` | The broker completed the operation |
| `1` | The operation or provider returned an error |
| `3` | Policy denied the request or requires approval |
| `64` | The request or command arguments are invalid |
| `65` | The policy or audit data is unavailable or invalid |

Every command writes one JSON object, including error cases.

## Development

Run the focused test suite:

```bash
python -m unittest discover -s jiritsu-broker/tests -v
```

The tests cover typed operations, exact adapters, policy authority, external approval, scoped provider failures, audit events, provenance, replay prevention, and the real five-module happy path.

The package also defines a standard Python command:

```bash
python -m pip install ./jiritsu-broker
jiritsu-broker catalog --pretty
```

The development command does not install files outside this module.

## Place in Jiritsu

`jiritsu-broker` is the agent boundary of Jiritsu. It exposes module commands without hiding their safety rules.

A person can still use each Jiritsu module directly. The broker does not replace those human interfaces.
