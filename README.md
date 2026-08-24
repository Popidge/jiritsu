# Jiritsu

> Agentic glue for an agent-first Omarchy system.

Jiritsu is an experimental set of small Linux tools for people and autonomous agents. The project adds structured state, intent, recovery, and controlled authority.

The name comes from the Japanese word `自律` (*jiritsu*), which means autonomy or self-direction.

Omarchy remains the opinionated, human-facing system. Jiritsu uses Omarchy methods first and adds agent-focused capabilities around them.

> [!IMPORTANT]
> Jiritsu is in early active development. Interfaces can change, modules can break, and incomplete ideas can disappear.
>
> This project is not ready for production use. It does not have a stable release or a complete installation guide.

## Why Jiritsu Exists

An agent with shell access can act, but it lacks several important system concepts. Shell access alone does not provide safe autonomy.

Jiritsu adds these concepts:

- Agents use current machine facts, not model memory.
- Each machine describes the workloads and capabilities that matter locally.
- Each change starts as durable intent instead of an immediate command.
- Recovery points protect supported machine state before a change starts.
- A broker controls agent authority through narrow operations and deterministic policy.
- Skills teach supported Omarchy methods, but they do not grant permissions.

The complete project follows one path:

```text
observe -> describe -> propose -> checkpoint -> apply -> verify -> commit or roll back
```

Each module also provides value before the complete path exists.

## Modules

| Module | Status | Purpose |
| --- | --- | --- |
| [`jiritsu-stated`](jiritsu-stated/) | Initial version | Reports stable, source-aware facts about the current machine. |
| [`jiritsu-workload`](jiritsu-workload/) | Initial version | Defines local workload contracts and assesses important capabilities. |
| [`jiritsu-proposals`](jiritsu-proposals/) | Manifesto only | Records intended changes, risk, approval, verification, and history. |
| [`jiritsu-checkpoints`](jiritsu-checkpoints/) | Manifesto only | Creates recovery points that relate to machine changes. |
| [`jiritsu-broker`](jiritsu-broker/) | Manifesto only | Exposes narrow Jiritsu operations to agents under deterministic policy. |
| [`jiritsu-skills`](jiritsu-skills/) | Manifesto only | Teaches agents how to use Omarchy and Jiritsu correctly. |

Every module has its own README. That README describes the current contract, boundaries, and development commands for the module.

## What Works Today

`jiritsu-stated` reports system, package, service, hardware, network, and snapshot facts. It returns stable JSON with source and observation details.

The module also replays captured source data through its production parsers. This process gives its important collection paths deterministic tests.

`jiritsu-workload` loads readable TOML contracts and assesses their capabilities. Its direct probes cover commands, environment variables, paths, and systemd units.

The module includes contracts for an Omarchy desktop and an agent development environment. User contracts can override these defaults.

Both modules work independently. Their focused test suites cover their main behavior and important error paths.

## Try the Implemented Modules

Python 3.11 or newer is required. Run these development commands from the repository root:

```bash
./jiritsu-stated/bin/jiritsu-stated query system hardware --pretty
./jiritsu-workload/bin/jiritsu-workload assess --pretty
```

These commands run directly from the source tree. They do not install files outside the module directories.

The module READMEs contain the current usage details. Comprehensive installation instructions will come after the project has stable installation behavior.

## What Comes Next

The next development stages are:

1. `jiritsu-workload` reads `jiritsu-stated` as its primary machine-state source.
2. `jiritsu-proposals` records intended changes as durable objects.
3. `jiritsu-checkpoints` creates and restores recovery points for proposals.
4. Proposal promotion coordinates application, verification, commit, and rollback.
5. `jiritsu-broker` exposes the completed operations through a controlled agent interface.
6. `jiritsu-skills` gives agents focused guidance for the complete system.

The order matters. Jiritsu builds trustworthy operations before it gives those operations to agents.

## Project Principles

Each module follows these principles:

- The module remains useful, understandable, and replaceable by itself.
- The public Omarchy interface is the first choice for machine operations.
- Standard Linux mechanisms fill genuine gaps in Omarchy.
- Structured facts and deterministic probes provide evidence.
- Machine changes stay explicit, narrow, and reversible where possible.
- Focused tests protect important behavior and keep the experiment fast.

Jiritsu is agentic glue, not a new operating system. The project favors tangible experiments over speculative frameworks and premature abstraction.
