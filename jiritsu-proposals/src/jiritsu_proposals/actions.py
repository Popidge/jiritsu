from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any

from .model import ProposalError
from .validation import sha256_bytes, target_path


class ActionApplyError(Exception):
    def __init__(self, message: str, applied: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.applied = applied


def _file_digest(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def inspect_actions(actions: list[dict[str, Any]], config_root: Path) -> dict[str, Any]:
    targets: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    recovery_actions: list[dict[str, Any]] = []
    planned_directories = {config_root.resolve()}
    for index, action in enumerate(actions):
        target = target_path(config_root, action["path"])
        if target.is_symlink():
            raise ProposalError(
                "unsafe_target",
                "symbolic-link action targets are not supported",
                details={"action": index, "path": action["path"]},
            )
        exists = target.exists()
        if action["type"] == "config.mkdir":
            if exists and not target.is_dir():
                raise ProposalError(
                    "target_conflict",
                    "config.mkdir target exists and is not a directory",
                    details={"action": index, "path": action["path"]},
                )
            parent = target.parent.resolve(strict=False)
            if not parent.is_dir() and parent not in planned_directories:
                raise ProposalError(
                    "target_parent_missing",
                    "config.mkdir parent does not exist and is not created by an earlier action",
                    details={"action": index, "path": action["path"]},
                )
            planned_directories.add(target.resolve(strict=False))
            observation = {
                "action": index,
                "path": action["path"],
                "exists": exists,
                "kind": "directory" if exists else "absent",
            }
            verification.append(
                {"type": "config.path", "path": action["path"], "kind": "directory"}
            )
            recovery_actions.append(
                {
                    "action": index,
                    "strategy": "no_change" if exists else "remove_created_directory",
                }
            )
        else:
            parent = target.parent.resolve(strict=False)
            if not parent.is_dir() and parent not in planned_directories:
                raise ProposalError(
                    "target_parent_missing",
                    "config.write parent does not exist and is not created by an earlier action",
                    details={"action": index, "path": action["path"]},
                )
            if exists and not target.is_file():
                raise ProposalError(
                    "target_conflict",
                    "config.write target exists and is not a regular file",
                    details={"action": index, "path": action["path"]},
                )
            current_digest = _file_digest(target) if exists else None
            expected = action.get("expected_sha256")
            if exists and expected is None:
                raise ProposalError(
                    "precondition_required",
                    "config.write requires expected_sha256 when it replaces a file",
                    details={
                        "action": index,
                        "path": action["path"],
                        "current_sha256": current_digest,
                    },
                )
            if expected is not None and expected != current_digest:
                raise ProposalError(
                    "precondition_failed",
                    "config.write expected_sha256 does not match the current file",
                    details={
                        "action": index,
                        "path": action["path"],
                        "expected_sha256": expected,
                        "actual_sha256": current_digest,
                    },
                )
            mode = stat.S_IMODE(target.stat().st_mode) if exists else None
            observation = {
                "action": index,
                "path": action["path"],
                "exists": exists,
                "kind": "file" if exists else "absent",
                "sha256": current_digest,
                "mode": f"0{mode:o}" if mode is not None else None,
            }
            desired_digest = sha256_bytes(action["content"].encode("utf-8"))
            verification.append(
                {
                    "type": "config.content_sha256",
                    "path": action["path"],
                    "expected": desired_digest,
                }
            )
            recovery_actions.append(
                {
                    "action": index,
                    "strategy": "restore_backup" if exists else "remove_created_file",
                }
            )
        targets.append(observation)
    return {
        "level": "low",
        "reasons": [
            "actions are limited to the user configuration directory",
            "no action executes a shell command or uses elevated privileges",
        ],
        "required_permissions": ["user_config.write"],
        "required_approval": "explicit",
        "safeguards": [
            "typed_actions_only",
            "atomic_file_replace",
            "optimistic_preconditions",
            "action_local_backup",
            "deterministic_verification",
            "workload_regression_check_when_available",
        ],
        "targets": targets,
        "verification": verification,
        "recovery_actions": recovery_actions,
    }


def _atomic_bytes(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}-", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def apply_actions(
    actions: list[dict[str, Any]], config_root: Path, recovery_dir: Path
) -> list[dict[str, Any]]:
    recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    applied: list[dict[str, Any]] = []
    try:
        for index, action in enumerate(actions):
            target = target_path(config_root, action["path"])
            record: dict[str, Any] = {
                "action": index,
                "type": action["type"],
                "path": action["path"],
                "changed": False,
            }
            if action["type"] == "config.mkdir":
                if not target.exists():
                    target.mkdir(mode=int(action["mode"], 8))
                    os.chmod(target, int(action["mode"], 8))
                    record["changed"] = True
                    record["recovery"] = "remove_created_directory"
                else:
                    record["recovery"] = "no_change"
            else:
                if target.exists():
                    backup = recovery_dir / f"{index:02d}.file"
                    shutil.copyfile(target, backup)
                    os.chmod(backup, 0o600)
                    record["original_mode"] = stat.S_IMODE(target.stat().st_mode)
                    record["backup"] = str(backup)
                    record["recovery"] = "restore_backup"
                else:
                    record["recovery"] = "remove_created_file"
                _atomic_bytes(
                    target, action["content"].encode("utf-8"), int(action["mode"], 8)
                )
                record["changed"] = True
                record["applied_sha256"] = sha256_bytes(
                    action["content"].encode("utf-8")
                )
            applied.append(record)
    except Exception as error:
        raise ActionApplyError(str(error), applied) from error
    return applied


def verify_actions(
    checks: list[dict[str, Any]], config_root: Path
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        try:
            target = target_path(config_root, check["path"])
            if check["type"] == "config.path":
                passed = target.is_dir()
                actual: Any = (
                    "directory"
                    if target.is_dir()
                    else ("file" if target.is_file() else "absent")
                )
            else:
                actual = _file_digest(target) if target.is_file() else None
                passed = actual == check["expected"]
            results.append(
                {**check, "status": "pass" if passed else "fail", "actual": actual}
            )
        except (OSError, ProposalError) as error:
            results.append({**check, "status": "error", "message": str(error)})
    return results


def rollback_actions(
    applied: list[dict[str, Any]], config_root: Path
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for record in reversed(applied):
        try:
            target = target_path(config_root, record["path"])
            strategy = record["recovery"]
            if strategy == "restore_backup":
                backup = Path(record["backup"])
                _atomic_bytes(target, backup.read_bytes(), int(record["original_mode"]))
            elif strategy == "remove_created_file":
                target.unlink(missing_ok=True)
            elif strategy == "remove_created_directory" and target.exists():
                target.rmdir()
            results.append(
                {
                    "action": record["action"],
                    "path": record["path"],
                    "status": "restored",
                }
            )
        except (OSError, ProposalError) as error:
            results.append(
                {
                    "action": record["action"],
                    "path": record["path"],
                    "status": "error",
                    "message": str(error),
                }
            )
    return results
