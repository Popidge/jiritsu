---
name: jiritsu-observe
description: Reads current machine facts and workload health through Jiritsu. Use for system inventory, capability assessment, source-aware evidence, or pre-change baselines. Do not use for configuration changes, workload definition, crash diagnosis, or checkpoint restore.
---

# Observe the Machine with Jiritsu

Work from current evidence. Do not replace unavailable facts with model memory.

## Related skills

- If the task needs Omarchy behavior or a supported Omarchy procedure, load `omarchy`.
- If a process produced a core dump or crash notification, load `diagnose-crash`.
- If the user asks to change the machine after observation, load `jiritsu-change`.
- If the task changes a workload contract, load `jiritsu-workloads`.
- If the task changes broker policy or approvals, load `jiritsu-broker-admin`.

Do not repeat procedures from a related skill. Use that skill as the authority for its task.

## Procedure

1. Find `jiritsu-broker` in `PATH`.
2. If the repository is a Jiritsu source tree, use `./jiritsu-broker/bin/jiritsu-broker` as the development command.
3. Run `jiritsu-broker catalog --pretty` before the first request.
4. Select the narrowest operation that answers the request.
5. Use `state.query` for machine facts.
6. Use `workload.assess` for workload capabilities and health.
7. Give each request a unique `request_id`.
8. Set `actor` to the real agent or harness identity.
9. Do not put secrets in the request.
10. Send the request through `jiritsu-broker request`.

Use this request shape:

```json
{
  "schema_version": "1.0",
  "request_id": "observe-example-1",
  "actor": "codex",
  "operation": "state.query",
  "arguments": {
    "selectors": ["system.omarchy.version"]
  }
}
```

If the task needs the complete available result, omit `selectors`.

## Read the result

1. Read the broker `status` first.
2. If the status is `denied`, stop and report the matching policy decision.
3. If the status is `approval_required`, stop and load `jiritsu-broker-admin`.
4. If the status is `error`, report the structured broker errors.
5. Do not call a module directly after a broker denial.
6. Read `result.selected_provider`, `result.source`, and `result.fallback_errors`.
7. Then read the module status inside `result.data`.
8. Preserve successful facts from a partial state query.
9. Report each unavailable fact with its structured error.
10. Treat fixture facts as replayed evidence, not current machine evidence.
11. If freshness affects the answer, include `observed_at` and `age_seconds`.

For workload results, report these items:

- The overall result: `healthy`, `degraded`, or `unhealthy`.
- Each failed or unavailable capability.
- Whether each capability is `critical` or `useful`.
- The selected machine-state source.
- Each fallback error.

A degraded workload has no failed critical capability. An unhealthy workload has at least one failed critical capability.

## Standalone observation

If `jiritsu-broker` is not installed, use a direct module.

- Run `jiritsu-stated catalog --pretty` before an unfamiliar fact query.
- Run `jiritsu-stated query SELECTOR --pretty` for current facts.
- Run `jiritsu-workload assess WORKLOAD_ID --pretty` for workload health.

If the source repository contains the module, use its `./jiritsu-*/bin/` development command.

Do not hide a direct command exit status. Exit status `2` can mean a partial fact result or degraded workload.

## Limits

This skill does not repair a failed capability. Report the evidence, then route a requested repair to the applicable skill.
