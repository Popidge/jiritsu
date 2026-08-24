from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .model import Check
from .state import StatedSnapshot


OUTPUT_LIMIT = 500


def _short_output(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= OUTPUT_LIMIT:
        return stripped
    return stripped[: OUTPUT_LIMIT - 3] + "..."


def _result(
    check: Check,
    status: str,
    message: str,
    started: float,
    source: str = "direct_probe",
    **details: Any,
) -> dict[str, Any]:
    return {
        "id": check.check_id,
        "type": check.check_type,
        "status": status,
        "source": source,
        "message": message,
        "duration_ms": round((time.monotonic() - started) * 1000, 3),
        "details": details,
    }


def _command_available(check: Check, started: float) -> dict[str, Any]:
    command = check.parameters["command"]
    executable = shutil.which(command)
    if executable is None:
        return _result(
            check,
            "fail",
            f"Command is not available: {command}",
            started,
            command=command,
        )
    return _result(
        check,
        "pass",
        f"Command is available: {command}",
        started,
        command=command,
        executable=executable,
    )


def _environment(check: Check, started: float) -> dict[str, Any]:
    name = check.parameters["name"]
    present = name in os.environ
    value = os.environ.get(name, "")
    if "equals" in check.parameters:
        passed = present and value == check.parameters["equals"]
        expectation = "has the required value"
    else:
        nonempty = check.parameters.get("nonempty", True)
        passed = present and bool(value) if nonempty else present
        expectation = "is present and nonempty" if nonempty else "is present"
    status = "pass" if passed else "fail"
    return _result(
        check,
        status,
        f"Environment variable {name} {expectation}"
        if passed
        else f"Environment variable {name} does not meet the contract",
        started,
        name=name,
        present=present,
        nonempty=bool(value),
    )


def _path(check: Check, started: float) -> dict[str, Any]:
    raw_path = check.parameters["path"]
    path = Path(raw_path).expanduser()
    kind = check.parameters["kind"]
    if kind == "any":
        passed = path.exists()
    elif kind == "file":
        passed = path.is_file()
    elif kind == "directory":
        passed = path.is_dir()
    else:
        passed = path.is_file() and os.access(path, os.X_OK)
    status = "pass" if passed else "fail"
    return _result(
        check,
        status,
        f"Path meets the {kind} requirement"
        if passed
        else f"Path does not meet the {kind} requirement",
        started,
        path=str(path),
        kind=kind,
    )


def _run_command(
    check: Check, started: float, default_timeout_seconds: float
) -> dict[str, Any]:
    command = check.parameters["command"]
    timeout = check.parameters.get("timeout_seconds", default_timeout_seconds)
    environment = os.environ.copy()
    environment["LC_ALL"] = "C.UTF-8"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env=environment,
        )
    except FileNotFoundError:
        return _result(
            check,
            "fail",
            f"Command is not available: {command[0]}",
            started,
            command=command,
        )
    except subprocess.TimeoutExpired:
        return _result(
            check,
            "error",
            f"Command did not finish within {timeout:g} seconds",
            started,
            command=command,
            timeout_seconds=timeout,
        )
    except (OSError, UnicodeError) as error:
        return _result(
            check,
            "error",
            f"Command could not start: {error}",
            started,
            command=command,
        )

    expected_exit = check.parameters["expected_exit"]
    stdout_match = check.parameters["stdout"]
    exit_matches = completed.returncode == expected_exit
    if stdout_match == "nonempty":
        stdout_matches = bool(completed.stdout.strip())
    elif stdout_match == "empty":
        stdout_matches = not completed.stdout.strip()
    else:
        stdout_matches = True
    passed = exit_matches and stdout_matches
    if passed:
        message = "Command met the exit and output requirements"
    elif not exit_matches:
        message = f"Command exited with status {completed.returncode}; expected {expected_exit}"
    else:
        message = f"Command stdout did not meet the {stdout_match} requirement"
    details: dict[str, Any] = {
        "command": command,
        "exit_code": completed.returncode,
        "expected_exit": expected_exit,
        "stdout_requirement": stdout_match,
    }
    stdout = _short_output(completed.stdout)
    stderr = _short_output(completed.stderr)
    if stdout:
        details["stdout"] = stdout
    if stderr:
        details["stderr"] = stderr
    return _result(check, "pass" if passed else "fail", message, started, **details)


def _systemd_unit(
    check: Check, started: float, default_timeout_seconds: float
) -> dict[str, Any]:
    scope = check.parameters["scope"]
    state = check.parameters["state"]
    unit = check.parameters["unit"]
    command = ["systemctl"]
    if scope == "user":
        command.append("--user")
    if state == "enabled":
        command.extend(["is-enabled", "--quiet", unit])
        expected_exit = 0
        stdout = "any"
    else:
        command.extend(["show", "--property=ActiveState", "--value", unit])
        expected_exit = 0
        stdout = "nonempty"
    adapted = Check(
        check_id=check.check_id,
        check_type="command",
        description=check.description,
        parameters={
            "command": command,
            "expected_exit": expected_exit,
            "stdout": stdout,
            "timeout_seconds": default_timeout_seconds,
        },
    )
    result = _run_command(adapted, started, default_timeout_seconds)
    result["type"] = "systemd_unit"
    result["details"].update({"scope": scope, "state": state, "unit": unit})
    if result["status"] == "pass" and state != "enabled":
        actual_state = result["details"].get("stdout")
        result["details"]["actual_state"] = actual_state
        if actual_state != state:
            result["status"] = "fail"
    if result["status"] == "pass":
        result["message"] = f"Unit {unit} is {state}"
    elif result["status"] == "fail":
        result["message"] = f"Unit {unit} is not {state}"
    return result


def _extract_fact_path(value: Any, path: str | None) -> Any:
    if path is None:
        return value
    current = [value]
    wildcard = False
    for segment in path.split("."):
        next_values: list[Any] = []
        if segment == "*":
            wildcard = True
            for item in current:
                if not isinstance(item, list):
                    raise ValueError("* requires an array")
                next_values.extend(item)
        else:
            for item in current:
                if not isinstance(item, dict) or segment not in item:
                    raise ValueError(f"field is missing: {segment}")
                next_values.append(item[segment])
        current = next_values
    if wildcard:
        return current
    if len(current) != 1:
        raise ValueError("path did not select one value")
    return current[0]


def _evaluate_fact(actual: Any, operator: str, expected: Any = None) -> bool:
    if operator == "exists":
        return True
    if operator == "nonempty":
        return bool(actual)
    if operator == "equals":
        return actual == expected
    if operator == "not_equals":
        return actual != expected
    if operator == "contains":
        if not isinstance(actual, (str, list, tuple, dict)):
            raise ValueError("contains requires a string, array, or object")
        return expected in actual
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        raise ValueError(f"{operator} requires a numeric fact value")
    if operator == "at_least":
        return actual >= expected
    return actual <= expected


def _fact_details(
    check: Check, entry: dict[str, Any], actual: Any, passed: bool
) -> dict[str, Any]:
    parameters = check.parameters
    details: dict[str, Any] = {
        "fact": parameters["fact"],
        "operator": parameters["operator"],
        "matched": passed,
    }
    if "path" in parameters:
        details["path"] = parameters["path"]
    if "expected" in parameters:
        details["expected"] = parameters["expected"]
    if isinstance(actual, (str, int, float, bool)) or actual is None:
        details["actual"] = actual
    if "source" in entry:
        details["fact_source"] = entry["source"]
    for field in ("observed_at", "age_seconds", "fixture"):
        if field in entry:
            details[field] = entry[field]
    return details


def _stated_fallback(
    check: Check,
    snapshot: StatedSnapshot,
    started: float,
    default_timeout_seconds: float,
) -> dict[str, Any]:
    fallback = check.parameters.get("fallback")
    reason = {
        "stated_status": snapshot.status,
        "fact": check.parameters["fact"],
        "stated_message": snapshot.message or "the requested fact is not available",
    }
    if fallback is None:
        return _result(
            check,
            "error",
            "The stated fact is unavailable and this check has no direct fallback",
            started,
            source="jiritsu-stated",
            **reason,
        )
    fallback_check = Check(
        check_id=check.check_id,
        check_type=fallback["type"],
        description=check.description,
        parameters={key: value for key, value in fallback.items() if key != "type"},
    )
    result = run_check(fallback_check, default_timeout_seconds, snapshot)
    result["type"] = "stated_fact"
    result["message"] = f"Direct fallback: {result['message']}"
    result["details"]["fallback"] = reason
    result["details"]["probe_type"] = fallback["type"]
    return result


def _stated_fact(
    check: Check,
    snapshot: StatedSnapshot,
    started: float,
    default_timeout_seconds: float,
) -> dict[str, Any]:
    fact_id = check.parameters["fact"]
    entry = (
        snapshot.facts.get(fact_id) if snapshot.status in {"used", "partial"} else None
    )
    if entry is None:
        return _stated_fallback(check, snapshot, started, default_timeout_seconds)
    try:
        actual = _extract_fact_path(entry["value"], check.parameters.get("path"))
        passed = _evaluate_fact(
            actual,
            check.parameters["operator"],
            check.parameters.get("expected"),
        )
    except (KeyError, TypeError, ValueError) as error:
        return _result(
            check,
            "error",
            f"The stated fact does not match this check: {error}",
            started,
            source="jiritsu-stated",
            fact=fact_id,
            path=check.parameters.get("path"),
            operator=check.parameters["operator"],
        )
    message = (
        "The stated fact meets the requirement"
        if passed
        else "The stated fact does not meet the requirement"
    )
    return _result(
        check,
        "pass" if passed else "fail",
        message,
        started,
        source="jiritsu-stated",
        **_fact_details(check, entry, actual, passed),
    )


def run_check(
    check: Check,
    default_timeout_seconds: float = 5.0,
    stated_snapshot: StatedSnapshot | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    if check.check_type == "stated_fact":
        snapshot = stated_snapshot or StatedSnapshot(
            "unavailable",
            (check.parameters["fact"],),
            {},
            (),
            message="jiritsu-stated was not initialized",
        )
        return _stated_fact(check, snapshot, started, default_timeout_seconds)
    if check.check_type == "command_available":
        return _command_available(check, started)
    if check.check_type == "environment":
        return _environment(check, started)
    if check.check_type == "path":
        return _path(check, started)
    if check.check_type == "systemd_unit":
        return _systemd_unit(check, started, default_timeout_seconds)
    return _run_command(check, started, default_timeout_seconds)
