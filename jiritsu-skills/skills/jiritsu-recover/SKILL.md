---
name: jiritsu-recover
description: Plans, creates, inspects, and restores Jiritsu checkpoints for system snapshots or selected user configuration. Use for independent machine experiments or recovery after a failed change. Do not use for routine proposal recovery, ordinary backups, remote data, or crash diagnosis.
---

# Recover Machine State with Jiritsu

A checkpoint records its reason, providers, captured scope, restore limits, and related proposal.

A checkpoint is not a complete backup.

## Related skills

- Load `jiritsu-change` for a supported proposal change. Proposal promotion creates its own linked configuration checkpoint.
- Load `omarchy` for current Omarchy system behavior and post-restore verification.
- Load `jiritsu-observe` for machine facts and workload assessment after a restore.
- If the task starts with a core dump or crash notification, load `diagnose-crash`.

Do not create a second checkpoint for routine proposal promotion.

## Inspect recovery providers

1. If the broker is available, run `jiritsu-broker catalog --pretty`.
2. Use `checkpoint.inspect` through the broker for provider discovery.
3. If the broker is absent, run `jiritsu-checkpoints inspect --pretty`.
4. Read the selected provider, source, provider limit, and fallback errors.

The broker exposes checkpoint inspection and reads. It does not expose checkpoint creation or restore in the first contract.

Use this request shape for broker inspection:

```json
{
  "schema_version": "1.0",
  "request_id": "checkpoint-inspect-1",
  "actor": "codex",
  "operation": "checkpoint.inspect",
  "arguments": {}
}
```

## Create an independent checkpoint

1. Describe the exact reason and affected scope.
2. Select user configuration paths relative to `$XDG_CONFIG_HOME`.
3. Do not include overlapping paths.
4. Create a small TOML policy for the selected paths.
5. Select `--system auto`, `required`, or `off` from the recovery requirement.
6. Run `jiritsu-checkpoints create` with `--dry-run` first.
7. Read the provider plan, selected scopes, and all unavailable scopes.
8. Inspect each policy path under the selected configuration root.
9. Record whether each path is present, missing, a file, a directory, or a symbolic link.
10. Run the same command without `--dry-run` only after the plan and paths are correct.
11. Record the checkpoint ID and each unrecoverable effect.

Use this policy shape:

```toml
schema_version = "1.0"
include = [
  "hypr",
  "omarchy/shell.json",
]
```

Do not use `~`, absolute paths, `.` components, or `..` components in the policy.

The first dry-run contract does not enumerate captured entries. Do the read-only path inspection before checkpoint creation.

If the change cannot proceed safely without a system snapshot, use `--system required`. Do not lower this requirement to avoid authority.

System snapshot creation uses `sudo snapper` and needs an interactive terminal. If no terminal exists, report the required user action.

## Restore user configuration

CAUTION: A restore can replace or remove each selected configuration path.

1. If the broker is available, read the record through `checkpoint.query`.
2. If the broker is absent, run `jiritsu-checkpoints show CHECKPOINT_ID --pretty`.
3. Read the captured paths, recorded configuration root, status, warnings, and restore history.
4. Run `jiritsu-checkpoints restore CHECKPOINT_ID --scope user-config --pretty`.
5. Read the plan before you request permission to apply it.
6. Make sure that the checkpoint ID, scope, configuration root, and paths are correct.
7. Run the same command with `--apply` only after explicit user approval.
8. Record the pre-restore backup path from the result.
9. Verify the restored behavior with the applicable skill.
10. Assess the relevant workloads with `jiritsu-observe`.

Do not add `--config-root` unless the user selected a different restore target.

## Restore the system root

1. Load the `omarchy` skill before a system restore.
2. Run `jiritsu-checkpoints restore CHECKPOINT_ID --scope system --pretty`.
3. Read the exact snapshot ID and required boot action.
4. Do not reboot or apply a system restore without explicit user approval.
5. Start the recorded snapshot from the Limine boot menu.
6. After that snapshot starts, run the restore command with `--apply`.
7. If the active snapshot ID differs, stop and report `action_required`.
8. After the next boot, observe machine facts and assess critical workloads.

If Omarchy is unavailable, the module can select `sudo snapper rollback`. Report the required reboot and bootloader verification.

## Limits

A checkpoint cannot restore remote actions, disclosed secrets, hardware damage, or user data outside its policy.

It also cannot guarantee a consistent database snapshot while that database writes.
