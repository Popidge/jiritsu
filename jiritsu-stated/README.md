# `jiritsu-stated`

`jiritsu-stated` is an always-running, read-only machine-state daemon for Omarchy.

The Rust daemon collects facts before a query arrives. The CLI reads this cache through a bounded Unix-socket protocol.

If the daemon is unavailable, the CLI collects the same facts directly. This fallback keeps the module useful by itself.

## Current contract

The Rust version keeps the Python `1.0` fact schema and all 13 fact IDs:

| Group | Facts |
| --- | --- |
| System | Hostname, operating system, kernel, and Omarchy version |
| Packages | Installed package names and exact versions |
| Services | The systemd manager state and running system services |
| Hardware | CPU identity and memory totals |
| Networks | Active Omarchy network state, interfaces, and addresses |
| Snapshots | Snapper configurations and the active root subvolume |

Run the catalog command to get the complete fact list without machine probes:

```bash
./bin/jiritsu-stated catalog --pretty
```

Each fact contains its value, source, observation time, age, and fixture status.

## Architecture

The CLI uses this provider order:

```text
jiritsu-stated query
        |
        v
/run/jiritsu/stated.sock
        |
        v
jiritsu-stated.service cache

If the socket fails:

jiritsu-stated query
        |
        v
direct Rust collectors
```

The CLI reports this selection in `runtime.selected_provider`. The value is `daemon`, `direct`, `fixture`, or `none`.

`runtime.fallback_errors` contains each daemon connection error before a direct fallback.

## Build the Rust command

Build the development command from this module directory:

```bash
cargo build
```

The source-tree wrapper selects `target/debug/jiritsu-stated` after this build:

```bash
./bin/jiritsu-stated query system hardware --pretty
```

Before the Rust build exists, the wrapper selects the Python reference command.

Set `JIRITSU_STATED_IMPLEMENTATION=python` to select Python explicitly.

## Run a development daemon

Start the daemon on a user-owned socket:

```bash
./target/debug/jiritsu-stated serve \
  --socket "$XDG_RUNTIME_DIR/jiritsu/stated.sock"
```

Use another terminal for the query:

```bash
JIRITSU_STATED_SOCKET="$XDG_RUNTIME_DIR/jiritsu/stated.sock" \
  ./bin/jiritsu-stated query --pretty
```

Stop the daemon with `Ctrl+C`. The daemon removes its socket during a normal shutdown.

## Query behavior

An exact fact ID selects one fact:

```bash
./bin/jiritsu-stated query system.omarchy.version --pretty
```

A group name selects all facts in that group:

```bash
./bin/jiritsu-stated query services networks --pretty
```

If you omit selectors, the command returns all facts.

Use `--direct` to bypass the daemon:

```bash
./bin/jiritsu-stated query system --direct --pretty
```

Use `--require-daemon` to disable direct fallback:

```bash
./bin/jiritsu-stated query system --require-daemon --pretty
```

Set `JIRITSU_STATED_SOCKET` or use `--socket` to select another socket.

## Cache and refresh behavior

The daemon collects every fact during startup. It does not serve an empty initial cache.

The cache has a monotonic `epoch`. A fact value, source, availability, or fixture status change advances this epoch.

A new observation time alone does not advance the epoch.

Filesystem events refresh these sources:

| Watched path | Facts |
| --- | --- |
| `/etc/hostname` | `system.hostname` |
| `/etc/os-release` | `system.os` |
| `/var/lib/pacman/local` | `packages.installed` |
| `/etc/snapper/configs` | `snapshots.configurations` |

The daemon refreshes dynamic facts every 15 seconds. These facts cover services, memory, networks, and the active root subvolume.

The daemon refreshes all facts every 300 seconds. This refresh recovers from a missed filesystem event.

Use the `serve` refresh options to change these periods during development.

If a refresh fails, the cache keeps the last successful fact. The fact age continues to increase.

`runtime.cache.last_refresh_errors` reports the latest refresh errors. An unavailable initial fact remains in the normal `errors` array.

## Replay source payloads

A fixture contains captured command output and file content. Rust and Python send this content through their production parsers.

Run one direct fixture query:

```bash
./bin/jiritsu-stated query \
  --fixture tests/fixtures/healthy.json \
  --pretty
```

Run a fixture-backed daemon:

```bash
./target/debug/jiritsu-stated serve \
  --socket "$XDG_RUNTIME_DIR/jiritsu/stated-test.sock" \
  --fixture tests/fixtures/healthy.json
```

The daemon watches the fixture directory. A fixture change refreshes all facts and can advance the cache epoch.

The fixture schema remains `1.0`. Command sources use `stdout`, and file sources use `content`.

The fixture limit is 16 MiB. Each fixture timestamp must include a timezone.

## Protocol

The daemon accepts one JSON request on each Unix-socket connection. The client closes its write half after the request.

The daemon closes the connection after one JSON response. It does not keep client sessions.

The request limit is 64 KiB. The response limit is 16 MiB, and the default request timeout is two seconds.

Read [the protocol document](docs/protocol.md) for the complete request and cache contracts.

## Install the system service

The repository includes a hardened systemd service. Omarchy does not provide a general service installation command.

Build the release command:

```bash
cargo build --release
```

Install the command, service, and documentation:

```bash
sudo install -Dm0755 target/release/jiritsu-stated \
  /usr/local/libexec/jiritsu-stated
sudo install -Dm0644 systemd/jiritsu-stated.service \
  /etc/systemd/system/jiritsu-stated.service
sudo install -Dm0644 README.md \
  /usr/local/share/doc/jiritsu-stated/README.md
```

Reload systemd and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now jiritsu-stated.service
```

Examine the service and its log:

```bash
systemctl status jiritsu-stated.service
journalctl -u jiritsu-stated.service
```

Then query the default socket:

```bash
./target/release/jiritsu-stated query --require-daemon --pretty
```

CAUTION: Do not install this unit on an untrusted multi-user machine. Its mode `0666` socket exposes reported state to every local user.

The service uses a dynamic non-root user. It has no capabilities and has read-only access to the operating-system hierarchy.

The service also restricts devices, namespaces, process visibility, address families, and system calls.

## Python reference

The Python implementation remains the reference for the `1.0` fact contract:

```bash
./bin/jiritsu-stated-python query \
  --fixture tests/fixtures/healthy.json \
  --pretty
```

The Rust differential tests compare all stable fixture fields and the complete catalog against Python.

The Python code does not run inside the Rust daemon or its direct fallback.

## Error behavior

Every query writes one JSON response, including request, provider, source, and fixture errors.

| Exit status | Meaning |
| --- | --- |
| `0` | All selected facts are present |
| `1` | No selected fact is available, or a required daemon failed |
| `2` | Some selected facts are present |
| `64` | The request or selector is invalid |
| `65` | The fixture is missing or invalid |

A partial result keeps successful facts. Each unavailable fact has a stable error code and source details.

The daemon limits concurrent connections to 64. It rejects malformed and oversized requests without stopping.

Each source command has a five-second timeout by default. A timed-out child process stops before the next refresh.

At startup, the daemon refuses a non-socket path or an active daemon socket. It removes only a stale socket.

A fatal `serve` error writes its stable code to standard error and returns a nonzero status.

## Safety boundaries

All collectors are read-only. The daemon does not use `sudo`, change files, or start services.

The daemon starts each source command with an argument array. It does not use a shell.

The first Rust version still starts commands for systemd, network, package, hardware, and snapshot facts.

Later versions can replace these sources with D-Bus, netlink, udev, and mount events. These changes must preserve fact meanings.

The daemon is not an authority boundary. `jiritsu-broker` still controls agent access to Jiritsu operations.

## Development

Run the Rust tests:

```bash
cargo test --all-targets
```

Run the Rust static checks:

```bash
cargo clippy --all-targets -- -D warnings
cargo fmt --all -- --check
```

Run the Python reference tests:

```bash
python -m unittest discover -s tests -v
```

The tests cover direct collection, fallback, protocol limits, cache epochs, file events, socket safety, structured errors, and Rust/Python parity.

## Development direction

Version `0.2` completes the socket, direct fallback, long-lived cache, epochs, and initial filesystem events.

These later stages are plans. They are not current capabilities:

| Stage | Planned change |
| --- | --- |
| `0.3` | Read systemd state and signals through D-Bus. |
| `0.4` | Read network state and events through netlink. |
| `0.5` | Read device changes through udev. |
| `0.6` | Watch mounts, filesystems, and snapshot state. |
| `0.7` | Add bounded client subscriptions for fact patterns. |

Each stage must keep the `1.0` fact meanings or introduce an explicit protocol version.

## Place in Jiritsu

`jiritsu-stated` is the observation layer of Jiritsu. Other modules use these facts instead of memory or machine assumptions.

`jiritsu-workload`, `jiritsu-proposals`, and `jiritsu-broker` continue to use the unchanged `query` contract.
