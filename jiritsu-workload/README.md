# `jiritsu-workload`

`jiritsu-workload` describes the capabilities that make a machine useful for its local roles.

A healthy Linux system can still fail its purpose. A desktop needs a working session. An agent workstation needs its development tools.

This module records these expectations as workload contracts. It assesses the machine, but it does not repair the machine.

## First version

The first version provides these functions:

- Load readable TOML workload contracts.
- Separate critical capabilities from useful capabilities.
- Read machine facts from `jiritsu-stated` by default.
- Use direct probes when a stated fact is unavailable.
- Return stable JSON for people, agents, and scripts.
- Create or update user contracts with one atomic operation.

The command result schema has version `1.1`. Each check identifies `jiritsu-stated` or `direct_probe` as its source.

## Default contracts

The package contains two default contracts:

| Contract | Critical capabilities | Useful capabilities |
| --- | --- | --- |
| `omarchy-desktop` | Omarchy base, graphical session | Desktop audio, desktop notifications |
| `agent-development` | Agent runtime, version control | Source search, Python runtime |

The defaults contain five stated-backed checks and three session-local direct checks. Each stated-backed default has a direct fallback.

User contracts take precedence over default contracts. A user contract overrides a default when both contracts have the same ID.

The standard user directory is `$XDG_CONFIG_HOME/jiritsu/workloads.d`. The fallback is `~/.config/jiritsu/workloads.d`.

Set `JIRITSU_WORKLOAD_CONFIG_DIR` to select a different directory. You can also use `--config-dir` for one command.

## Machine-state source

The `assess` command requests all required facts in one `jiritsu-stated query`. Duplicate fact requests occur only once in this query.

The module finds the stated executable in this order:

1. The `--stated-command` option.
2. The `JIRITSU_STATED_COMMAND` environment variable.
3. The `jiritsu-stated` command in `PATH`.
4. The sibling development command in this repository.

If the executable is unavailable, each `stated_fact` check runs its direct fallback. A failed or invalid stated query has the same behavior.

If a partial query omits one fact, only checks for that fact use their fallbacks. Checks for returned facts still use stated.

A stated fact that does not meet a requirement produces a normal failed check. The module does not replace a valid negative result with a fallback.

Use direct probes for one assessment to diagnose stated behavior:

```bash
./bin/jiritsu-workload assess --state-source direct --pretty
```

If a contract has no `stated_fact` checks, the module does not start `jiritsu-stated`.

## Run an assessment

Run the development command from this module directory:

```bash
./bin/jiritsu-workload assess --pretty
```

Select one or more workloads by ID:

```bash
./bin/jiritsu-workload assess omarchy-desktop --pretty
```

The development command does not install files outside this module.

The package also defines a standard Python command:

```bash
python -m pip install ./jiritsu-workload
jiritsu-workload assess --pretty
```

## Query contracts

List the resolved contracts and their source files:

```bash
./bin/jiritsu-workload list --pretty
```

Query one complete contract:

```bash
./bin/jiritsu-workload query agent-development --pretty
```

If you omit the IDs, `query` returns all resolved contracts.

Get the active user directory:

```bash
./bin/jiritsu-workload config-path --pretty
```

## Define a contract

Create a TOML file with this structure:

```toml
schema_version = "1.0"
id = "local-backup"
title = "Local backup"
description = "The tools and storage path for local backups are available."

[[capabilities]]
id = "backup-tool"
title = "Backup tool"
description = "Restic is installed."
importance = "critical"

[[capabilities.checks]]
id = "restic-command"
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

Validate the file before you apply it:

```bash
./bin/jiritsu-workload validate local-backup.toml --pretty
```

Apply the file to the user directory:

```bash
./bin/jiritsu-workload apply local-backup.toml --pretty
```

The `apply` command creates a new contract or updates the matching user contract. It validates the complete file before it writes.

The command writes the file atomically and sets mode `0600`. It does not change the source file.

To modify a contract, edit its TOML file in the user directory. Then run `validate` and `assess` again.

## Contract rules

Each contract must contain these root fields:

| Field | Meaning |
| --- | --- |
| `schema_version` | The contract schema. The first version accepts `"1.0"`. |
| `id` | A stable lowercase ID with a maximum of 64 characters. |
| `title` | A short display name. |
| `description` | The purpose of the workload. |
| `capabilities` | One or more capability tables. |

Each capability must have an ID, title, description, importance, and one or more checks.

The `importance` value is `critical` or `useful`. IDs can contain lowercase letters, numbers, dots, underscores, and hyphens.

IDs must be unique inside their parent contract or capability. Two user files cannot define the same contract ID.

Unknown fields cause a validation error.

## Check types

The first version supports one stated check type and five direct check types:

| Type | Required fields | Optional fields | Pass condition |
| --- | --- | --- | --- |
| `stated_fact` | `fact` | `path`, `operator`, `expected`, `fallback` | The selected fact value meets the requirement. |
| `command_available` | `command` | None | The executable occurs in `PATH`. |
| `command` | `command` array | `expected_exit`, `stdout`, `timeout_seconds` | The exit status and output meet the requirements. |
| `environment` | `name` | `nonempty`, `equals` | The variable meets the presence or value requirement. |
| `path` | `path` | `kind` | The expanded path has the required kind. |
| `systemd_unit` | `unit` | `scope`, `state` | The unit has the required systemd state. |

### Stated fact checks

The `fact` field contains an exact fact ID from the `jiritsu-stated` catalog.

The optional `path` selects fields inside the fact value. Use a dot between fields and `*` for each item in an array.

For example, `packages.*.name` selects all package names from `packages.installed`.

The default operator is `exists`. The supported operators are:

| Operator | Requirement |
| --- | --- |
| `exists` | The fact and selected path exist. |
| `nonempty` | The selected value is not empty. |
| `equals` | The selected value equals `expected`. |
| `not_equals` | The selected value does not equal `expected`. |
| `contains` | The selected string, array, or object contains `expected`. |
| `at_least` | The selected number is not less than `expected`. |
| `at_most` | The selected number is not more than `expected`. |

The `expected` field is required for all operators except `exists` and `nonempty`.

The optional `fallback` table contains one direct check type and its fields. It must not contain another `stated_fact` check.

If no fallback exists, an unavailable fact produces a check error.

### Direct checks

The default `expected_exit` is `0`. The `stdout` value is `any`, `nonempty`, or `empty`.

The default command timeout is five seconds. The `assess --timeout` option changes it for checks without a local timeout.

The same option sets the timeout for each source inside the stated query.

The path `kind` value is `any`, `file`, `directory`, or `executable`. A path check expands a leading `~`.

The systemd `scope` value is `user` or `system`. The default is `user`.

The systemd `state` value is `active`, `enabled`, `failed`, or `inactive`. The default is `active`.

An environment result never contains the variable value. Command output has a 500-character limit in the result.

## Assessment rules

A check result is `pass`, `fail`, or `error`. An execution timeout produces an `error`.

A capability passes only when all its checks pass. A failed check makes the capability fail.

If no check fails, a check error makes the capability result `error`.

A workload has one of these results:

| Result | Meaning |
| --- | --- |
| `healthy` | All capabilities pass. |
| `degraded` | All critical capabilities pass, but a useful capability does not pass. |
| `unhealthy` | A critical capability does not pass. |

An assessment of multiple workloads uses the least healthy workload result.

The `machine_state.source` value is `jiritsu-stated`, `direct_probes`, or `hybrid`. A hybrid assessment uses both sources.

The `machine_state` object also counts stated checks, direct probes, and fallbacks. Its stated details contain the query status and requested facts.

## Read the JSON result

Every assessment returns one JSON object. This example omits check details:

```json
{
  "schema_version": "1.1",
  "status": "healthy",
  "assessed_at": "2026-08-24T12:00:05Z",
  "machine_state": {
    "source": "hybrid",
    "jiritsu_stated": "used",
    "stated": {
      "status": "used",
      "requested_facts": [
        "packages.installed",
        "system.omarchy.version"
      ],
      "fact_count": 2,
      "query_status": "ok"
    },
    "stated_check_count": 5,
    "direct_probe_count": 3,
    "fallback_check_count": 0
  },
  "summary": {
    "workload_count": 2,
    "healthy": 2,
    "degraded": 0,
    "unhealthy": 0
  },
  "workloads": [],
  "errors": []
}
```

Each workload contains its resolved contract source. Each capability contains all check results, sources, messages, durations, and bounded details.

## Exit status

| Exit status | Meaning |
| --- | --- |
| `0` | The request succeeded, or all assessed workloads are healthy. |
| `1` | At least one assessed workload is unhealthy. |
| `2` | No workload is unhealthy, but at least one workload is degraded. |
| `64` | The request or workload selector is invalid. |
| `65` | A contract or contract directory is invalid. |

## Safety and boundaries

All packaged checks and stated queries are read-only. The module does not use `sudo`, start services, or repair failed capabilities.

The `command` check starts its argument array directly. It does not use shell interpretation.

A custom command still runs with the current user permissions. Apply contracts only from sources that you trust.

Do not put secrets in command output. The result can contain the first 500 characters of standard output and standard error.

The package has no required Python dependency on `jiritsu-stated`. The command boundary keeps both modules useful by themselves.

The direct fallbacks preserve standalone assessment when `jiritsu-stated` is not installed.

## Development

Run the focused test suite:

```bash
python -m unittest discover -s tests -v
```

The tests cover stated success, partial results, invalid responses, missing executables, direct fallbacks, and valid negative facts.

They also cover loading, selection, overrides, validation, user writes, result severity, and direct-only contracts.

## Place in Jiritsu

`jiritsu-workload` gives Jiritsu a local definition of success. This definition helps the project protect the functions that matter on this machine.

`jiritsu-proposals` can use workload results during risk assessment and verification. A proposal that breaks a critical workload does not succeed.

`jiritsu-broker` exposes workload health to agents. A person can also assess a workload without agent involvement.
