from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .config_capture import default_config_root, load_policy
from .discovery import discover_machine_state
from .model import (
    RESULT_SCHEMA_VERSION,
    CheckpointError,
    new_checkpoint_id,
    validate_id,
)
from .service import (
    create_checkpoint,
    inspect_backend,
    restore_system,
    restore_user_config,
)
from .store import CheckpointStore, default_state_dir


EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PARTIAL = 2
EXIT_USAGE = 64
EXIT_DATA = 65


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CheckpointError("invalid_request", message)


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-dir", metavar="PATH", help="checkpoint state directory")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")


def _discovery_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stated-command", metavar="PATH", help="jiritsu-stated executable")
    parser.add_argument(
        "--state-source",
        choices=("auto", "direct"),
        default="auto",
        help="prefer jiritsu-stated or use direct read-only probes",
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0, metavar="SECONDS", help="probe timeout"
    )


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="jiritsu-checkpoints")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect = subparsers.add_parser("inspect", help="report available recovery providers")
    _common(inspect)
    _discovery_options(inspect)

    create = subparsers.add_parser("create", help="create a checkpoint")
    create.add_argument("--reason", required=True, help="why the checkpoint exists")
    create.add_argument("--proposal", metavar="ID", help="related proposal ID")
    create.add_argument("--id", metavar="ID", help="explicit checkpoint ID")
    create.add_argument("--policy", metavar="PATH", help="user config capture policy")
    create.add_argument("--config-root", metavar="PATH", help="user config root")
    create.add_argument(
        "--system",
        choices=("auto", "required", "off"),
        default="auto",
        help="system snapshot behavior (default: auto)",
    )
    create.add_argument("--dry-run", action="store_true", help="show the plan without writing or snapshotting")
    _common(create)
    _discovery_options(create)

    listing = subparsers.add_parser("list", help="list known checkpoints")
    _common(listing)

    show = subparsers.add_parser("show", help="show one complete checkpoint")
    show.add_argument("checkpoint_id")
    _common(show)

    restore = subparsers.add_parser("restore", help="plan or apply a restore")
    restore.add_argument("checkpoint_id")
    restore.add_argument("--scope", choices=("system", "user-config"), required=True)
    restore.add_argument("--apply", action="store_true", help="perform the restore")
    restore.add_argument("--config-root", metavar="PATH", help="override the recorded config root")
    _common(restore)
    _discovery_options(restore)
    return parser


def emit(payload: dict[str, Any], pretty: bool = False) -> None:
    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    sys.stdout.write("\n")


def _result(status: str, action: str, **values: Any) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": status,
        "action": action,
        **values,
        "errors": [],
    }


def _error_result(error: CheckpointError) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "error",
        "errors": [error.public()],
    }


def _store(arguments: argparse.Namespace) -> CheckpointStore:
    root = Path(arguments.state_dir) if arguments.state_dir else default_state_dir()
    return CheckpointStore(root)


def _discover(arguments: argparse.Namespace):
    if arguments.timeout <= 0:
        raise CheckpointError("invalid_request", "--timeout must be greater than zero", field="timeout")
    return discover_machine_state(
        stated_command=arguments.stated_command,
        state_source=arguments.state_source,
        timeout_seconds=arguments.timeout,
    )


def _summary(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": checkpoint["id"],
        "status": checkpoint["status"],
        "reason": checkpoint["reason"],
        "created_at": checkpoint["created_at"],
        **({"proposal_id": checkpoint["proposal_id"]} if "proposal_id" in checkpoint else {}),
        "backend": {
            "provider": checkpoint["backend"]["provider"],
            "source": checkpoint["backend"]["source"],
        },
        "scope": {
            "system": checkpoint["scope"]["system"]["status"],
            "user_config": checkpoint["scope"]["user_config"]["status"],
        },
    }


def run(arguments: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    if arguments.command == "inspect":
        result = inspect_backend(_discover(arguments))
        return EXIT_OK, _result("ok", "inspect", **result)

    store = _store(arguments)
    if arguments.command == "list":
        checkpoints = [_summary(item) for item in store.list()]
        return EXIT_OK, _result(
            "ok", "list", checkpoint_count=len(checkpoints), checkpoints=checkpoints
        )
    if arguments.command == "show":
        checkpoint = store.load(validate_id(arguments.checkpoint_id))
        return EXIT_OK, _result("ok", "show", checkpoint=checkpoint)
    if arguments.command == "create":
        reason = arguments.reason.strip()
        if not reason:
            raise CheckpointError("invalid_request", "--reason must not be empty", field="reason")
        if len(reason) > 500:
            raise CheckpointError("invalid_request", "--reason cannot exceed 500 characters", field="reason")
        checkpoint_id = validate_id(arguments.id) if arguments.id else new_checkpoint_id()
        proposal_id = validate_id(arguments.proposal, "proposal") if arguments.proposal else None
        policy = load_policy(Path(arguments.policy).expanduser()) if arguments.policy else None
        config_root = Path(arguments.config_root).expanduser() if arguments.config_root else default_config_root()
        status, checkpoint = create_checkpoint(
            store,
            checkpoint_id=checkpoint_id,
            reason=reason,
            proposal_id=proposal_id,
            system_mode=arguments.system,
            policy=policy,
            config_root=config_root,
            state=_discover(arguments),
            dry_run=arguments.dry_run,
        )
        exit_status = EXIT_OK if status in {"planned", "ready"} else (EXIT_PARTIAL if status == "partial" else EXIT_ERROR)
        payload = _result(status, "create", checkpoint=checkpoint)
        payload["errors"] = checkpoint.get("errors", [])
        return exit_status, payload

    checkpoint = store.load(validate_id(arguments.checkpoint_id))
    if arguments.scope == "user-config":
        config_root = Path(arguments.config_root).expanduser() if arguments.config_root else None
        status, restoration = restore_user_config(
            store, checkpoint, apply=arguments.apply, config_root=config_root
        )
    else:
        status, restoration = restore_system(
            store, checkpoint, _discover(arguments), apply=arguments.apply
        )
    exit_status = EXIT_PARTIAL if status == "action_required" else EXIT_OK
    return exit_status, _result(status, "restore", checkpoint_id=checkpoint["id"], restoration=restoration)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    pretty = False
    try:
        arguments = parser.parse_args(argv)
        pretty = bool(arguments.pretty)
        status, payload = run(arguments)
    except CheckpointError as error:
        payload = _error_result(error)
        if error.code in {"invalid_request", "policy_invalid"}:
            status = EXIT_USAGE
        elif error.code in {"store_error", "policy_not_found"}:
            status = EXIT_DATA
        else:
            status = EXIT_ERROR
    emit(payload, pretty)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
