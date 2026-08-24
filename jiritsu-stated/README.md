# `jiritsu-stated`

`jiritsu-stated` gives people and agents a structured view of the current machine.

The module only observes the machine. It does not recommend, approve, or apply changes.

## Initial contract

The first version reports these fact groups:

| Group | Facts |
| --- | --- |
| System | Hostname, operating system, kernel, and Omarchy version |
| Packages | Installed package names and exact versions |
| Services | The systemd manager state and running system services |
| Hardware | CPU identity and memory totals |
| Networks | Active Omarchy network state, interfaces, and addresses |
| Snapshots | Snapper configurations and the active root subvolume |

Run `jiritsu-stated catalog` to get the complete fact catalog without machine probes.

Each fact contains its value, source, observation time, and age. The `fixture` field identifies replayed source data.

The output schema has version `1.0`. A new collection method will not change the meaning of an existing fact.

## Run a live query

Run the command from this module directory:

```bash
./bin/jiritsu-stated query system hardware --pretty
```

An exact fact ID selects one fact:

```bash
./bin/jiritsu-stated query system.omarchy.version --pretty
```

A group name selects all facts in that group. If you omit selectors, the command collects all facts.

The package also defines a standard Python command:

```bash
python -m pip install .
jiritsu-stated query snapshots --pretty
```

The development command does not install files outside this module.

## Read the response

Every query returns one JSON object. This example omits some fields:

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "collected_at": "2026-08-24T12:00:05Z",
  "query": {"selectors": ["system.omarchy.version"]},
  "facts": {
    "system.omarchy.version": {
      "value": "4.0.0-1",
      "source": {
        "id": "omarchy.version",
        "kind": "command",
        "locator": "omarchy version"
      },
      "observed_at": "2026-08-24T12:00:05Z",
      "age_seconds": 0.0,
      "fixture": false
    }
  },
  "errors": []
}
```

`status` is `ok`, `partial`, or `error`. A partial result keeps successful facts and gives an error for each unavailable fact.

The live provider caches each source for one query. Thus, facts from one source use the same payload and observation time.

## Replay source payloads

A fixture contains captured command output and file content. The fixture provider sends this content through the production parsers.

```bash
./bin/jiritsu-stated query --fixture tests/fixtures/healthy.json --pretty
```

Each fixture source uses its stable source ID:

```json
{
  "schema_version": "1.0",
  "sources": {
    "omarchy.version": {
      "kind": "command",
      "stdout": "4.0.0-test\n",
      "exit_code": 0,
      "observed_at": "2026-08-24T12:00:01Z"
    }
  }
}
```

File sources use `content` instead of `stdout`. Set a nonzero `exit_code` or an `error` value to reproduce a source error.

The fixture timestamp must include a timezone. The command calculates `age_seconds` from this timestamp.

## Error behavior

Every query writes a JSON response, including request and fixture errors.

| Exit status | Meaning |
| --- | --- |
| `0` | All selected facts are present |
| `1` | No selected fact is available |
| `2` | Some selected facts are present |
| `64` | The request or selector is invalid |
| `65` | The fixture is missing or invalid |

Source errors include a stable code, a message, the fact ID, and source details. The command does not hide successful facts.

Live command probes stop after five seconds by default. Use `--timeout SECONDS` to change this limit.

## Sources and boundaries

The module uses supported Omarchy commands for Omarchy and active network facts. It uses standard Linux interfaces for other facts.

All live probes are read-only. The module does not use `sudo`, change files, or start services.

The snapshot facts report Snapper configurations and the active root subvolume. The module identifies a root that uses a Snapper snapshot.

The first version does not request privileged snapshot contents.

The first version has no cache between command invocations. Later versions can add a short cache without changing the fact schema.

## Development

Run the focused test suite:

```bash
python -m unittest discover -s tests -v
```

The tests cover complete fixture data, group selection, malformed data, missing sources, source failures, and request errors.

## Place in Jiritsu

`jiritsu-stated` is the observation layer of Jiritsu. Other modules use these facts instead of model memory or machine assumptions.

`jiritsu-workload` can assess important capabilities with these facts. `jiritsu-proposals` can record the state before a change.

`jiritsu-broker` can expose selected facts to agents. A person can use `jiritsu-stated` without the other modules.
