# `jiritsu-skills`

`jiritsu-skills` teaches agents how to use Omarchy and Jiritsu correctly.

## What It Is

An agent needs more than tool names. It needs clear knowledge about supported methods, important limits, verification, and recovery.

`jiritsu-skills` packages that knowledge as focused agent skills. Each skill covers one task or system area and stays readable for people.

A skill supplies guidance, not authority. The broker and the operating system still control permissions and machine changes.

## Goals

The module has four goals:

- It gives agents short and accurate instructions for common machine tasks.
- It puts Omarchy methods before lower-level Linux methods.
- It identifies important state, invariants, verification steps, and recovery paths.
- It keeps agent knowledge versioned, inspectable, and replaceable.

## How It Works

Each skill describes its purpose, required context, supported actions, and limits. A skill can include small scripts or references where text is insufficient.

The skills read live facts through `jiritsu-stated` where possible. They create durable changes through `jiritsu-proposals` instead of hidden shell actions.

Skills use the public Omarchy interface first. They use user configuration next and use lower-level Linux interfaces only for a genuine gap.

## Place in Jiritsu

`jiritsu-skills` is the knowledge layer of Jiritsu. It connects agent reasoning to the safe interfaces that the other modules provide.

The module also has standalone value. An agent can use an installed skill before the complete Jiritsu stack exists.

The skills provide the common guide for observation, proposals, workload protection, checkpoints, and broker operations.

## First Skills

The first version contains five discoverable skills:

| Skill | Purpose |
| --- | --- |
| [`jiritsu-observe`](skills/jiritsu-observe/SKILL.md) | Read source-aware machine facts and workload health. |
| [`jiritsu-change`](skills/jiritsu-change/SKILL.md) | Govern supported user-configuration changes through proposals. |
| [`jiritsu-workloads`](skills/jiritsu-workloads/SKILL.md) | Define and assess local workload contracts. |
| [`jiritsu-recover`](skills/jiritsu-recover/SKILL.md) | Create and restore system or user-configuration checkpoints. |
| [`jiritsu-broker-admin`](skills/jiritsu-broker-admin/SKILL.md) | Administer policy, approvals, principals, and audit records. |

These skills use the installed `omarchy` and `diagnose-crash` skills as neighboring authorities.

The Jiritsu skills do not copy Omarchy commands, configuration formats, or troubleshooting procedures. They load the applicable Omarchy skill when a task needs that knowledge.

Jiritsu then supplies the glue around that task:

```text
observe -> describe -> propose -> checkpoint -> apply -> verify -> commit or roll back
```

## Shared Behavior

The skills use `jiritsu-broker` for each operation that its live catalog exposes.

A broker denial is final for that request. The skills never bypass a denial through a direct module command.

When the broker is absent, read-only skills can use the standalone module interfaces. The change skill keeps a separate approval step in standalone mode.

Each result keeps the selected provider, source, status, and fallback errors visible.

External approval requires a channel that the requesting agent cannot write. The first version does not provision this operating-system trust boundary.

## Fresh-Context Trial

The first skill set passed a fresh-context agent trial. The agent used the installed guidance without prior Jiritsu conversation context.

This trial exercises skill discovery and usability. It is not a security evaluation of the broker or operating-system boundary.

## Development Install

Install the source-tree skills as user-owned links:

```bash
./jiritsu-skills/bin/jiritsu-skills-install --dry-run
./jiritsu-skills/bin/jiritsu-skills-install
```

The default target is `${CODEX_HOME:-$HOME/.codex}/skills`. Use `--target PATH` for a different agent skill directory.

The installer stops when a target name contains another file or link. It does not change packaged Omarchy skills under `/usr/share/omarchy/`.

Start a new agent task after installation. The active task does not reload its skill catalog.

## Development

Run the focused validation suite from the repository root:

```bash
python -m unittest discover -s jiritsu-skills/tests -v
```

The tests validate skill metadata, local references, data templates, installation, portable structure, and ambiguous modal language.
