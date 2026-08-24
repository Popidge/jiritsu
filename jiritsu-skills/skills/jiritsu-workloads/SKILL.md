---
name: jiritsu-workloads
description: Defines, validates, applies, and assesses Jiritsu workload contracts for local machine roles. Use for critical capabilities, useful capabilities, stated facts, or direct probes. Do not use to repair the machine, change Omarchy preferences, diagnose crashes, or restore checkpoints.
---

# Describe Machine Workloads with Jiritsu

A workload contract describes the capabilities that make this machine useful for one local role.

The module assesses these capabilities. It does not repair them.

## Related skills

- If a contract describes an Omarchy desktop behavior, load `omarchy`.
- Let `omarchy` define the supported behavior and current verification method.
- If the task only needs an existing assessment, load `jiritsu-observe`.
- If the user asks to repair a failed capability through configuration, load `jiritsu-change`.
- If the evidence includes a core dump or crash notification, load `diagnose-crash`.

Do not duplicate Omarchy procedures in a workload description. Represent their observable result as a capability check.

## Procedure

1. Run `jiritsu-workload list --pretty`.
2. Run `jiritsu-workload query WORKLOAD_ID --pretty` before you change an existing contract.
3. Run `jiritsu-stated catalog --pretty` before you select a stated fact.
4. Define one workload for one local machine role.
5. Define each required outcome as one capability.
6. If a capability failure prevents the primary role, mark that capability `critical`.
7. Mark other valuable capabilities `useful`.
8. Prefer a `stated_fact` check for a stable fact in the catalog.
9. If the same fact has a safe direct probe, add that direct fallback.
10. Use direct checks for session-local facts that `jiritsu-stated` does not report.
11. Keep all probes read-only.
12. Read [references/contract-format.md](references/contract-format.md) for fields, operators, and check types.
13. Validate the complete TOML file before you apply it.
14. Preview a new or changed contract in an isolated contract directory.
15. Apply the live contract with `jiritsu-workload apply CONTRACT.toml --pretty` only after the preview.
16. If the broker is available, assess the applied workload through `jiritsu-observe`.
17. If the broker is absent, run `jiritsu-workload assess WORKLOAD_ID --pretty`.
18. Report its source file, result, failed capabilities, and fallback use.

If the source repository contains the module, use the development command under `./jiritsu-workload/bin/`.

## Source selection

The assessment uses `jiritsu-stated` by default. It uses direct fallbacks only for unavailable facts.

A valid negative stated fact is a failed check. Do not replace that result with a fallback.

Use `--state-source direct` only to diagnose the stated integration. Report that override in the result.

## Isolated preview

The `--config-dir` option selects a temporary user-contract directory. It does not isolate the machine probes.

1. Create a fresh temporary directory.
2. Validate the contract with `--config-dir TEMP_DIR`.
3. Apply the contract with `--config-dir TEMP_DIR`.
4. Assess its workload ID with `--config-dir TEMP_DIR`.
5. Read the result before you write the live user contract.

The preview reads the live machine. It writes only the temporary contract copy.

## Apply boundary

The `apply` command creates or updates one user contract atomically. A user contract overrides a default contract with the same ID.

Do not apply a contract from an untrusted source. A custom command check runs with the current user permissions.

Do not put secrets in environment expectations or command output. Assessment results can contain bounded command output.

## Read the result

- `healthy` means that all capabilities pass.
- `degraded` means that all critical capabilities pass, but a useful capability does not pass.
- `unhealthy` means that a critical capability does not pass.

A check result is `pass`, `fail`, or `error`. Preserve this distinction in the report.

## Error handling

If validation reports an unknown field, read the installed module contract before you change the field.

If a stated fact is unavailable, report the fallback result and the source error.

If no fallback exists, keep the check result as `error`. Do not guess the capability state.
