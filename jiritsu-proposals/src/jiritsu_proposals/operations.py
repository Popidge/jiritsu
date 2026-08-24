from __future__ import annotations

import secrets
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .actions import (
    ActionApplyError,
    apply_actions,
    inspect_actions,
    rollback_actions,
    verify_actions,
)
from .model import ProposalError, SCHEMA_VERSION, event, timestamp, transition
from .providers import (
    assess_workloads,
    collect_machine_state,
    prepare_recovery,
    recovery_provider,
)
from .store import ProposalStore
from .validation import ID_PATTERN, action_digest, parse_definition


def _new_id() -> str:
    prefix = datetime.now(timezone.utc).strftime("p-%Y%m%d-%H%M%S")
    return f"{prefix}-{secrets.token_hex(3)}"


def _require_state(proposal: dict[str, Any], *states: str) -> None:
    if proposal["state"] not in states:
        raise ProposalError(
            "invalid_transition",
            f"proposal must be in {', '.join(states)} state",
            proposal_id=proposal["id"],
            details={"actual_state": proposal["state"], "allowed_states": list(states)},
        )


def create_proposal(
    store: ProposalStore, definition: Any, proposal_id: str | None = None
) -> dict[str, Any]:
    normalized = parse_definition(definition)
    selected_id = proposal_id or _new_id()
    if not ID_PATTERN.fullmatch(selected_id):
        raise ProposalError(
            "invalid_id",
            "proposal ID must use lowercase letters, numbers, dots, underscores, or hyphens",
            field="id",
        )
    now = timestamp()
    actor = normalized["origin"]["actor"]
    proposal = {
        "schema_version": SCHEMA_VERSION,
        "id": selected_id,
        "revision": 1,
        "state": "draft",
        "created_at": now,
        "updated_at": now,
        "intent": normalized["intent"],
        "origin": normalized["origin"],
        "actions": normalized["actions"],
        "classification": None,
        "approval": None,
        "verification": [],
        "recovery": {"status": "unclassified"},
        "promotion": None,
        "history": [event(1, "created", actor, to_state="draft")],
    }
    with store.lock(selected_id, create=True):
        store.save(proposal)
    return proposal


def classify_proposal(
    store: ProposalStore,
    proposal_id: str,
    actor: str,
    config_root: Path,
    timeout: float,
) -> dict[str, Any]:
    with store.lock(proposal_id):
        proposal = store.load(proposal_id)
        _require_state(proposal, "draft")
        if not config_root.is_dir():
            raise ProposalError(
                "config_root_missing",
                f"user config directory does not exist: {config_root}",
                proposal_id=proposal_id,
            )
        try:
            inspection = inspect_actions(proposal["actions"], config_root)
        except OSError as error:
            raise ProposalError(
                "target_inspection_failed",
                f"cannot inspect an action target: {error}",
                proposal_id=proposal_id,
            ) from error
        proposal["classification"] = {
            "classified_at": timestamp(),
            "classified_by": actor,
            "risk": {
                key: inspection[key]
                for key in (
                    "level",
                    "reasons",
                    "required_permissions",
                    "required_approval",
                    "safeguards",
                )
            },
            "action_digest": action_digest(proposal["actions"]),
            "config_root": str(config_root.resolve()),
            "targets": inspection["targets"],
            "machine_state": collect_machine_state(timeout),
            "workload_state": assess_workloads(timeout),
        }
        proposal["verification"] = inspection["verification"]
        proposal["recovery"] = {
            **recovery_provider(),
            "actions": inspection["recovery_actions"],
        }
        transition(proposal, "classified", "classified", actor)
        store.save(proposal)
        return proposal


def approve_proposal(
    store: ProposalStore, proposal_id: str, actor: str, note: str | None = None
) -> dict[str, Any]:
    with store.lock(proposal_id):
        proposal = store.load(proposal_id)
        _require_state(proposal, "classified")
        approval: dict[str, Any] = {
            "status": "approved",
            "approved_at": timestamp(),
            "approved_by": actor,
            "action_digest": proposal["classification"]["action_digest"],
            "permissions": proposal["classification"]["risk"]["required_permissions"],
        }
        if note:
            approval["note"] = note
        proposal["approval"] = approval
        transition(proposal, "approved", "approved", actor)
        store.save(proposal)
        return proposal


def reject_proposal(
    store: ProposalStore, proposal_id: str, actor: str, reason: str
) -> dict[str, Any]:
    with store.lock(proposal_id):
        proposal = store.load(proposal_id)
        _require_state(proposal, "draft", "classified")
        proposal["approval"] = {
            "status": "rejected",
            "rejected_at": timestamp(),
            "rejected_by": actor,
            "reason": reason,
        }
        transition(proposal, "rejected", "rejected", actor, details={"reason": reason})
        store.save(proposal)
        return proposal


def _finish_rollback(
    store: ProposalStore,
    proposal: dict[str, Any],
    actor: str,
    config_root: Path,
    applied: list[dict[str, Any]],
    failure: dict[str, Any],
) -> dict[str, Any]:
    rollback = rollback_actions(applied, config_root)
    rollback_ok = all(item["status"] == "restored" for item in rollback)
    proposal["promotion"].update(
        {
            "status": "rolled_back" if rollback_ok else "failed",
            "finished_at": timestamp(),
            "failure": failure,
            "applied_actions": applied,
            "rollback": rollback,
        }
    )
    proposal["recovery"]["status"] = "restored" if rollback_ok else "failed"
    transition(
        proposal,
        "rolled_back" if rollback_ok else "failed",
        "rolled_back" if rollback_ok else "rollback_failed",
        actor,
        details={"failure_code": failure["code"]},
    )
    store.save(proposal)
    return proposal


def promote_proposal(
    store: ProposalStore,
    proposal_id: str,
    actor: str,
    config_root: Path,
    timeout: float,
) -> dict[str, Any]:
    with store.lock(proposal_id):
        proposal = store.load(proposal_id)
        _require_state(proposal, "approved")
        classification = proposal["classification"]
        if str(config_root.resolve()) != classification["config_root"]:
            raise ProposalError(
                "config_root_changed",
                "promotion must use the config directory used for classification",
                proposal_id=proposal_id,
                details={
                    "classified": classification["config_root"],
                    "requested": str(config_root.resolve()),
                },
            )
        digest = action_digest(proposal["actions"])
        if proposal["approval"]["action_digest"] != digest:
            raise ProposalError(
                "approval_scope_changed",
                "approved actions no longer match the proposal",
                proposal_id=proposal_id,
            )
        try:
            inspection = inspect_actions(proposal["actions"], config_root)
        except OSError as error:
            raise ProposalError(
                "target_inspection_failed",
                f"cannot inspect an action target: {error}",
                proposal_id=proposal_id,
            ) from error
        if inspection["targets"] != classification["targets"]:
            raise ProposalError(
                "target_state_changed",
                "an action target changed after classification; classify a new proposal",
                proposal_id=proposal_id,
                details={
                    "classified_targets": classification["targets"],
                    "current_targets": inspection["targets"],
                },
            )

        before_state = collect_machine_state(timeout)
        before_workloads = assess_workloads(timeout)
        proposal["promotion"] = {
            "status": "applying",
            "started_at": timestamp(),
            "promoted_by": actor,
            "machine_state_before": before_state,
            "workloads_before": before_workloads,
            "checkpoint": deepcopy(proposal["recovery"]),
        }
        transition(proposal, "applying", "promotion_started", actor)
        store.save(proposal)

        try:
            prepared_recovery = prepare_recovery(
                proposal["recovery"],
                proposal_id=proposal_id,
                summary=proposal["intent"]["summary"],
                actions=proposal["actions"],
                config_root=config_root,
                recovery_dir=store.backup_dir(proposal_id),
                timeout=timeout,
            )
        except OSError as error:
            return _finish_rollback(
                store,
                proposal,
                actor,
                config_root,
                [],
                {"code": "recovery_preparation_failed", "message": str(error)},
            )
        proposal["promotion"]["checkpoint"] = prepared_recovery
        proposal["recovery"] = prepared_recovery
        store.save(proposal)

        try:
            applied = apply_actions(
                proposal["actions"], config_root, store.backup_dir(proposal_id)
            )
        except ActionApplyError as error:
            return _finish_rollback(
                store,
                proposal,
                actor,
                config_root,
                error.applied,
                {"code": "action_failed", "message": str(error)},
            )

        verification = verify_actions(proposal["verification"], config_root)
        if any(check["status"] != "pass" for check in verification):
            proposal["promotion"]["verification"] = verification
            return _finish_rollback(
                store,
                proposal,
                actor,
                config_root,
                applied,
                {
                    "code": "verification_failed",
                    "message": "one or more deterministic verification checks failed",
                },
            )

        after_state = collect_machine_state(timeout)
        after_workloads = assess_workloads(timeout)
        workload_results_are_comparable = (
            before_workloads["selected_provider"] == "jiritsu-workload"
            and after_workloads["selected_provider"] == "jiritsu-workload"
        )
        new_critical_failures = (
            sorted(
                set(after_workloads["critical_failures"])
                - set(before_workloads["critical_failures"])
            )
            if workload_results_are_comparable
            else []
        )
        if new_critical_failures:
            proposal["promotion"].update(
                {
                    "verification": verification,
                    "machine_state_after": after_state,
                    "workloads_after": after_workloads,
                }
            )
            return _finish_rollback(
                store,
                proposal,
                actor,
                config_root,
                applied,
                {
                    "code": "workload_regression",
                    "message": "promotion introduced a critical workload failure",
                    "new_critical_failures": new_critical_failures,
                },
            )

        proposal["promotion"].update(
            {
                "status": "committed",
                "finished_at": timestamp(),
                "applied_actions": applied,
                "verification": verification,
                "machine_state_after": after_state,
                "workloads_after": after_workloads,
            }
        )
        proposal["recovery"]["status"] = "available"
        transition(proposal, "committed", "committed", actor)
        store.save(proposal)
        return proposal


def proposal_summary(proposal: dict[str, Any]) -> dict[str, Any]:
    classification = proposal.get("classification")
    return {
        "id": proposal["id"],
        "state": proposal["state"],
        "created_at": proposal["created_at"],
        "updated_at": proposal["updated_at"],
        "summary": proposal["intent"]["summary"],
        "origin": proposal["origin"],
        "action_count": len(proposal["actions"]),
        "risk": classification["risk"]["level"] if classification else "unclassified",
    }
