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

As the stack grows, the skills become the common guide for observation, proposals, workload protection, checkpoints, and broker operations.
