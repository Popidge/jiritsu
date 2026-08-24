from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from .backend import (
    apply_system_restore,
    backend_capabilities,
    create_system_snapshots,
    system_restore_plan,
)
from .config_capture import capture_config, restore_config
from .discovery import MachineState, Runner, run_command
from .model import CHECKPOINT_SCHEMA_VERSION, CheckpointError, timestamp
from .store import CheckpointStore


def _scope_summary(scope: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    recoverable: list[dict[str, Any]] = []
    not_recoverable: list[dict[str, Any]] = [
        {
            "kind": "external_effects",
            "description": "Remote actions, disclosed secrets, hardware changes, and external deletions are not recoverable.",
        }
    ]
    system = scope["system"]
    if system["status"] == "captured":
        root_snapshots = [item for item in system["snapshots"] if item["subvolume"] == "/"]
        other_snapshots = [item for item in system["snapshots"] if item["subvolume"] != "/"]
        if root_snapshots:
            recoverable.append(
                {
                    "kind": "system_root",
                    "description": "The Btrfs root can be restored through its Snapper snapshot.",
                    "snapshots": root_snapshots,
                }
            )
        if other_snapshots:
            not_recoverable.append(
                {
                    "kind": "non_root_snapshots",
                    "description": "Non-root Snapper snapshots are recorded but need a backend-specific manual restore.",
                    "snapshots": other_snapshots,
                }
            )
    elif system["status"] == "planned":
        recoverable.append(
            {
                "kind": "system_root",
                "description": "The operation will create identifiable snapshots for the available Snapper configurations.",
            }
        )
    else:
        not_recoverable.append(
            {
                "kind": "system_state",
                "description": "This checkpoint has no system snapshot.",
            }
        )
    user = scope["user_config"]
    if user["status"] in {"captured", "planned"}:
        entries = user["entries"]
        paths = (
            [entry["path"] for entry in entries]
            if entries
            else user["policy"]["include"]
        )
        recoverable.append(
            {
                "kind": "user_config",
                "description": (
                    "Only the explicitly selected user configuration paths can be restored."
                    if user["status"] == "captured"
                    else "The operation will capture only the user configuration paths in the explicit policy."
                ),
                "paths": paths,
            }
        )
        not_recoverable.append(
            {
                "kind": "unselected_user_data",
                "description": "User files outside the explicit config policy are not captured.",
            }
        )
    else:
        not_recoverable.append(
            {
                "kind": "user_config",
                "description": "No user configuration policy was supplied.",
            }
        )
    return {"recoverable": recoverable, "not_recoverable": not_recoverable}


def inspect_backend(state: MachineState) -> dict[str, Any]:
    return {
        "provider_status": "available",
        "machine_state": state.public(),
        "backend": backend_capabilities(state),
        "user_config": {
            "available": True,
            "provider": "python:file-copy",
            "requires_explicit_policy": True,
        },
    }


def create_checkpoint(
    store: CheckpointStore,
    *,
    checkpoint_id: str,
    reason: str,
    proposal_id: str | None,
    system_mode: str,
    policy: dict[str, Any] | None,
    config_root: Path,
    state: MachineState,
    dry_run: bool = False,
    runner: Runner = run_command,
) -> tuple[str, dict[str, Any]]:
    capabilities = backend_capabilities(state)
    system_available = capabilities["selection"]["snapper_create"]["available"]
    system_selected = system_mode != "off" and system_available
    if system_mode == "required" and not system_available:
        raise CheckpointError(
            "backend_unavailable",
            "a system checkpoint was required, but Snapper creation is unavailable",
            details={"backend": capabilities, "machine_state": state.public()},
        )
    if not system_selected and policy is None:
        raise CheckpointError(
            "empty_scope",
            "no recoverable scope is available; configure Snapper or supply a user config policy",
        )
    scope: dict[str, Any] = {
        "system": {
            "requested": system_mode,
            "status": "planned" if system_selected else ("skipped" if system_mode == "off" else "unavailable"),
            "provider": "snapper" if system_selected else None,
            "source": "linux:snapper" if system_selected else None,
            "snapshots": [],
            "warnings": [],
        },
        "user_config": {
            "status": "planned" if policy else "skipped",
            "provider": "python:file-copy" if policy else None,
            "config_root": str(config_root.expanduser().resolve()) if policy else None,
            "policy": policy,
            "entries": [],
        },
    }
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "id": checkpoint_id,
        "status": "planned" if dry_run else "creating",
        "reason": reason,
        "created_at": timestamp(),
        "backend": capabilities,
        "machine_state": state.public(),
        "scope": scope,
        "recovery": _scope_summary(scope),
        "restore_history": [],
    }
    if proposal_id:
        checkpoint["proposal_id"] = proposal_id
    if dry_run:
        return "planned", checkpoint

    errors: list[dict[str, Any]] = []
    with store.lock(checkpoint_id, create=True) as directory:
        store.save(checkpoint)
        if system_selected:
            try:
                snapshots, warnings = create_system_snapshots(
                    checkpoint_id,
                    reason,
                    proposal_id,
                    state,
                    runner=runner,
                )
                scope["system"].update(
                    status="captured", snapshots=snapshots, warnings=warnings
                )
            except CheckpointError as error:
                scope["system"]["status"] = "failed"
                if error.details and isinstance(error.details.get("created"), list):
                    scope["system"]["snapshots"] = error.details["created"]
                errors.append(error.public())
        if policy:
            try:
                entries = capture_config(
                    config_root, policy, directory / "user-config" / "files"
                )
                scope["user_config"].update(status="captured", entries=entries)
            except (CheckpointError, OSError) as error:
                scope["user_config"]["status"] = "failed"
                public = error.public() if isinstance(error, CheckpointError) else {
                    "code": "config_capture_failed",
                    "message": f"cannot capture user config: {error}",
                }
                errors.append(public)
        captured = [
            item for item in (scope["system"]["status"], scope["user_config"]["status"])
            if item == "captured"
        ]
        if errors and captured:
            checkpoint["status"] = "partial"
        elif errors:
            checkpoint["status"] = "failed"
        else:
            checkpoint["status"] = "ready"
        checkpoint["recovery"] = _scope_summary(scope)
        checkpoint["errors"] = errors
        checkpoint["completed_at"] = timestamp()
        store.save(checkpoint)
    return checkpoint["status"], checkpoint


def restore_user_config(
    store: CheckpointStore,
    checkpoint: dict[str, Any],
    *,
    apply: bool,
    config_root: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    scope = checkpoint["scope"]["user_config"]
    if scope["status"] != "captured":
        raise CheckpointError(
            "scope_unavailable",
            "this checkpoint does not contain user configuration",
            checkpoint_id=checkpoint["id"],
        )
    root = config_root or Path(scope["config_root"])
    plan = {
        "scope": "user_config",
        "provider": "python:file-copy",
        "config_root": str(root.expanduser().resolve()),
        "paths": [entry["path"] for entry in scope["entries"]],
        "effect": "Replace each selected path with its captured state; paths captured as missing are removed.",
    }
    if not apply:
        return "planned", plan
    restore_id = (
        timestamp().replace(":", "").replace("-", "")
        + f"-{secrets.token_hex(3)}"
    )
    with store.lock(checkpoint["id"]) as directory:
        result = restore_config(
            root,
            scope["entries"],
            directory / "user-config" / "files",
            directory / "restores" / restore_id / "before",
        )
        checkpoint = store.load(checkpoint["id"])
        checkpoint["restore_history"].append(
            {"at": timestamp(), "scope": "user_config", "status": "restored", **result}
        )
        store.save(checkpoint)
    return "restored", result


def restore_system(
    store: CheckpointStore,
    checkpoint: dict[str, Any],
    state: MachineState,
    *,
    apply: bool,
    runner: Runner = run_command,
) -> tuple[str, dict[str, Any]]:
    plan = system_restore_plan(checkpoint, state)
    if not apply:
        return "planned", plan
    result = apply_system_restore(plan, runner=runner)
    if result["status"] == "action_required":
        return "action_required", result
    with store.lock(checkpoint["id"]):
        checkpoint = store.load(checkpoint["id"])
        checkpoint["restore_history"].append(
            {
                "at": timestamp(),
                "scope": "system",
                "status": result["status"],
                "provider": result["provider"],
                "target": result["target"],
            }
        )
        store.save(checkpoint)
    return result["status"], result
