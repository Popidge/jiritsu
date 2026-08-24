# `jiritsu-checkpoints`

`jiritsu-checkpoints` creates recovery points for machine changes.

## What It Is

Experiments can damage a configuration, package state, or service. A known recovery point makes these experiments safer and easier to understand.

`jiritsu-checkpoints` gives one interface to the recovery mechanisms that the machine already supports. It associates each recovery point with its reason and proposal.

A checkpoint is not a complete safety boundary. It cannot reverse an external action, disclosed secret, remote deletion, or hardware damage.

## Goals

The module has four goals:

- It creates a known recovery point before a machine change.
- It shows what each recovery point contains and why it exists.
- It restores supported state through a clear operation.
- It uses existing Linux storage tools instead of new filesystem technology.

## How It Works

The first version wraps the snapshot support available on this Omarchy installation. Snapper and Btrfs provide the initial foundation where available.

Some important files can sit outside a system snapshot. The module can capture selected user configuration through an explicit policy.

Each checkpoint records its backend, scope, creation time, and related proposal. Restore operations report the state that they can and cannot recover.

## Place in Jiritsu

`jiritsu-checkpoints` is the recovery layer of Jiritsu. It gives `jiritsu-proposals` a safe point before an approved change starts.

`jiritsu-stated` can report available checkpoints and the active snapshot state. `jiritsu-broker` can expose narrow checkpoint operations to agents.

A person can create, inspect, and restore checkpoints without the proposal or broker modules.
