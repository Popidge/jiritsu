from __future__ import annotations

import json
import hashlib
import os
import shutil
import socket
import subprocess
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any


OUTPUT_LIMIT = 500


def _short(value: str) -> str:
    value = value.strip()
    return value if len(value) <= OUTPUT_LIMIT else value[: OUTPUT_LIMIT - 3] + "..."


def _sibling_command(module: str, binary: str) -> str | None:
    module_root = Path(__file__).resolve().parents[2]
    candidate = module_root.parent / module / "bin" / binary
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def resolve_command(binary: str, sibling_module: str) -> str | None:
    environment_name = binary.upper().replace("-", "_") + "_COMMAND"
    explicit = os.environ.get(environment_name)
    if explicit:
        return str(Path(explicit).expanduser())
    installed = shutil.which(binary)
    return installed or _sibling_command(sibling_module, binary)


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
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


def _baseline_facts(timeout: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    facts: dict[str, Any] = {"system.hostname": socket.gethostname()}
    errors: list[dict[str, Any]] = []
    omarchy = shutil.which("omarchy")
    if omarchy is None:
        errors.append(
            {
                "code": "provider_unavailable",
                "provider": "omarchy",
                "message": "the Omarchy command is unavailable",
            }
        )
        return facts, errors
    try:
        completed = _run([omarchy, "version"], timeout)
        if completed.returncode == 0 and completed.stdout.strip():
            facts["system.omarchy.version"] = completed.stdout.strip()
        else:
            errors.append(
                {
                    "code": "provider_failed",
                    "provider": "omarchy",
                    "source": f"{omarchy} version",
                    "message": _short(completed.stderr or completed.stdout)
                    or f"Omarchy exited with status {completed.returncode}",
                }
            )
    except (OSError, subprocess.TimeoutExpired) as error:
        errors.append(
            {
                "code": "provider_failed",
                "provider": "omarchy",
                "source": f"{omarchy} version",
                "message": str(error),
            }
        )
    return facts, errors


def collect_machine_state(timeout: float = 5.0) -> dict[str, Any]:
    requested = ["system.hostname", "system.omarchy.version"]
    stated = resolve_command("jiritsu-stated", "jiritsu-stated")
    fallback_errors: list[dict[str, Any]] = []
    stated_facts: dict[str, Any] = {}
    if stated is not None:
        command = [stated, "query", *requested, "--timeout", f"{timeout:g}"]
        try:
            completed = _run(command, timeout * len(requested) + 2)
            payload = json.loads(completed.stdout)
            if (
                isinstance(payload, dict)
                and payload.get("schema_version") == "1.0"
                and isinstance(payload.get("facts"), dict)
            ):
                for fact_id in requested:
                    fact = payload["facts"].get(fact_id)
                    if isinstance(fact, dict) and "value" in fact:
                        stated_facts[fact_id] = {
                            "value": fact["value"],
                            "source": fact.get("source"),
                            "observed_at": fact.get("observed_at"),
                        }
                for error in payload.get("errors", []):
                    fallback_errors.append(
                        {
                            "code": "provider_partial",
                            "provider": "jiritsu-stated",
                            "source": stated,
                            "message": str(error.get("message", error)),
                        }
                    )
            else:
                raise ValueError(
                    'response does not use the jiritsu-stated "1.0" schema'
                )
        except (
            OSError,
            subprocess.TimeoutExpired,
            json.JSONDecodeError,
            TypeError,
            ValueError,
        ) as error:
            fallback_errors.append(
                {
                    "code": "provider_failed",
                    "provider": "jiritsu-stated",
                    "source": stated,
                    "message": str(error),
                }
            )
    else:
        fallback_errors.append(
            {
                "code": "provider_unavailable",
                "provider": "jiritsu-stated",
                "message": "jiritsu-stated is unavailable",
            }
        )

    missing = set(requested) - set(stated_facts)
    baseline, baseline_errors = _baseline_facts(timeout) if missing else ({}, [])
    facts = dict(stated_facts)
    for fact_id in missing:
        if fact_id in baseline:
            facts[fact_id] = {
                "value": baseline[fact_id],
                "source": {
                    "id": "omarchy.version"
                    if fact_id.endswith("version")
                    else "linux.hostname",
                    "kind": "command" if fact_id.endswith("version") else "system",
                },
            }
    fallback_errors.extend(baseline_errors)
    if stated_facts and missing:
        provider = "hybrid"
    elif stated_facts:
        provider = "jiritsu-stated"
    else:
        provider = "baseline"
    return {
        "status": "ok" if len(facts) == len(requested) else "partial",
        "selected_provider": provider,
        "source": stated if stated_facts else "Omarchy and standard Linux",
        "requested_facts": requested,
        "facts": facts,
        "fallback_errors": fallback_errors,
    }


def assess_workloads(timeout: float = 5.0) -> dict[str, Any]:
    executable = resolve_command("jiritsu-workload", "jiritsu-workload")
    if executable is None:
        return {
            "status": "unavailable",
            "selected_provider": "none",
            "source": None,
            "critical_failures": [],
            "fallback_errors": [
                {
                    "code": "provider_unavailable",
                    "provider": "jiritsu-workload",
                    "message": "jiritsu-workload is unavailable; promotion remains standalone",
                }
            ],
        }
    try:
        completed = _run(
            [executable, "assess", "--timeout", f"{timeout:g}"], timeout * 20 + 5
        )
        payload = json.loads(completed.stdout)
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "1.1"
            or payload.get("status") not in {"healthy", "degraded", "unhealthy"}
            or not isinstance(payload.get("workloads"), list)
        ):
            raise ValueError('response does not use the jiritsu-workload "1.1" schema')
        failures = sorted(
            f"{workload.get('id')}/{capability.get('id')}"
            for workload in payload["workloads"]
            if isinstance(workload, dict)
            for capability in workload.get("capabilities", [])
            if isinstance(capability, dict)
            and capability.get("importance") == "critical"
            and capability.get("status") != "pass"
        )
        return {
            "status": payload["status"],
            "selected_provider": "jiritsu-workload",
            "source": executable,
            "assessed_at": payload.get("assessed_at"),
            "machine_state": payload.get("machine_state"),
            "summary": payload.get("summary"),
            "critical_failures": failures,
            "fallback_errors": [],
        }
    except (
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ) as error:
        return {
            "status": "unavailable",
            "selected_provider": "none",
            "source": executable,
            "critical_failures": [],
            "fallback_errors": [
                {
                    "code": "provider_failed",
                    "provider": "jiritsu-workload",
                    "source": executable,
                    "message": str(error),
                }
            ],
        }


def recovery_provider() -> dict[str, Any]:
    executable = resolve_command("jiritsu-checkpoints", "jiritsu-checkpoints")
    fallback_errors: list[dict[str, Any]] = []
    if executable is None:
        fallback_errors.append(
            {
                "code": "provider_unavailable",
                "provider": "jiritsu-checkpoints",
                "message": "jiritsu-checkpoints is unavailable; action-local recovery is selected",
            }
        )
    return {
        "required": False,
        "status": "planned",
        "selected_provider": "jiritsu-checkpoints"
        if executable
        else "action_local_backup",
        "source": executable or "jiritsu-proposals",
        "jiritsu_checkpoints": executable,
        "fallback_provider": "action_local_backup",
        "fallback_errors": fallback_errors,
    }


def _checkpoint_paths(actions: list[dict[str, Any]]) -> list[str]:
    selected: list[PurePosixPath] = []
    for action in actions:
        candidate = PurePosixPath(action["path"])
        if any(
            parent == existing for existing in selected for parent in candidate.parents
        ):
            continue
        selected = [
            existing for existing in selected if candidate not in existing.parents
        ]
        selected.append(candidate)
    return sorted(path.as_posix() for path in selected)


def prepare_recovery(
    plan: dict[str, Any],
    *,
    proposal_id: str,
    summary: str,
    actions: list[dict[str, Any]],
    config_root: Path,
    recovery_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    executable = plan.get("jiritsu_checkpoints")
    if not executable:
        return {
            **plan,
            "status": "ready",
            "selected_provider": "action_local_backup",
        }
    recovery_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    policy_path = recovery_dir / "checkpoint-policy.toml"
    paths = _checkpoint_paths(actions)
    policy_lines = ['schema_version = "1.0"', "include = ["]
    policy_lines.extend(f"  {json.dumps(path, ensure_ascii=False)}," for path in paths)
    policy_lines.append("]")
    try:
        policy_path.write_text("\n".join(policy_lines) + "\n", encoding="utf-8")
        os.chmod(policy_path, 0o600)
        proposal_hash = hashlib.sha256(proposal_id.encode("utf-8")).hexdigest()[:10]
        checkpoint_id = f"cp-{proposal_id[:48]}-{proposal_hash}"
        command = [
            executable,
            "create",
            "--reason",
            f"Before proposal {proposal_id}: {summary}"[:500],
            "--proposal",
            proposal_id,
            "--id",
            checkpoint_id,
            "--policy",
            str(policy_path),
            "--config-root",
            str(config_root.resolve()),
            "--system",
            "off",
            "--timeout",
            f"{timeout:g}",
        ]
        completed = _run(command, min(60.0, timeout * 10 + 5))
        payload = json.loads(completed.stdout)
        checkpoint = payload.get("checkpoint") if isinstance(payload, dict) else None
        if (
            completed.returncode == 0
            and isinstance(payload, dict)
            and payload.get("schema_version") == "1.0"
            and payload.get("status") == "ready"
            and isinstance(checkpoint, dict)
            and isinstance(checkpoint.get("id"), str)
        ):
            return {
                **plan,
                "status": "ready",
                "selected_provider": "jiritsu-checkpoints",
                "source": executable,
                "checkpoint_id": checkpoint["id"],
                "scope": checkpoint.get("scope"),
                "policy": {"path": str(policy_path), "include": paths},
            }
        detail = _short(completed.stderr or completed.stdout)
        raise ValueError(
            detail or f"checkpoint command exited with status {completed.returncode}"
        )
    except (
        OSError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        ValueError,
    ) as error:
        return {
            **plan,
            "status": "ready",
            "selected_provider": "action_local_backup",
            "source": "jiritsu-proposals",
            "policy": {"path": str(policy_path), "include": paths},
            "fallback_errors": [
                *plan.get("fallback_errors", []),
                {
                    "code": "provider_failed",
                    "provider": "jiritsu-checkpoints",
                    "source": executable,
                    "message": str(error),
                },
            ],
        }
