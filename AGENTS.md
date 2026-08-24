# How We Build Jiritsu

Jiritsu is experimental agentic glue for Omarchy. Build tangible ideas quickly, test them on this live Omarchy installation, and expect early designs to change or break. This is not production-ready or enterprise-focused software, and it is not an attempt to build a new operating system.

## Working Principles

- Keep every `jiritsu-*` module useful on its own. A module must have a clear purpose, understandable behaviour, and explicit interfaces with the rest of the stack.
- Preserve module boundaries. Prefer small, replaceable components and stable data contracts over hidden coupling or a large orchestration framework.
- Build the narrowest version that proves the concept. Do not add speculative abstractions, extensibility, compatibility layers, or operational machinery before they solve a demonstrated need.
- Optimise for learning and working software. It is acceptable to revise or discard an approach when the live experiment teaches us something better.
- Describe goals, behaviour, inputs, outputs, side effects, and failure modes in plain English. The code and module documentation should be understandable without knowing the whole Jiritsu architecture.

## Module State and Fallbacks

Before you design or change a module, inspect the repository root, other module code, tests, and README files.

Treat executable code and tests as the source of truth for implemented behaviour. Treat a manifesto or roadmap as intent, not an implemented capability.

Do not copy a fixed module-status list into this file. Get the current status from the repository contents.

Apply these integration rules:

- Use the complete Jiritsu stack as the default happy path.
- If a relevant module exists and its current contract supports the task, use its public interface.
- Without sibling modules, keep each module functional through baseline providers.
- If an optional Jiritsu integration is unavailable, fall back automatically to the supported Omarchy interface.
- If Omarchy has a genuine gap, fall back to the standard Linux interface.
- Do not make a missing optional module a startup error.
- Do not block the core standalone behaviour.
- Keep public behaviour and data meaning stable across Jiritsu and baseline providers.
- Report the selected provider, source, and fallback errors in structured output.
- Add focused tests for the integrated path and the standalone fallback.

## Omarchy First

This Omarchy installation is the development environment and the first test bed.

When inspecting or changing the machine:

1. Use the supported `omarchy ...` command or workflow when one exists.
2. If Omarchy has no suitable command, use its supported user-level extension or configuration mechanism.
3. Fall back to the underlying Arch Linux, systemd, or other Linux mechanism only when Omarchy has a genuine gap.

Inspect the installed Omarchy commands and implementation before assuming a gap. Do not modify packaged files under `/usr/share/omarchy/`; they are useful as read-only references but are owned by Omarchy and may be replaced by updates.

## Safety and Verification

- Start with observation. Make mutation explicit, narrow, and reversible as the relevant module gains that capability.
- Prefer dry runs, checkpoints, deterministic verification, and clear rollback paths for machine changes. Increase safeguards in proportion to the possible impact.
- Do not confuse model confidence with verification. Important outcomes must be checked against the real machine through deterministic probes.
- Keep tests focused on key behaviours, contracts, and failure paths. Do not build comprehensive suites for their own sake.
- Use this live machine for meaningful integration checks, while avoiding unnecessary disruption to the active desktop and services.

## Linux Components

Anything that runs as a daemon or changes machine state should behave like a good Linux component: use appropriate system interfaces and filesystem locations, support clean startup and shutdown, produce useful logs and exit statuses, avoid ambient privilege, and fail predictably.

Apply that standard pragmatically. Do not let naming debates, abstraction polish, exhaustive edge-case handling, or premature hardening prevent us from proving the next useful behaviour.
