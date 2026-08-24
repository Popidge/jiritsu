# `jiritsu-proposals`

`jiritsu-proposals` records an intended machine change before that change becomes an action.

## What It Is

An agent can produce shell commands quickly. A command list does not explain intent, risk, expected results, or recovery.

`jiritsu-proposals` represents a change as a durable, structured object. The proposal keeps its purpose, origin, actions, risk, verification, and history together.

The proposal separates the desired change from the conditions that permit its application.

## Goals

The module has four goals:

- It preserves intent and provenance for each machine change.
- It gives each change a clear lifecycle before and after application.
- It makes risk, approval, verification, and recovery visible.
- It keeps the same interface for human and agent use.

## How It Works

A proposal starts as a record of intent and expected effects. Classification adds risk and identifies the required permissions and safeguards.

Approval permits a defined action. Promotion can coordinate a checkpoint, the action, verification, and either commit or rollback.

The first versions support a narrow set of low-risk actions. The module grows through explicit action types, not arbitrary command execution.

## Place in Jiritsu

`jiritsu-proposals` is the change journal and coordination layer of Jiritsu. It turns an agent request into an object that the system can inspect.

The module reads current facts from `jiritsu-stated`. It uses `jiritsu-workload` to protect important capabilities and `jiritsu-checkpoints` to prepare recovery.

`jiritsu-broker` exposes proposal operations to agents. A person can create, inspect, approve, or promote a proposal through the same model.
