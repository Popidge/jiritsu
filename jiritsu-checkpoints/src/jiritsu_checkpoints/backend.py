from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from .discovery import MachineState, Runner, run_command
from .model import CheckpointError


def backend_capabilities(state: MachineState) -> dict[str, Any]:
    snapper = shutil.which("snapper")
    sudo = shutil.which("sudo")
    omarchy = shutil.which("omarchy")
    configured = bool(state.configurations)
    btrfs_root = bool(
        state.active_root and state.active_root.get("filesystem") == "btrfs"
    )
    return {
        "provider": "snapper" if snapper and configured else "user_config",
        "source": "linux:snapper" if snapper and configured else "python:file-copy",
        "selection": {
            "omarchy_create": {
                "available": bool(omarchy),
                "selected": False,
                "reason": (
                    "the Omarchy create command does not return the created snapshot IDs"
                    if omarchy
                    else "Omarchy is not available"
                ),
            },
            "snapper_create": {
                "available": bool(snapper and sudo and configured),
                "selected": bool(snapper and sudo and configured),
                "authority": "sudo" if sudo else None,
            },
            "system_restore": {
                "available": bool(snapper and sudo and configured and btrfs_root),
                "provider": "omarchy" if omarchy else "snapper",
                "workflow": "boot_snapshot_then_restore" if omarchy else "snapper_rollback",
            },
        },
    }


def _detail(result: subprocess.CompletedProcess[str]) -> str:
    value = (result.stderr or result.stdout).strip()
    return value if len(value) <= 500 else value[:497] + "..."


def create_system_snapshots(
    checkpoint_id: str,
    reason: str,
    proposal_id: str | None,
    state: MachineState,
    *,
    runner: Runner = run_command,
    timeout_seconds: float = 120.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    capabilities = backend_capabilities(state)
    selected = capabilities["selection"]["snapper_create"]
    if not selected["available"]:
        raise CheckpointError(
            "backend_unavailable",
            "Snapper creation needs a configuration and the snapper and sudo commands",
            details={"backend": capabilities},
        )
    description = f"jiritsu {checkpoint_id}: {reason}"
    if len(description) > 240:
        description = description[:237] + "..."
    userdata = f"jiritsu_checkpoint={checkpoint_id}"
    if proposal_id:
        userdata += f",proposal={proposal_id}"
    references: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for configuration in state.configurations:
        command = [
            "sudo",
            "snapper",
            "-c",
            configuration["name"],
            "create",
            "--type",
            "single",
            "--cleanup-algorithm",
            "number",
            "--description",
            description,
            "--userdata",
            userdata,
            "--read-only",
            "--print-number",
        ]
        try:
            result = runner(command, timeout=timeout_seconds)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
            raise CheckpointError(
                "snapshot_create_failed",
                f"Snapper could not create a snapshot for {configuration['name']}: {error}",
                details={"created": references},
            ) from error
        if result.returncode != 0:
            raise CheckpointError(
                "snapshot_create_failed",
                f"Snapper failed for {configuration['name']} with status {result.returncode}: {_detail(result)}",
                details={"created": references},
            )
        numbers = [
            int(match.group(1))
            for line in result.stdout.splitlines()
            if (match := re.fullmatch(r"\s*(\d+)\s*", line))
        ]
        if not numbers:
            raise CheckpointError(
                "snapshot_create_failed",
                f"Snapper did not return a snapshot ID for {configuration['name']}",
                details={"created": references},
            )
        references.append(
            {
                "configuration": configuration["name"],
                "subvolume": configuration["subvolume"],
                "snapshot_id": numbers[-1],
                "read_only": True,
            }
        )
        try:
            cleanup = runner(
                [
                    "sudo",
                    "snapper",
                    "-c",
                    configuration["name"],
                    "cleanup",
                    "number",
                ],
                timeout=timeout_seconds,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
            warnings.append(
                {
                    "code": "snapshot_cleanup_failed",
                    "message": f"Snapper cleanup could not run for {configuration['name']}: {error}",
                    "configuration": configuration["name"],
                }
            )
        else:
            if cleanup.returncode != 0:
                warnings.append(
                    {
                        "code": "snapshot_cleanup_failed",
                        "message": f"Snapper cleanup failed for {configuration['name']}: {_detail(cleanup)}",
                        "configuration": configuration["name"],
                    }
                )
    return references, warnings


def root_reference(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    references = checkpoint.get("scope", {}).get("system", {}).get("snapshots", [])
    for reference in references:
        if isinstance(reference, dict) and reference.get("subvolume") == "/":
            return reference
    return None


def system_restore_plan(
    checkpoint: dict[str, Any], state: MachineState
) -> dict[str, Any]:
    reference = root_reference(checkpoint)
    if reference is None:
        raise CheckpointError(
            "scope_unavailable",
            "this checkpoint does not contain a root filesystem snapshot",
            checkpoint_id=checkpoint["id"],
        )
    active_snapshot_id = (
        state.active_root.get("snapper_snapshot_id") if state.active_root else None
    )
    omarchy = shutil.which("omarchy")
    target = reference["snapshot_id"]
    if omarchy:
        booted_from_target = active_snapshot_id == target
        instructions = (
            ["Run: omarchy snapshot restore", "Reboot into the normal system."]
            if booted_from_target
            else [
                f"Reboot and select Snapper snapshot {target} in the Limine boot menu.",
                f"After that snapshot boots, run: jiritsu-checkpoints restore {checkpoint['id']} --scope system --apply",
                "Reboot into the restored normal system when Omarchy finishes.",
            ]
        )
        return {
            "provider": "omarchy",
            "source": "omarchy snapshot restore",
            "workflow": "boot_snapshot_then_restore",
            "target": reference,
            "active_snapshot_id": active_snapshot_id,
            "ready_to_apply": booted_from_target,
            "instructions": instructions,
        }
    return {
        "provider": "snapper",
        "source": "linux:snapper rollback",
        "workflow": "snapper_rollback",
        "target": reference,
        "active_snapshot_id": active_snapshot_id,
        "ready_to_apply": True,
        "instructions": [
            f"Run Snapper rollback for snapshot {target} in configuration {reference['configuration']}.",
            "Reboot and verify that the bootloader uses the new default Btrfs subvolume.",
        ],
    }


def apply_system_restore(
    plan: dict[str, Any], *, runner: Runner = run_command, timeout_seconds: float = 600.0
) -> dict[str, Any]:
    if not plan["ready_to_apply"]:
        return {"status": "action_required", "plan": plan}
    if plan["provider"] == "omarchy":
        command = ["omarchy", "snapshot", "restore"]
    else:
        target = plan["target"]
        command = [
            "sudo",
            "snapper",
            "-c",
            target["configuration"],
            "rollback",
            str(target["snapshot_id"]),
        ]
    try:
        result = runner(command, timeout=timeout_seconds)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        raise CheckpointError("restore_failed", f"system restore command could not run: {error}") from error
    if result.returncode != 0:
        raise CheckpointError(
            "restore_failed",
            f"system restore command failed with status {result.returncode}: {_detail(result)}",
        )
    return {
        "status": "reboot_required",
        "provider": plan["provider"],
        "target": plan["target"],
        "instructions": ["Reboot, then verify the restored system and boot configuration."],
    }
