# `jiritsu-proposals`

`jiritsu-proposals` records a machine change before it applies the change.

The module keeps the intent, origin, actions, risk, approval, evidence, recovery plan, result, and history in one durable record.

## First version

The first version provides these functions:

- Create a proposal from a JSON definition.
- Classify typed actions and record live machine evidence.
- Record an explicit approval for the exact action set.
- Apply approved actions to the user configuration directory.
- Verify each effect with deterministic checks.
- Compare critical workloads before and after the actions.
- Commit a successful proposal or restore changed files after an error.
- Return the same JSON interface to people, agents, and scripts.

The command result schema and the proposal schema have version `1.0`.

## Lifecycle

A proposal uses this lifecycle:

```text
draft -> classified -> approved -> applying -> committed
   \          \                        \----> rolled_back
    \          \----------------------------> rejected
     \---------------------------------------> rejected
                                      \----> failed
```

| State | Meaning |
| --- | --- |
| `draft` | The store contains the intent and typed actions. |
| `classified` | The proposal contains risk, safeguards, evidence, checks, and a recovery plan. |
| `approved` | An actor approved the exact action digest and required permissions. |
| `applying` | Promotion started and the durable record contains the pre-change evidence. |
| `committed` | All actions and checks passed without a new critical workload error. |
| `rolled_back` | An action or check failed, and the module restored all applied actions. |
| `failed` | Promotion failed, and the module could not restore all applied actions. |
| `rejected` | An actor rejected the proposal before approval. |

The module rejects an operation that does not match the current state. Terminal states do not change.

## Supported actions

The first version accepts two action types. Both types use paths relative to the user configuration directory.

| Type | Fields | Effect |
| --- | --- | --- |
| `config.mkdir` | `path`, optional `mode` | Create one directory. The default mode is `0700`. |
| `config.write` | `path`, `content`, optional `mode`, optional `expected_sha256` | Write one file atomically. The default mode is `0600`. |

The module does not accept absolute paths, parent-path segments, symbolic-link targets, or paths outside the configuration directory.

`config.write` requires `expected_sha256` when the target file exists. This condition prevents an overwrite after stale observation.

The first version does not execute arbitrary commands. It does not use `sudo` or change system files.

## Create a proposal

Create `example.json` with this content:

```json
{
  "schema_version": "1.0",
  "intent": {
    "summary": "Add a local Jiritsu example.",
    "rationale": "The example records one user preference."
  },
  "origin": {
    "kind": "human",
    "actor": "jamie",
    "request_id": "example-1"
  },
  "actions": [
    {
      "type": "config.mkdir",
      "path": "jiritsu-example",
      "mode": "0700"
    },
    {
      "type": "config.write",
      "path": "jiritsu-example/preferences.conf",
      "content": "enabled=true\n",
      "mode": "0600"
    }
  ]
}
```

Run this command from the repository root:

```bash
./jiritsu-proposals/bin/jiritsu-proposals create example.json --pretty
```

You can use `-` instead of a file path to read the definition from standard input.

The response contains the generated proposal ID. The proposal starts in the `draft` state.

## Classify and approve the proposal

Classify the draft:

```bash
./jiritsu-proposals/bin/jiritsu-proposals classify PROPOSAL_ID \
  --actor classifier-name --pretty
```

Classification records these items:

- The low risk level and its reasons.
- The `user_config.write` permission.
- The required explicit approval.
- The target state and optimistic preconditions.
- The machine facts from `jiritsu-stated` or a baseline provider.
- The workload state from `jiritsu-workload`, when available.
- The deterministic checks and action-local recovery plan.

Approve the classified action set:

```bash
./jiritsu-proposals/bin/jiritsu-proposals approve PROPOSAL_ID \
  --actor approver-name --note "Reviewed the paths and content." --pretty
```

The approval contains the action digest. Promotion stops if the stored action set does not match this digest.

Reject a draft or classified proposal with this command:

```bash
./jiritsu-proposals/bin/jiritsu-proposals reject PROPOSAL_ID \
  --actor reviewer-name --reason "This change is not required." --pretty
```

## Promote the proposal

Promote an approved proposal:

```bash
./jiritsu-proposals/bin/jiritsu-proposals promote PROPOSAL_ID \
  --actor operator-name --pretty
```

Promotion uses this sequence:

1. Compare the approval digest with the current actions.
2. Compare each target with its classified state.
3. Record current machine facts and the workload baseline.
4. Save recovery data before each file replacement.
5. Apply each typed action.
6. Compare each result with its expected path or SHA-256 digest.
7. Assess the workloads again when `jiritsu-workload` is available.
8. Restore the applied actions if a check fails or a new critical workload error occurs.
9. Commit the proposal when all conditions pass.

The command returns exit status `1` when promotion rolls back or recovery fails. The JSON result contains the failure and recovery details.

## Inspect proposals

Show one complete proposal:

```bash
./jiritsu-proposals/bin/jiritsu-proposals show PROPOSAL_ID --pretty
```

Show its append-only lifecycle history:

```bash
./jiritsu-proposals/bin/jiritsu-proposals history PROPOSAL_ID --pretty
```

List proposal summaries:

```bash
./jiritsu-proposals/bin/jiritsu-proposals list --pretty
./jiritsu-proposals/bin/jiritsu-proposals list --state approved --pretty
```

The list output does not contain action content. The `show` output contains the complete durable record.

## Providers and fallbacks

Classification and promotion select `jiritsu-stated` first for machine facts. The baseline uses `omarchy version` and the standard Linux hostname.

Promotion selects `jiritsu-workload` for workload protection. It rolls back only for a new critical failure, not for an existing critical failure.

Promotion selects `jiritsu-checkpoints` when its command is available. It captures the selected user-configuration paths with an explicit generated policy.

This capture uses `--system off`, because the first action types have low risk. The checkpoint record links the recovery point to the proposal.

If checkpoint capture fails, promotion records the provider error. It then uses local file backups and removes newly created paths.

Each provider result contains the selected provider, source, status, and fallback errors. A missing optional module does not block standalone promotion.

## Storage

The standard store is `$XDG_STATE_HOME/jiritsu/proposals`. The fallback is `~/.local/state/jiritsu/proposals`.

Set `JIRITSU_PROPOSALS_STATE_DIR` to select a different store. You can also use `--state-dir` for one command.

The standard action root is `$XDG_CONFIG_HOME`. The fallback is `~/.config`.

Set `JIRITSU_PROPOSALS_CONFIG_ROOT` to select a different root. You can also use `--config-root` for classification and promotion.

Run this command to show the active paths:

```bash
./jiritsu-proposals/bin/jiritsu-proposals paths --pretty
```

The module writes proposal files atomically with mode `0600`. It uses mode `0700` for proposal and recovery directories.

Proposal definitions can contain file content. Do not put secrets in a proposal unless the durable store is the correct location.

## Exit status

Every command writes one JSON response, including request and operation errors.

| Exit status | Meaning |
| --- | --- |
| `0` | The requested operation completed. |
| `1` | The operation failed, rolled back, or does not match the lifecycle. |
| `64` | The command arguments are invalid. |
| `65` | The definition or stored data is invalid. |

## Install and develop

The development command runs from the source tree. It does not install files outside this module.

Install the standard Python command with this command:

```bash
python -m pip install ./jiritsu-proposals
jiritsu-proposals paths --pretty
```

Run the focused test suite:

```bash
python -m unittest discover -s jiritsu-proposals/tests -v
```

## Boundaries

This module coordinates supported low-risk actions. It does not provide a general command runner or a privilege boundary.

`jiritsu-broker` enforces deterministic policy for broker operations. Direct module use relies on operating-system permissions and separate approval.

Action-local recovery cannot restore external services, remote data, disclosed secrets, or unrelated machine state.

## Place in Jiritsu

`jiritsu-proposals` is the change journal and coordination layer. It turns a request into an inspectable, approved, and recoverable operation.

`jiritsu-stated` supplies current machine facts. `jiritsu-workload` supplies capability protection. `jiritsu-checkpoints` captures the selected user-configuration paths before promotion, with action-local recovery as the standalone fallback.

People and agents use the same proposal schema and command interface. The origin and history preserve their identities and actions.
