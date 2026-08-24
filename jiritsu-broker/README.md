# `jiritsu-broker`

`jiritsu-broker` gives agents a narrow and controlled interface to Jiritsu.

## What It Is

An agent needs useful tools, but it does not need ambient root access. It also does not need direct knowledge of each module implementation.

`jiritsu-broker` exposes selected Jiritsu operations as clear, typed tools. It keeps authority separate from model reasoning and untrusted text.

The broker does not decide the desired outcome. It decides whether a requested operation matches the available authority and policy.

## Goals

The module has four goals:

- It gives agents a small and stable tool surface.
- It grants only the authority that an approved operation requires.
- It records requests, decisions, actions, and results.
- It keeps policy enforcement deterministic and outside the model.

## How It Works

An agent requests a semantic operation, such as reading state or creating a proposal. The broker maps that request to a Jiritsu module.

The broker evaluates the request against local policy and available permissions. Sensitive effects can require explicit approval before the broker continues.

The broker returns structured results and records the operation. A skill, web page, or command output cannot grant new authority to the agent.

## Place in Jiritsu

`jiritsu-broker` is the agent boundary of Jiritsu. It exposes the completed abilities of other modules without hiding their safety rules.

The broker comes after the core modules because it depends on their clear contracts. It does not replace their direct human interfaces.

This boundary lets agents act through Jiritsu without direct access to internal databases, snapshot tools, or unrestricted system commands.
