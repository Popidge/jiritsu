# `jiritsu-checkpoints`

`jiritsu-checkpoints` creates identifiable recovery points before a machine change.

The module records the reason, related proposal, providers, captured scope, and restore limits. It works without the other Jiritsu modules.

## First version

The first version provides these functions:

- Inspect the available snapshot and user-configuration providers.
- Create read-only Snapper snapshots with stable checkpoint metadata.
- Capture selected user-configuration paths through an explicit policy.
- List checkpoints and show the full contents of one checkpoint.
- Restore selected user configuration after a separate plan operation.
- Restore the root snapshot through the Omarchy boot workflow.

The command result schema and checkpoint schema have version `1.0`.

## Inspect the providers

Run this command before the first checkpoint:

```bash
./bin/jiritsu-checkpoints inspect --pretty
```

The command reads snapshot facts from `jiritsu-stated` by default. If that command is unavailable, direct read-only probes use Snapper and `findmnt`.

The result reports the selected provider, source, and fallback errors. Use `--state-source direct` to bypass `jiritsu-stated` for one command.

This Omarchy installation has one important provider limit. `omarchy snapshot create` does not return the new Snapper snapshot IDs.

Thus, the module uses `sudo snapper` for snapshot creation. The metadata records this Linux fallback and the reason for it.

## Create a system checkpoint

Run this command from the module directory:

```bash
./bin/jiritsu-checkpoints create \
  --reason "Before the graphics driver experiment" \
  --proposal proposal-20260824-a \
  --pretty
```

The command asks `sudo` for authority in the current terminal. Then it creates one read-only snapshot for each Snapper configuration.

Each snapshot has the cleanup algorithm `number`. The command runs the configured number cleanup after creation.

The `--system` option has these values:

| Value | Behavior |
| --- | --- |
| `auto` | Create system snapshots when Snapper is available. Continue with user configuration when Snapper is unavailable. |
| `required` | Stop before capture when Snapper is unavailable. |
| `off` | Do not create a system snapshot. |

The default value is `auto`. A checkpoint must contain a system snapshot or an explicit user-configuration capture.

Use `--dry-run` to inspect the complete plan. A dry run does not create a snapshot, copy a file, or write a checkpoint record.

## Capture user configuration

A Btrfs root snapshot does not capture this machine's separate `/home` subvolume. Use a policy to select important user configuration.

The policy is a TOML file with this format:

```toml
schema_version = "1.0"
include = [
  "hypr",
  "omarchy/shell.json",
  "ghostty/config",
]
```

Each path is relative to `$XDG_CONFIG_HOME`. The default root is `~/.config`.

Create a checkpoint with this policy:

```bash
./bin/jiritsu-checkpoints create \
  --reason "Before changing the desktop configuration" \
  --policy ./desktop-checkpoint.toml \
  --pretty
```

Use `--config-root PATH` to select a different configuration root. This option is also useful for deterministic integration tests.

The policy rules are:

- Use normalized relative paths only.
- Do not use `~`, absolute paths, `.` components, or `..` components.
- Do not select duplicate or overlapping paths.
- Select files, directories, or symbolic links only.
- Do not select a tree that contains sockets, devices, or other special nodes.

The capture records a selected path that does not exist. A restore removes that path if it exists at restore time.

## Read checkpoint records

List the known checkpoints:

```bash
./bin/jiritsu-checkpoints list --pretty
```

Show one complete record:

```bash
./bin/jiritsu-checkpoints show cp-20260824T120000Z-a1b2c3 --pretty
```

The full record contains these fields:

- The checkpoint ID, status, reason, and creation time.
- The related proposal ID, when supplied.
- The provider selection and discovery source.
- The Snapper configuration, subvolume, and snapshot ID.
- The user-configuration policy, digest, and captured path state.
- The recoverable state and the state that is not recoverable.
- The errors, warnings, and restore history.

The default state directory is `$XDG_STATE_HOME/jiritsu/checkpoints`. The fallback is `~/.local/state/jiritsu/checkpoints`.

Set `JIRITSU_CHECKPOINTS_STATE_DIR` to change the directory. You can also use `--state-dir` for one command.

The store uses mode `0700`. Each metadata file uses mode `0600`, and each write replaces the old record atomically.

## Restore user configuration

First, inspect the restore plan:

```bash
./bin/jiritsu-checkpoints restore CHECKPOINT_ID --scope user-config --pretty
```

The plan does not change a file. It shows each selected path and the target configuration root.

CAUTION: The next command replaces or removes each selected path. Make sure that the plan has the correct checkpoint and configuration root.

Apply the restore:

```bash
./bin/jiritsu-checkpoints restore CHECKPOINT_ID --scope user-config --apply --pretty
```

Before replacement, the module copies the current selected paths into the checkpoint directory. The result gives the path of this pre-restore backup.

If a copy operation fails, the module tries to restore all paths from this backup. The error reports each backup-restore error.

## Restore the system root

First, inspect the system restore plan:

```bash
./bin/jiritsu-checkpoints restore CHECKPOINT_ID --scope system --pretty
```

Omarchy restores a root snapshot after that snapshot starts from the Limine boot menu. The plan gives the exact Snapper snapshot ID.

Use this procedure on Omarchy:

1. Reboot the machine.
2. Select the recorded Snapper snapshot in the Limine boot menu.
3. Start the system from that snapshot.
4. Apply the checkpoint restore:

```bash
./bin/jiritsu-checkpoints restore CHECKPOINT_ID --scope system --apply --pretty
```

If the active snapshot ID is incorrect, the command stops with `action_required`. It does not start a different restore.

If Omarchy is unavailable, the apply operation uses `sudo snapper rollback`. The result tells you to reboot and examine the bootloader configuration.

The first version restores only a Snapper configuration for subvolume `/` automatically. It reports non-root snapshots as manual restore items.

## Status and exit values

A checkpoint has one of these status values:

| Status | Meaning |
| --- | --- |
| `planned` | A dry run selected the scope, but did not write state. |
| `ready` | All selected capture operations succeeded. |
| `partial` | At least one selected scope succeeded and one scope failed. |
| `failed` | No selected scope succeeded. |

Every command writes one JSON object, including command and provider errors.

| Exit value | Meaning |
| --- | --- |
| `0` | The operation succeeded, or the requested plan is ready. |
| `1` | The operation failed. |
| `2` | The checkpoint is partial, or a system restore needs the boot step. |
| `64` | The request or policy is invalid. |
| `65` | Stored data or an input file is invalid. |

## Recovery limits

A checkpoint is not a complete safety boundary. It cannot recover these effects:

- A remote action or deletion.
- A disclosed secret.
- Hardware damage.
- User data outside the explicit policy.
- A consistent database state when the database writes during snapshot creation.

Snapper cleanup can remove an old snapshot while its Jiritsu metadata remains. The checkpoint record identifies the created snapshot, but it does not prevent backend retention.

## Development

Run the focused test suite:

```bash
python -m unittest discover -s tests -v
```

The tests cover stated discovery, direct fallback, identifiable Snapper creation, dry runs, policies, records, and transactional user-configuration restore.

The test suite does not create a live Snapper snapshot or start a system restore. The live integration probe uses `inspect` and `create --dry-run`.

## Place in Jiritsu

`jiritsu-checkpoints` is the recovery layer of Jiritsu. It gives `jiritsu-proposals` a recovery point before an approved change starts.

`jiritsu-stated` supplies snapshot facts. `jiritsu-proposals` creates linked user-configuration checkpoints before promotion. `jiritsu-broker` exposes provider inspection and checkpoint reads to agents.

A person can create, inspect, and restore checkpoints without the proposal or broker modules.
