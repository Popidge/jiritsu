from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


STATED_SCHEMA_VERSION = "1.0"
OUTPUT_LIMIT = 500


def _short_output(value: str) -> str:
    stripped = value.strip()
    if len(stripped) <= OUTPUT_LIMIT:
        return stripped
    return stripped[: OUTPUT_LIMIT - 3] + "..."


@dataclass(frozen=True)
class StatedSnapshot:
    status: Literal[
        "disabled", "failed", "not_required", "partial", "unavailable", "used"
    ]
    requested_facts: tuple[str, ...]
    facts: dict[str, dict[str, Any]]
    errors: tuple[dict[str, Any], ...]
    command: str | None = None
    query_status: str | None = None
    message: str | None = None

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "requested_facts": list(self.requested_facts),
            "fact_count": len(self.facts),
        }
        if self.command is not None:
            result["command"] = self.command
        if self.query_status is not None:
            result["query_status"] = self.query_status
        if self.message is not None:
            result["message"] = self.message
        if self.errors:
            result["errors"] = list(self.errors)
        return result


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
    if module_root.name == "jiritsu-workload":
        sibling = module_root.parent / "jiritsu-stated" / "bin" / "jiritsu-stated"
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling)
    return None


def _invoke(
    command: list[str], timeout_seconds: float
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C.UTF-8"
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
        env=environment,
    )


def collect_stated_facts(
    fact_ids: set[str],
    *,
    timeout_seconds: float = 5.0,
    stated_command: str | None = None,
    enabled: bool = True,
) -> StatedSnapshot:
    requested = tuple(sorted(fact_ids))
    if not requested:
        return StatedSnapshot("not_required", requested, {}, ())
    if not enabled:
        return StatedSnapshot(
            "disabled",
            requested,
            {},
            (),
            message="jiritsu-stated use was disabled for this assessment",
        )
    executable = resolve_stated_command(stated_command)
    if executable is None:
        return StatedSnapshot(
            "unavailable",
            requested,
            {},
            (),
            message="jiritsu-stated is not available",
        )
    command = [
        executable,
        "query",
        *requested,
        "--timeout",
        f"{timeout_seconds:g}",
    ]
    outer_timeout = timeout_seconds * max(1, len(requested)) + 2.0
    try:
        completed = _invoke(command, outer_timeout)
    except FileNotFoundError:
        return StatedSnapshot(
            "unavailable",
            requested,
            {},
            (),
            command=executable,
            message="jiritsu-stated is not available",
        )
    except subprocess.TimeoutExpired:
        return StatedSnapshot(
            "failed",
            requested,
            {},
            (),
            command=executable,
            message=f"jiritsu-stated did not finish within {outer_timeout:g} seconds",
        )
    except (OSError, UnicodeError) as error:
        return StatedSnapshot(
            "failed",
            requested,
            {},
            (),
            command=executable,
            message=f"jiritsu-stated could not run: {error}",
        )

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        detail = _short_output(completed.stderr or completed.stdout)
        suffix = f": {detail}" if detail else ""
        return StatedSnapshot(
            "failed",
            requested,
            {},
            (),
            command=executable,
            message=f"jiritsu-stated returned invalid JSON{suffix}",
        )
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != STATED_SCHEMA_VERSION
    ):
        return StatedSnapshot(
            "failed",
            requested,
            {},
            (),
            command=executable,
            message=f'jiritsu-stated must return schema_version "{STATED_SCHEMA_VERSION}"',
        )
    facts = payload.get("facts")
    errors = payload.get("errors")
    query_status = payload.get("status")
    if (
        not isinstance(facts, dict)
        or not isinstance(errors, list)
        or any(not isinstance(error, dict) for error in errors)
        or query_status not in {"ok", "partial", "error"}
    ):
        return StatedSnapshot(
            "failed",
            requested,
            {},
            (),
            command=executable,
            message="jiritsu-stated returned an invalid response",
        )
    valid_facts: dict[str, dict[str, Any]] = {}
    for fact_id, entry in facts.items():
        if fact_id in fact_ids and isinstance(entry, dict) and "value" in entry:
            valid_facts[fact_id] = entry
    public_errors = tuple(errors)
    missing = set(requested) - set(valid_facts)
    if not valid_facts and (completed.returncode != 0 or query_status == "error"):
        status = "failed"
    elif missing or query_status == "partial" or completed.returncode == 2:
        status = "partial"
    elif completed.returncode == 0 and query_status == "ok":
        status = "used"
    else:
        status = "failed"
    message = None
    if status == "partial":
        message = "jiritsu-stated returned only some requested facts"
    elif status == "failed":
        detail = _short_output(completed.stderr)
        message = f"jiritsu-stated query failed with status {completed.returncode}"
        if detail:
            message += f": {detail}"
    return StatedSnapshot(
        status,
        requested,
        valid_facts,
        public_errors,
        command=executable,
        query_status=query_status,
        message=message,
    )
