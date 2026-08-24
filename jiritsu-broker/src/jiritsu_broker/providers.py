from __future__ import annotations

import json
import os
import platform
import shlex
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import timestamp


OUTPUT_LIMIT = 500


def _short(value: str) -> str:
    stripped = value.strip()
    return (
        stripped
        if len(stripped) <= OUTPUT_LIMIT
        else stripped[: OUTPUT_LIMIT - 3] + "..."
    )


def _sibling_command(module: str, binary: str) -> str | None:
    module_root = Path(__file__).resolve().parents[2]
    candidate = module_root.parent / module / "bin" / binary
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def resolve_command(binary: str, sibling_module: str) -> str | None:
    key = f"JIRITSU_BROKER_{binary.upper().replace('-', '_')}_COMMAND"
    explicit = os.environ.get(key)
    if explicit:
        return str(Path(explicit).expanduser())
    installed = shutil.which(binary)
    return installed or _sibling_command(sibling_module, binary)


def _child_environment() -> dict[str, str]:
    allowed = {
        "HOME",
        "PATH",
        "LANG",
        "LC_ALL",
        "XDG_CONFIG_HOME",
        "XDG_STATE_HOME",
        "XDG_RUNTIME_DIR",
        "JIRITSU_PROPOSALS_STATE_DIR",
        "JIRITSU_PROPOSALS_CONFIG_ROOT",
        "JIRITSU_WORKLOAD_CONFIG_DIR",
        "JIRITSU_CHECKPOINTS_STATE_DIR",
        "JIRITSU_STATED_COMMAND",
        "JIRITSU_WORKLOAD_COMMAND",
        "JIRITSU_CHECKPOINTS_COMMAND",
    }
    environment = {key: value for key, value in os.environ.items() if key in allowed}
    environment["LC_ALL"] = "C.UTF-8"
    return environment


def _run(
    command: list[str], timeout: float, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=_child_environment(),
    )


@dataclass(frozen=True)
class ProviderResult:
    status: str
    selected_provider: str
    source: str | None
    fallback_errors: tuple[dict[str, Any], ...]
    data: dict[str, Any] | None

    def public(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "selected_provider": self.selected_provider,
            "source": self.source,
            "fallback_errors": list(self.fallback_errors),
            "data": self.data,
        }


def _provider_error(
    code: str, provider: str, message: str, source: str | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {"code": code, "provider": provider, "message": message}
    if source is not None:
        result["source"] = source
    return result


def _parse_json_result(
    completed: subprocess.CompletedProcess[str], provider: str, source: str
) -> dict[str, Any]:
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON response: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), str):
        raise ValueError("response root must contain a string status")
    if completed.returncode not in {0, 1, 2, 64, 65}:
        detail = _short(completed.stderr or completed.stdout)
        raise ValueError(
            detail
            or f"{provider} exited with unsupported status {completed.returncode}"
        )
    return payload


BASELINE_FACTS = (
    "system.hostname",
    "system.os",
    "system.kernel",
    "system.omarchy.version",
)


def _selected_baseline_facts(selectors: list[str]) -> tuple[list[str], list[str]]:
    if not selectors:
        return list(BASELINE_FACTS), []
    selected: list[str] = []
    unsupported: list[str] = []
    for selector in selectors:
        if selector == "system":
            selected.extend(BASELINE_FACTS)
        elif selector in BASELINE_FACTS:
            selected.append(selector)
        else:
            unsupported.append(selector)
    return list(dict.fromkeys(selected)), list(dict.fromkeys(unsupported))


def _parse_os_release() -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", 1)
        values = shlex.split(raw)
        fields[key] = values[0] if values else ""
    if "ID" not in fields or "NAME" not in fields:
        raise ValueError("os-release lacks ID or NAME")
    result = {"id": fields["ID"], "name": fields["NAME"]}
    for source, target in (
        ("VERSION_ID", "version_id"),
        ("VERSION", "version"),
        ("PRETTY_NAME", "pretty_name"),
    ):
        if source in fields:
            result[target] = fields[source]
    return result


def _baseline_value(fact_id: str, timeout: float) -> tuple[Any, dict[str, str]]:
    if fact_id == "system.hostname":
        return socket.gethostname(), {
            "id": "linux.hostname",
            "kind": "system",
            "locator": "socket.gethostname",
        }
    if fact_id == "system.os":
        return _parse_os_release(), {
            "id": "linux.os_release",
            "kind": "file",
            "locator": "/etc/os-release",
        }
    if fact_id == "system.kernel":
        return {
            "name": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
        }, {
            "id": "linux.uname",
            "kind": "system",
            "locator": "platform.uname",
        }
    omarchy = shutil.which("omarchy")
    if omarchy is None:
        raise ValueError("the Omarchy command is unavailable")
    completed = _run([omarchy, "version"], timeout)
    if completed.returncode != 0 or not completed.stdout.strip():
        raise ValueError(
            _short(completed.stderr or completed.stdout)
            or f"Omarchy exited with status {completed.returncode}"
        )
    return completed.stdout.strip(), {
        "id": "omarchy.version",
        "kind": "command",
        "locator": f"{omarchy} version",
    }


def _baseline_state(
    selectors: list[str], timeout: float, fallback_errors: list[dict[str, Any]]
) -> ProviderResult:
    selected, unsupported = _selected_baseline_facts(selectors)
    facts: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    observed = timestamp()
    for fact_id in selected:
        try:
            value, source = _baseline_value(fact_id, timeout)
            facts[fact_id] = {
                "value": value,
                "source": source,
                "observed_at": observed,
                "age_seconds": 0.0,
                "fixture": False,
            }
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            errors.append(
                {
                    "code": "source_unavailable",
                    "message": str(error),
                    "fact_id": fact_id,
                    "retryable": isinstance(error, subprocess.TimeoutExpired),
                }
            )
    for selector in unsupported:
        errors.append(
            {
                "code": "selector_unavailable",
                "message": f"the baseline provider does not implement selector: {selector}",
                "selector": selector,
                "retryable": False,
            }
        )
    if not selectors:
        errors.append(
            {
                "code": "provider_scope_limited",
                "message": "the baseline provider reports only the system fact group",
                "retryable": False,
            }
        )
    status = "ok" if not errors else ("partial" if facts else "error")
    payload = {
        "schema_version": "1.0",
        "status": status,
        "collected_at": timestamp(),
        "query": {"selectors": selectors},
        "facts": facts,
        "errors": errors,
    }
    return ProviderResult(
        status="error" if status == "error" else "ok",
        selected_provider="baseline",
        source="Omarchy and standard Linux",
        fallback_errors=tuple(fallback_errors),
        data=payload,
    )


def query_state(selectors: list[str], timeout: float) -> ProviderResult:
    executable = resolve_command("jiritsu-stated", "jiritsu-stated")
    fallback_errors: list[dict[str, Any]] = []
    if executable is None:
        fallback_errors.append(
            _provider_error(
                "provider_unavailable",
                "jiritsu-stated",
                "jiritsu-stated is unavailable",
            )
        )
        return _baseline_state(selectors, timeout, fallback_errors)
    command = [executable, "query", *selectors, "--timeout", f"{timeout:g}"]
    try:
        completed = _run(command, timeout * 16 + 5)
        payload = _parse_json_result(completed, "jiritsu-stated", executable)
        if payload.get("schema_version") != "1.0" or not isinstance(
            payload.get("facts"), dict
        ):
            raise ValueError('response does not use the jiritsu-stated "1.0" schema')
        return ProviderResult(
            status="error" if payload.get("status") == "error" else "ok",
            selected_provider="jiritsu-stated",
            source=executable,
            fallback_errors=(),
            data=payload,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        fallback_errors.append(
            _provider_error("provider_failed", "jiritsu-stated", str(error), executable)
        )
        return _baseline_state(selectors, timeout, fallback_errors)


def assess_workload(selectors: list[str], timeout: float) -> ProviderResult:
    executable = resolve_command("jiritsu-workload", "jiritsu-workload")
    if executable is None:
        error = _provider_error(
            "provider_unavailable",
            "jiritsu-workload",
            "jiritsu-workload is unavailable and has no equivalent baseline contract provider",
        )
        return ProviderResult("error", "none", None, (error,), None)
    command = [executable, "assess", *selectors, "--timeout", f"{timeout:g}"]
    try:
        completed = _run(command, max(timeout * 20 + 5, 10))
        payload = _parse_json_result(completed, "jiritsu-workload", executable)
        if payload.get("schema_version") != "1.1" or payload.get("status") not in {
            "healthy",
            "degraded",
            "unhealthy",
            "error",
        }:
            raise ValueError('response does not use the jiritsu-workload "1.1" schema')
        return ProviderResult(
            "error" if payload["status"] == "error" else "ok",
            "jiritsu-workload",
            executable,
            (),
            payload,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        failure = _provider_error(
            "provider_failed", "jiritsu-workload", str(error), executable
        )
        return ProviderResult("error", "none", executable, (failure,), None)


def run_proposal(
    arguments: list[str], timeout: float, *, input_text: str | None = None
) -> ProviderResult:
    executable = resolve_command("jiritsu-proposals", "jiritsu-proposals")
    if executable is None:
        error = _provider_error(
            "provider_unavailable",
            "jiritsu-proposals",
            "jiritsu-proposals is unavailable; no proposal action was performed",
        )
        return ProviderResult("error", "none", None, (error,), None)
    try:
        completed = _run([executable, *arguments], timeout, input_text)
        payload = _parse_json_result(completed, "jiritsu-proposals", executable)
        if payload.get("schema_version") != "1.0":
            raise ValueError('response does not use the jiritsu-proposals "1.0" schema')
        return ProviderResult(
            "error" if payload.get("status") == "error" else "ok",
            "jiritsu-proposals",
            executable,
            (),
            payload,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        failure = _provider_error(
            "provider_failed", "jiritsu-proposals", str(error), executable
        )
        return ProviderResult("error", "none", executable, (failure,), None)


def run_checkpoint(arguments: list[str], timeout: float) -> ProviderResult:
    executable = resolve_command("jiritsu-checkpoints", "jiritsu-checkpoints")
    if executable is None:
        error = _provider_error(
            "provider_unavailable",
            "jiritsu-checkpoints",
            "jiritsu-checkpoints is unavailable; no checkpoint operation was performed",
        )
        return ProviderResult("error", "none", None, (error,), None)
    try:
        completed = _run([executable, *arguments], timeout)
        payload = _parse_json_result(completed, "jiritsu-checkpoints", executable)
        if payload.get("schema_version") != "1.0":
            raise ValueError(
                'response does not use the jiritsu-checkpoints "1.0" schema'
            )
        return ProviderResult(
            "error" if payload.get("status") == "error" else "ok",
            "jiritsu-checkpoints",
            executable,
            (),
            payload,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        failure = _provider_error(
            "provider_failed", "jiritsu-checkpoints", str(error), executable
        )
        return ProviderResult("error", "none", executable, (failure,), None)
