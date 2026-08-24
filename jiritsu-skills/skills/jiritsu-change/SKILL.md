---
name: jiritsu-change
description: Records, classifies, approves, applies, and verifies supported user-configuration changes through Jiritsu proposals. Use for durable and recoverable agent changes on Omarchy. Do not use for arbitrary commands, system files, remote actions, workload contracts, or checkpoint restore.
---

# Change User Configuration with Jiritsu

If the broker and proposal module are available, use the complete Jiritsu path.

```text
observe -> propose -> classify -> approve -> promote -> verify
```

A skill gives instructions. It does not grant authority.

## Related skills

- Load `omarchy` before an end-user Omarchy or desktop customization.
- Let `omarchy` identify the supported method, target files, apply behavior, and verification commands.
- Use this skill to govern supported file changes after the Omarchy method is known.
- Load `jiritsu-observe` for additional current facts or workload evidence.
- Load `jiritsu-recover` for an independent checkpoint or restore.
- Load `jiritsu-broker-admin` when approval needs diagnosis or administrator setup.

Do not copy an Omarchy procedure into this workflow. Keep the Omarchy and Jiritsu responsibilities separate.

For a supported file edit, do not write the target before proposal promotion.

If a required Omarchy discovery or verification command blocks, stop. Do not infer live state from packaged defaults.

## Scope

The first proposal contract supports these action types:

- `config.mkdir` creates one directory under the user configuration root.
- `config.write` writes one file atomically under the user configuration root.

The contract rejects absolute paths, parent paths, and symbolic-link targets. It does not run commands or change system files.

If the supported Omarchy method is a command, do not invent a file edit. Use the `omarchy` skill for that command.

## Procedure

1. Observe the current target and the relevant workloads.
2. Read the target file before you draft its replacement.
3. If the target file exists, calculate its SHA-256 digest.
4. Make the proposed action as narrow as possible.
5. Do not put secrets in proposal content or broker requests.
6. Run `jiritsu-broker catalog --pretty` before the first broker request.
7. Read [references/broker-requests.md](references/broker-requests.md) for the request templates.
8. Create the draft through `proposal.create`.
9. Classify the draft through `proposal.classify`.
10. Read the classification before you request approval.
11. Make sure that the classification names the target, preconditions, safeguards, verification, and recovery.
12. Apply the existing-critical-workload rule in this skill.
13. Request proposal approval through `proposal.approve`.
14. Follow the approval boundary in this skill.
15. Promote the approved proposal through `proposal.promote`.
16. Read the terminal proposal state.
17. If Omarchy requires an apply command, run it after the proposal commits.
18. Verify the requested effect with the applicable Omarchy procedure.
19. If semantic verification fails, follow the post-commit recovery rule.
20. Report the proposal ID, checkpoint ID, final state, provider, and fallback errors.

For an existing file, include `expected_sha256` in `config.write`. This digest prevents an overwrite after stale observation.

An Omarchy apply command is outside the proposal action. Report this limit before you run that command.

## Existing critical workload

Promotion detects a new critical failure. It does not reject an existing critical failure.

1. If classification reports an unhealthy critical capability, identify its relationship to the change.
2. If the change cannot affect that capability, report the existing failure and continue.
3. If the change can affect that capability without repairing it, stop until the user accepts the weak baseline.
4. If the change repairs that capability, define deterministic semantic verification.
5. State that the workload baseline cannot detect a regression of the failed capability.
6. Continue only after the user explicitly accepts this limit.

## Post-commit recovery

Proposal verification confirms the expected file state. It does not run each Omarchy semantic validator.

If post-commit semantic verification fails, use this procedure:

1. Stop the related apply commands and unrelated changes.
2. Do not make an unrecorded corrective edit.
3. Read the linked checkpoint ID from the proposal result.
4. If a linked checkpoint exists, load `jiritsu-recover` and plan a user-configuration restore.
5. If no linked checkpoint exists, report that automated post-commit restore is unavailable.
6. Get explicit user approval before you apply an available restore.
7. Report that the proposal stays `committed` and the restore is a separate recovery action.

## Approval boundary

The packaged broker policy requires request-bound external approval for approval and promotion.

1. If the broker returns `approval_required`, keep the exact request unchanged.
2. Run `jiritsu-broker fingerprint REQUEST.json --pretty`.
3. Report the request digest and approval path to the user.
4. Do not create or edit the approval file.
5. Do not claim a human identity in `approved_by`.
6. If the approval channel needs setup, load `jiritsu-broker-admin`.
7. Stop until an independent approver creates the trusted approval file.
8. Send the same request again after that approval exists.

Create a different request ID for proposal approval and proposal promotion. A terminal broker result prevents replay of that request ID.

If the broker returns `denied`, stop. Do not bypass the policy with `jiritsu-proposals` or a direct file edit.

## Read the terminal state

- `committed` means that all actions and deterministic verification succeeded.
- `rolled_back` means that promotion found an error and restored the applied actions.
- `failed` means that promotion found an error and cannot restore all applied actions.

If the state is `failed`, stop unrelated changes. Report the recovery errors.

If a checkpoint exists, load `jiritsu-recover`.

## Standalone proposal path

If the broker is not installed, use the direct proposal module.

Read [references/standalone-proposals.md](references/standalone-proposals.md) before you use this path.

Do not approve a direct proposal on behalf of another actor. Require a separate, explicit approval after classification.

## Limits

Proposal recovery cannot restore remote effects, disclosed secrets, external services, or unrelated machine state.
