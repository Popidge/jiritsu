from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


Runner = Callable[..., subprocess.CompletedProcess[str]]


def run_command(
    command: list[str], *, timeout: float = 10.0
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C.UTF-8"
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=environment,
    )


def resolve_stated_command(explicit: str | None = None) -> str | None:
    if explicit:
        return str(Path(explicit).expanduser())
    environment_command = os.environ.get("JIRITSU_STATED_COMMAND")
    if environment_command:
        return str(Path(environment_command).expanduser())
    installed = shutil.which("jiritsu-stated")
    if installed:
        return installed
    module_root = Path(__file__).resolve().parents[2]
    sibling = module_root.parent / "jiritsu-stated" / "bin" / "jiritsu-stated"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return None


def _error(code: str, message: str, source: str) -> dict[str, str]:
    return {"code": code, "message": message, "source": source}


def _bounded_detail(result: subprocess.CompletedProcess[str]) -> str:
    value = (result.stderr or result.stdout).strip()
    return value if len(value) <= 500 else value[:497] + "..."


def _parse_active_root(payload: Any) -> dict[str, Any]:
    filesystems = payload.get("filesystems") if isinstance(payload, dict) else None
    if not isinstance(filesystems, list) or len(filesystems) != 1:
        raise ValueError("findmnt JSON does not contain one root filesystem")
    record = filesystems[0]
    if not isinstance(record, dict):
        raise ValueError("findmnt root entry is invalid")
    source = record.get("source")
    filesystem = record.get("fstype")
    options = record.get("options", "")
    if not all(isinstance(value, str) for value in (source, filesystem, options)):
        raise ValueError("findmnt root entry lacks source, filesystem, or options")
    source_match = re.fullmatch(r"(.+)\[(.+)]", source)
    device = source_match.group(1) if source_match else source
    source_subvolume = source_match.group(2) if source_match else None
    option_subvolume = next(
        (
            option.split("=", 1)[1]
            for option in options.split(",")
            if option.startswith("subvol=")
        ),
        None,
    )
    subvolume = option_subvolume or source_subvolume
    snapshot = re.search(r"/\.snapshots/(\d+)/snapshot(?:/|$)", subvolume or "")
    return {
        "filesystem": filesystem,
        "device": device,
        "subvolume": subvolume,
        "snapper_snapshot_id": int(snapshot.group(1)) if snapshot else None,
    }


@dataclass(frozen=True)
class MachineState:
    configurations: tuple[dict[str, str], ...]
    active_root: dict[str, Any] | None
    source: str
    stated_status: str
    fallback_errors: tuple[dict[str, Any], ...]

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "jiritsu_stated": self.stated_status,
            "configurations": list(self.configurations),
            "active_root": self.active_root,
            "fallback_errors": list(self.fallback_errors),
        }


def _stated_snapshot(
    executable: str,
    timeout_seconds: float,
    runner: Runner,
) -> tuple[list[dict[str, str]] | None, dict[str, Any] | None, str, list[dict[str, Any]]]:
    command = [
        executable,
        "query",
        "snapshots.configurations",
        "snapshots.active_root",
        "--timeout",
        f"{timeout_seconds:g}",
    ]
    try:
        result = runner(command, timeout=timeout_seconds * 2 + 2)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return None, None, "unavailable", [
            _error("stated_unavailable", f"jiritsu-stated could not run: {error}", "jiritsu-stated")
        ]
    try:
        payload = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        detail = _bounded_detail(result)
        suffix = f": {detail}" if detail else ""
        return None, None, "failed", [
            _error("stated_invalid", f"jiritsu-stated returned invalid JSON{suffix}", "jiritsu-stated")
        ]
    if not isinstance(payload, dict) or payload.get("schema_version") != "1.0":
        return None, None, "failed", [
            _error("stated_invalid", 'jiritsu-stated did not return schema_version "1.0"', "jiritsu-stated")
        ]
    facts = payload.get("facts")
    if not isinstance(facts, dict):
        return None, None, "failed", [
            _error("stated_invalid", "jiritsu-stated facts must be an object", "jiritsu-stated")
        ]
    configs_value = facts.get("snapshots.configurations", {}).get("value")
    root_value = facts.get("snapshots.active_root", {}).get("value")
    configs: list[dict[str, str]] | None = None
    if isinstance(configs_value, list) and all(
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("subvolume"), str)
        for item in configs_value
    ):
        configs = [
            {"name": item["name"], "subvolume": item["subvolume"]}
            for item in configs_value
        ]
    root = root_value if isinstance(root_value, dict) else None
    errors = [
        error
        for error in payload.get("errors", [])
        if isinstance(error, dict)
    ]
    if configs is not None and root is not None:
        status = "used" if not errors and result.returncode == 0 else "partial"
    elif configs is not None or root is not None:
        status = "partial"
    else:
        status = "failed"
    return configs, root, status, errors


def _direct_configurations(
    timeout_seconds: float, runner: Runner
) -> tuple[list[dict[str, str]] | None, dict[str, Any] | None]:
    try:
        result = runner(["snapper", "--jsonout", "list-configs"], timeout=timeout_seconds)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return None, _error("snapper_unavailable", f"Snapper could not run: {error}", "snapper")
    if result.returncode != 0:
        return None, _error(
            "snapper_failed",
            f"Snapper list-configs failed with status {result.returncode}: {_bounded_detail(result)}",
            "snapper",
        )
    try:
        payload = json.loads(result.stdout)
        raw = payload["configs"]
        if not isinstance(raw, list):
            raise TypeError
        configurations = [
            {"name": item["config"], "subvolume": item["subvolume"]}
            for item in raw
            if isinstance(item, dict)
            and isinstance(item.get("config"), str)
            and isinstance(item.get("subvolume"), str)
        ]
        if len(configurations) != len(raw):
            raise TypeError
        return configurations, None
    except (json.JSONDecodeError, KeyError, TypeError):
        return None, _error("snapper_invalid", "Snapper returned invalid list-configs JSON", "snapper")


def _direct_active_root(
    timeout_seconds: float, runner: Runner
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        result = runner(
            ["findmnt", "--json", "--output", "SOURCE,FSTYPE,OPTIONS", "--target", "/"],
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as error:
        return None, _error("findmnt_unavailable", f"findmnt could not run: {error}", "findmnt")
    if result.returncode != 0:
        return None, _error(
            "findmnt_failed",
            f"findmnt failed with status {result.returncode}: {_bounded_detail(result)}",
            "findmnt",
        )
    try:
        return _parse_active_root(json.loads(result.stdout)), None
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return None, _error("findmnt_invalid", f"findmnt returned invalid JSON: {error}", "findmnt")


def discover_machine_state(
    *,
    stated_command: str | None = None,
    state_source: str = "auto",
    timeout_seconds: float = 5.0,
    runner: Runner = run_command,
) -> MachineState:
    configurations: list[dict[str, str]] | None = None
    active_root: dict[str, Any] | None = None
    errors: list[dict[str, Any]] = []
    stated_status = "disabled" if state_source == "direct" else "unavailable"
    used_stated = False
    used_direct = False

    if state_source != "direct":
        executable = resolve_stated_command(stated_command)
        if executable is None:
            errors.append(_error("stated_unavailable", "jiritsu-stated is not available", "jiritsu-stated"))
        else:
            configurations, active_root, stated_status, stated_errors = _stated_snapshot(
                executable, timeout_seconds, runner
            )
            errors.extend(stated_errors)
            used_stated = configurations is not None or active_root is not None

    if configurations is None:
        configurations, error = _direct_configurations(timeout_seconds, runner)
        used_direct = configurations is not None
        if error:
            errors.append(error)
    if active_root is None:
        active_root, error = _direct_active_root(timeout_seconds, runner)
        used_direct = used_direct or active_root is not None
        if error:
            errors.append(error)

    if used_stated and used_direct:
        source = "hybrid"
    elif used_stated:
        source = "jiritsu-stated"
    elif used_direct:
        source = "direct_probes"
    else:
        source = "unavailable"
    return MachineState(
        tuple(configurations or ()), active_root, source, stated_status, tuple(errors)
    )
