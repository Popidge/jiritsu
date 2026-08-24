from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tomllib
from pathlib import Path, PurePosixPath
from typing import Any

from .model import CheckpointError


POLICY_SCHEMA_VERSION = "1.0"


def default_config_root(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    root = env.get("XDG_CONFIG_HOME")
    return Path(root).expanduser() if root else Path.home() / ".config"


def _relative_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CheckpointError("policy_invalid", f"{field} must be a nonempty string", field=field)
    pure = PurePosixPath(value)
    if pure.is_absolute() or value.startswith("~") or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise CheckpointError(
            "policy_invalid",
            f"{field} must be a normalized path relative to the user config directory",
            field=field,
        )
    return pure.as_posix()


def load_policy(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        payload = tomllib.loads(raw.decode("utf-8"))
    except FileNotFoundError as error:
        raise CheckpointError("policy_not_found", f"policy does not exist: {path}") from error
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise CheckpointError("policy_invalid", f"cannot read policy {path}: {error}") from error
    if not isinstance(payload, dict):
        raise CheckpointError("policy_invalid", "policy root must be a table")
    unknown = sorted(set(payload) - {"schema_version", "include"})
    if unknown:
        raise CheckpointError("policy_invalid", f"unknown policy field: {unknown[0]}", field=unknown[0])
    if payload.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise CheckpointError("policy_invalid", 'policy schema_version must be "1.0"', field="schema_version")
    include = payload.get("include")
    if not isinstance(include, list) or not include:
        raise CheckpointError("policy_invalid", "policy include must contain at least one path", field="include")
    if len(include) > 64:
        raise CheckpointError("policy_invalid", "policy include cannot contain more than 64 paths", field="include")
    normalized = [_relative_path(value, f"include[{index}]") for index, value in enumerate(include)]
    if len(set(normalized)) != len(normalized):
        raise CheckpointError("policy_invalid", "policy include contains a duplicate path", field="include")
    pure_paths = [PurePosixPath(value) for value in normalized]
    for index, candidate in enumerate(pure_paths):
        for other_index, other in enumerate(pure_paths):
            if index != other_index and other in candidate.parents:
                raise CheckpointError(
                    "policy_invalid",
                    f"policy paths overlap: {other.as_posix()} and {candidate.as_posix()}",
                    field="include",
                )
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "source": str(path.expanduser().resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "include": normalized,
    }


def _safe_target(root: Path, relative: str) -> Path:
    root = root.expanduser().resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    parent = candidate.parent.resolve(strict=False)
    if parent != root and root not in parent.parents:
        raise CheckpointError(
            "unsafe_path",
            "a selected config path resolves outside the config root",
            details={"path": relative, "config_root": str(root)},
        )
    return candidate


def _validate_node(path: Path, relative: str) -> str:
    mode = path.lstat().st_mode
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISDIR(mode):
        for directory, names, files in os.walk(path, followlinks=False):
            for name in [*names, *files]:
                child = Path(directory) / name
                child_mode = child.lstat().st_mode
                if not (
                    stat.S_ISREG(child_mode)
                    or stat.S_ISDIR(child_mode)
                    or stat.S_ISLNK(child_mode)
                ):
                    raise CheckpointError(
                        "unsupported_config_node",
                        "selected config contains a socket, device, or other unsupported node",
                        details={"path": relative, "node": str(child)},
                    )
        return "directory"
    raise CheckpointError(
        "unsupported_config_node",
        "selected config path is not a file, directory, or symbolic link",
        details={"path": relative},
    )


def _copy_node(source: Path, destination: Path) -> None:
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if source.is_symlink():
        destination.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def _remove_node(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def capture_config(
    config_root: Path,
    policy: dict[str, Any],
    destination: Path,
) -> list[dict[str, str]]:
    root = config_root.expanduser().resolve()
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    entries: list[dict[str, str]] = []
    for relative in policy["include"]:
        source = _safe_target(root, relative)
        if not source.exists() and not source.is_symlink():
            entries.append({"path": relative, "state": "missing"})
            continue
        node_type = _validate_node(source, relative)
        _copy_node(source, destination.joinpath(*PurePosixPath(relative).parts))
        entries.append({"path": relative, "state": "captured", "type": node_type})
    return entries


def restore_config(
    config_root: Path,
    entries: list[dict[str, str]],
    captured_root: Path,
    backup_root: Path,
) -> dict[str, Any]:
    root = config_root.expanduser().resolve()
    targets: list[tuple[dict[str, str], Path, Path]] = []
    for entry in entries:
        relative = _relative_path(entry.get("path"), "checkpoint.scope.user_config.entries.path")
        state = entry.get("state")
        if state not in {"captured", "missing"}:
            raise CheckpointError("store_error", f"checkpoint has invalid config state for {relative}")
        target = _safe_target(root, relative)
        captured = captured_root.joinpath(*PurePosixPath(relative).parts)
        if state == "captured" and not captured.exists() and not captured.is_symlink():
            raise CheckpointError("store_error", f"checkpoint data is missing for {relative}")
        if state == "captured":
            _validate_node(captured, relative)
        targets.append((entry, target, captured))

    backup_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    backup_entries: list[dict[str, str]] = []
    try:
        for entry, target, _ in targets:
            relative = entry["path"]
            if target.exists() or target.is_symlink():
                _validate_node(target, relative)
                _copy_node(target, backup_root.joinpath(*PurePosixPath(relative).parts))
                backup_entries.append({"path": relative, "state": "captured"})
            else:
                backup_entries.append({"path": relative, "state": "missing"})
        for entry, target, captured in targets:
            _remove_node(target)
            if entry["state"] == "captured":
                _copy_node(captured, target)
    except (CheckpointError, OSError) as error:
        rollback_errors: list[str] = []
        for backup in reversed(backup_entries):
            try:
                target = _safe_target(root, backup["path"])
                _remove_node(target)
                if backup["state"] == "captured":
                    source = backup_root.joinpath(*PurePosixPath(backup["path"]).parts)
                    _copy_node(source, target)
            except (CheckpointError, OSError) as rollback_error:
                rollback_errors.append(str(rollback_error))
        raise CheckpointError(
            "restore_failed",
            f"user config restore failed: {error}",
            details={"rollback_errors": rollback_errors},
        ) from error
    return {
        "config_root": str(root),
        "restored_paths": [entry["path"] for entry, _, _ in targets],
        "pre_restore_backup": str(backup_root),
    }
