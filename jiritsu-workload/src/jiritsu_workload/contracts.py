from __future__ import annotations

import os
import re
import tempfile
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, Iterable

from .model import Capability, Check, ContractError, SCHEMA_VERSION, WorkloadContract


ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
CHECK_TYPES = {"command", "command_available", "environment", "path", "systemd_unit"}
IMPORTANCE_VALUES = {"critical", "useful"}
PATH_KIND_VALUES = {"any", "file", "directory", "executable"}
SYSTEMD_SCOPE_VALUES = {"system", "user"}
SYSTEMD_STATE_VALUES = {"active", "enabled", "failed", "inactive"}
STDOUT_MATCH_VALUES = {"any", "nonempty", "empty"}


def user_config_dir(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    override = env.get("JIRITSU_WORKLOAD_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    xdg_root = env.get("XDG_CONFIG_HOME")
    root = Path(xdg_root).expanduser() if xdg_root else Path.home() / ".config"
    return root / "jiritsu" / "workloads.d"


def _require_string(payload: dict[str, Any], field: str, path: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            "contract_invalid",
            f"{field} must be a nonempty string",
            path=path,
            field=field,
        )
    return value


def _require_id(payload: dict[str, Any], field: str, path: str) -> str:
    value = _require_string(payload, field, path)
    if not ID_PATTERN.fullmatch(value):
        raise ContractError(
            "contract_invalid",
            f"{field} must use lowercase letters, numbers, dots, underscores, or hyphens",
            path=path,
            field=field,
        )
    return value


def _require_bool(payload: dict[str, Any], field: str, path: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ContractError(
            "contract_invalid", f"{field} must be a boolean", path=path, field=field
        )
    return value


def _positive_number(payload: dict[str, Any], field: str, path: str) -> float | None:
    if field not in payload:
        return None
    value = payload[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ContractError(
            "contract_invalid",
            f"{field} must be a positive number",
            path=path,
            field=field,
        )
    return float(value)


def _check_parameters(payload: dict[str, Any], check_type: str, path: str) -> dict[str, Any]:
    common = {"id", "type", "description"}
    result: dict[str, Any]
    allowed: set[str]

    if check_type == "command_available":
        command = _require_string(payload, "command", path)
        result = {"command": command}
        allowed = common | {"command"}
    elif check_type == "environment":
        name = _require_string(payload, "name", path)
        result = {"name": name}
        if "equals" in payload:
            if not isinstance(payload["equals"], str):
                raise ContractError(
                    "contract_invalid", "equals must be a string", path=path, field="equals"
                )
            result["equals"] = payload["equals"]
        if "nonempty" in payload:
            result["nonempty"] = _require_bool(payload, "nonempty", path)
        allowed = common | {"name", "equals", "nonempty"}
    elif check_type == "path":
        target = _require_string(payload, "path", path)
        kind = payload.get("kind", "any")
        if kind not in PATH_KIND_VALUES:
            raise ContractError(
                "contract_invalid",
                f"kind must be one of {sorted(PATH_KIND_VALUES)}",
                path=path,
                field="kind",
            )
        result = {"path": target, "kind": kind}
        allowed = common | {"path", "kind"}
    elif check_type == "systemd_unit":
        unit = _require_string(payload, "unit", path)
        scope = payload.get("scope", "user")
        state = payload.get("state", "active")
        if scope not in SYSTEMD_SCOPE_VALUES:
            raise ContractError(
                "contract_invalid",
                f"scope must be one of {sorted(SYSTEMD_SCOPE_VALUES)}",
                path=path,
                field="scope",
            )
        if state not in SYSTEMD_STATE_VALUES:
            raise ContractError(
                "contract_invalid",
                f"state must be one of {sorted(SYSTEMD_STATE_VALUES)}",
                path=path,
                field="state",
            )
        result = {"unit": unit, "scope": scope, "state": state}
        allowed = common | {"unit", "scope", "state"}
    else:
        command = payload.get("command")
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(argument, str) or not argument for argument in command)
        ):
            raise ContractError(
                "contract_invalid",
                "command must be a nonempty array of nonempty strings",
                path=path,
                field="command",
            )
        expected_exit = payload.get("expected_exit", 0)
        if isinstance(expected_exit, bool) or not isinstance(expected_exit, int):
            raise ContractError(
                "contract_invalid",
                "expected_exit must be an integer",
                path=path,
                field="expected_exit",
            )
        stdout_match = payload.get("stdout", "any")
        if stdout_match not in STDOUT_MATCH_VALUES:
            raise ContractError(
                "contract_invalid",
                f"stdout must be one of {sorted(STDOUT_MATCH_VALUES)}",
                path=path,
                field="stdout",
            )
        result = {
            "command": command,
            "expected_exit": expected_exit,
            "stdout": stdout_match,
        }
        timeout = _positive_number(payload, "timeout_seconds", path)
        if timeout is not None:
            result["timeout_seconds"] = timeout
        allowed = common | {"command", "expected_exit", "stdout", "timeout_seconds"}

    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ContractError(
            "contract_invalid",
            f"unknown check field: {unknown[0]}",
            path=path,
            field=unknown[0],
        )
    return result


def parse_contract(
    payload: Any, *, source: str, source_kind: str = "explicit"
) -> WorkloadContract:
    if not isinstance(payload, dict):
        raise ContractError("contract_invalid", "contract root must be a table", path=source)
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ContractError(
            "contract_invalid",
            f'schema_version must be "{SCHEMA_VERSION}"',
            path=source,
            field="schema_version",
        )
    contract_id = _require_id(payload, "id", source)
    title = _require_string(payload, "title", source)
    description = _require_string(payload, "description", source)
    raw_capabilities = payload.get("capabilities")
    if not isinstance(raw_capabilities, list) or not raw_capabilities:
        raise ContractError(
            "contract_invalid",
            "capabilities must contain at least one capability",
            path=source,
            field="capabilities",
        )
    unknown_root = sorted(set(payload) - {"schema_version", "id", "title", "description", "capabilities"})
    if unknown_root:
        raise ContractError(
            "contract_invalid",
            f"unknown contract field: {unknown_root[0]}",
            path=source,
            field=unknown_root[0],
        )

    capabilities: list[Capability] = []
    capability_ids: set[str] = set()
    for index, raw_capability in enumerate(raw_capabilities):
        field_path = f"capabilities[{index}]"
        if not isinstance(raw_capability, dict):
            raise ContractError(
                "contract_invalid", "capability must be a table", path=source, field=field_path
            )
        capability_id = _require_id(raw_capability, "id", source)
        if capability_id in capability_ids:
            raise ContractError(
                "contract_invalid",
                f"duplicate capability id: {capability_id}",
                path=source,
                field=field_path,
            )
        capability_ids.add(capability_id)
        importance = raw_capability.get("importance")
        if importance not in IMPORTANCE_VALUES:
            raise ContractError(
                "contract_invalid",
                f"importance must be one of {sorted(IMPORTANCE_VALUES)}",
                path=source,
                field=f"{field_path}.importance",
            )
        raw_checks = raw_capability.get("checks")
        if not isinstance(raw_checks, list) or not raw_checks:
            raise ContractError(
                "contract_invalid",
                "checks must contain at least one check",
                path=source,
                field=f"{field_path}.checks",
            )
        unknown_capability = sorted(
            set(raw_capability) - {"id", "title", "description", "importance", "checks"}
        )
        if unknown_capability:
            raise ContractError(
                "contract_invalid",
                f"unknown capability field: {unknown_capability[0]}",
                path=source,
                field=f"{field_path}.{unknown_capability[0]}",
            )

        checks: list[Check] = []
        check_ids: set[str] = set()
        for check_index, raw_check in enumerate(raw_checks):
            check_path = f"{field_path}.checks[{check_index}]"
            if not isinstance(raw_check, dict):
                raise ContractError(
                    "contract_invalid", "check must be a table", path=source, field=check_path
                )
            check_id = _require_id(raw_check, "id", source)
            if check_id in check_ids:
                raise ContractError(
                    "contract_invalid",
                    f"duplicate check id: {check_id}",
                    path=source,
                    field=check_path,
                )
            check_ids.add(check_id)
            check_type = raw_check.get("type")
            if check_type not in CHECK_TYPES:
                raise ContractError(
                    "contract_invalid",
                    f"type must be one of {sorted(CHECK_TYPES)}",
                    path=source,
                    field=f"{check_path}.type",
                )
            checks.append(
                Check(
                    check_id=check_id,
                    check_type=check_type,
                    description=_require_string(raw_check, "description", source),
                    parameters=_check_parameters(raw_check, check_type, source),
                )
            )
        capabilities.append(
            Capability(
                capability_id=capability_id,
                title=_require_string(raw_capability, "title", source),
                description=_require_string(raw_capability, "description", source),
                importance=importance,
                checks=tuple(checks),
            )
        )
    return WorkloadContract(
        contract_id=contract_id,
        title=title,
        description=description,
        capabilities=tuple(capabilities),
        source=source,
        source_kind=source_kind,  # type: ignore[arg-type]
    )


def load_contract(path: str | Path, *, source_kind: str = "explicit") -> WorkloadContract:
    contract_path = Path(path)
    try:
        with contract_path.open("rb") as contract_file:
            payload = tomllib.load(contract_file)
    except FileNotFoundError as error:
        raise ContractError("contract_not_found", "contract file does not exist", path=str(path)) from error
    except PermissionError as error:
        raise ContractError("contract_denied", "cannot read contract file", path=str(path)) from error
    except tomllib.TOMLDecodeError as error:
        raise ContractError("contract_invalid", f"invalid TOML: {error}", path=str(path)) from error
    except OSError as error:
        raise ContractError("contract_unreadable", f"cannot read contract file: {error}", path=str(path)) from error
    return parse_contract(payload, source=str(contract_path), source_kind=source_kind)


def default_contract_paths() -> list[Path]:
    directory = resources.files("jiritsu_workload").joinpath("defaults")
    return sorted(Path(str(entry)) for entry in directory.iterdir() if entry.name.endswith(".toml"))


def discover_contracts(config_dir: Path | None = None) -> list[WorkloadContract]:
    contracts: dict[str, WorkloadContract] = {}
    for path in default_contract_paths():
        contract = load_contract(path, source_kind="default")
        contracts[contract.contract_id] = contract
    directory = user_config_dir() if config_dir is None else config_dir
    if directory.exists():
        if not directory.is_dir():
            raise ContractError(
                "config_invalid", "the config path is not a directory", path=str(directory)
            )
        user_sources: dict[str, str] = {}
        for path in sorted(directory.glob("*.toml")):
            contract = load_contract(path, source_kind="user")
            if contract.contract_id in user_sources:
                raise ContractError(
                    "duplicate_workload",
                    f"user contract ID also occurs in {user_sources[contract.contract_id]}",
                    path=str(path),
                    field="id",
                )
            user_sources[contract.contract_id] = str(path)
            contracts[contract.contract_id] = contract
    return [contracts[contract_id] for contract_id in sorted(contracts)]


def select_contracts(
    contracts: Iterable[WorkloadContract], selectors: Iterable[str]
) -> list[WorkloadContract]:
    available = {contract.contract_id: contract for contract in contracts}
    selected_ids = list(selectors)
    if not selected_ids:
        return [available[contract_id] for contract_id in sorted(available)]
    unknown = [selector for selector in selected_ids if selector not in available]
    if unknown:
        raise ContractError(
            "unknown_workload", f"unknown workload: {unknown[0]}", field="selectors"
        )
    return [available[contract_id] for contract_id in dict.fromkeys(selected_ids)]


def install_contract(source_path: Path, destination_dir: Path) -> tuple[str, WorkloadContract, Path]:
    contract = load_contract(source_path, source_kind="explicit")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{contract.contract_id}.toml"
    action = "updated" if destination.exists() else "created"
    try:
        content = source_path.read_bytes()
        with tempfile.NamedTemporaryFile(dir=destination_dir, delete=False) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        temporary_path.chmod(0o600)
        temporary_path.replace(destination)
    except OSError as error:
        if "temporary_path" in locals():
            temporary_path.unlink(missing_ok=True)
        raise ContractError(
            "contract_write_failed", f"cannot write contract: {error}", path=str(destination)
        ) from error
    installed = load_contract(destination, source_kind="user")
    return action, installed, destination
