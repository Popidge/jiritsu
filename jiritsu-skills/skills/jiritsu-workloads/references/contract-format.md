# Workload Contract Format

Read this reference when you create or change a workload contract.

## Root fields

Each TOML contract contains these root fields:

- `schema_version = "1.0"`
- `id`
- `title`
- `description`
- One or more `capabilities` tables.

IDs contain lowercase letters, numbers, dots, underscores, or hyphens. An ID has a maximum length of 64 characters.

Each capability contains `id`, `title`, `description`, `importance`, and one or more `checks` tables.

Use `critical` or `useful` for `importance`.

## Example

```toml
schema_version = "1.0"
id = "local-backup"
title = "Local backup"
description = "The local backup tools and storage path are available."

[[capabilities]]
id = "backup-tool"
title = "Backup tool"
description = "Restic is installed."
importance = "critical"

[[capabilities.checks]]
id = "restic-package"
type = "stated_fact"
description = "Find the installed Restic package."
fact = "packages.installed"
path = "packages.*.name"
operator = "contains"
expected = "restic"
fallback = { type = "command_available", command = "restic" }

[[capabilities]]
id = "backup-target"
title = "Backup target"
description = "The local backup directory exists."
importance = "useful"

[[capabilities.checks]]
id = "backup-directory"
type = "path"
description = "Find the local backup directory."
path = "~/Backups"
kind = "directory"
```

## Stated fact checks

Set `fact` to an exact fact ID from `jiritsu-stated catalog`.

Use `path` to select nested fields. Use `*` for each array item.

The supported operators are:

- `exists`
- `nonempty`
- `equals`
- `not_equals`
- `contains`
- `at_least`
- `at_most`.

All operators except `exists` and `nonempty` require `expected`.

A fallback contains one direct check. It cannot contain another `stated_fact` check.

## Direct check types

- `command_available` requires `command` as one executable name.
- `command` requires `command` as an argument array.
- `environment` requires `name`.
- `path` requires `path`.
- `systemd_unit` requires `unit`.

The `command` check can also set `expected_exit`, `stdout`, and `timeout_seconds`.

The `path` check can set `kind` to `any`, `file`, `directory`, or `executable`.

The `systemd_unit` check can set `scope` and `state`. The default scope is `user`.

Run `jiritsu-workload validate CONTRACT.toml --pretty` after each edit.
