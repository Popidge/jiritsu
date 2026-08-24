from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from .audit import AuditJournal
from .broker import execute_request
from .model import BrokerError, ID_PATTERN, SCHEMA_VERSION, parse_request
from .operations import OPERATIONS
from .policy import Policy


EXIT_OK = 0
EXIT_OPERATION = 1
EXIT_POLICY = 3
EXIT_USAGE = 64
EXIT_DATA = 65


class UsageError(Exception):
    pass


class JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def default_state_directory(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    explicit = env.get("JIRITSU_BROKER_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    xdg_root = env.get("XDG_STATE_HOME")
    root = Path(xdg_root).expanduser() if xdg_root else Path.home() / ".local" / "state"
    return root / "jiritsu" / "broker"


def _parser() -> JsonParser:
    parser = JsonParser(prog="jiritsu-broker")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    parser.add_argument("--policy", type=Path, help="select a broker policy TOML file")
    parser.add_argument(
        "--state-dir", type=Path, help="select broker state and audit storage"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser, *, policy: bool = False) -> None:
        command.add_argument(
            "--pretty",
            action="store_true",
            default=argparse.SUPPRESS,
            help="indent JSON output",
        )
        command.add_argument(
            "--state-dir",
            type=Path,
            default=argparse.SUPPRESS,
            help="select broker state and audit storage",
        )
        if policy:
            command.add_argument(
                "--policy",
                type=Path,
                default=argparse.SUPPRESS,
                help="select a broker policy TOML file",
            )

    catalog = subparsers.add_parser("catalog", help="list the typed broker tools")
    common(catalog, policy=True)

    request = subparsers.add_parser(
        "request", help="evaluate and execute one JSON request"
    )
    common(request, policy=True)
    request.add_argument(
        "file", nargs="?", default="-", help="request JSON path, or - for stdin"
    )

    fingerprint = subparsers.add_parser(
        "fingerprint", help="calculate the digest needed by an external approval"
    )
    common(fingerprint, policy=True)
    fingerprint.add_argument(
        "file", nargs="?", default="-", help="request JSON path, or - for stdin"
    )

    audit = subparsers.add_parser("audit", help="read append-only broker audit records")
    common(audit)
    audit.add_argument(
        "request_id", nargs="?", help="return records for one request ID"
    )
    return parser


def _write(payload: dict[str, Any], pretty: bool) -> None:
    json.dump(
        payload,
        sys.stdout,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    )
    sys.stdout.write("\n")


def _error(error: Exception) -> dict[str, Any]:
    detail = (
        error.public()
        if isinstance(error, BrokerError)
        else {
            "code": "invalid_request",
            "message": str(error),
        }
    )
    return {"schema_version": SCHEMA_VERSION, "status": "error", "errors": [detail]}


def _read_json(path: str) -> Any:
    try:
        text = (
            sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        )
        return json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BrokerError(
            "request_invalid", f"cannot read request JSON: {error}"
        ) from error


def _catalog(policy: Policy, state_directory: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "request_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "schema_version",
                "request_id",
                "actor",
                "operation",
                "arguments",
            ],
            "properties": {
                "schema_version": {"const": SCHEMA_VERSION},
                "request_id": {"type": "string", "pattern": ID_PATTERN.pattern},
                "actor": {
                    "type": "string",
                    "pattern": ID_PATTERN.pattern,
                    "description": "Provenance only; this value does not grant authority.",
                },
                "operation": {
                    "type": "string",
                    "enum": [operation.operation_id for operation in OPERATIONS],
                },
                "arguments": {"type": "object"},
            },
        },
        "policy": {
            "provider": "toml",
            "source": str(policy.source),
            "principal_source": "effective operating-system user",
            "approval_directory": str(
                policy.approval_directory or state_directory / "approvals"
            ),
        },
        "tools": [operation.public() for operation in OPERATIONS],
        "errors": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments_list = list(argv) if argv is not None else sys.argv[1:]
    pretty = "--pretty" in arguments_list
    try:
        arguments = _parser().parse_args(arguments_list)
        state_directory = arguments.state_dir or default_state_directory()
        if arguments.command == "audit":
            records = AuditJournal(state_directory).records(arguments.request_id)
            _write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "ok",
                    "audit": {"source": str(state_directory / "audit.jsonl")},
                    "records": records,
                    "errors": [],
                },
                arguments.pretty,
            )
            return EXIT_OK

        policy = Policy.load(arguments.policy)
        if arguments.command == "catalog":
            _write(_catalog(policy, state_directory), arguments.pretty)
            return EXIT_OK

        request = parse_request(_read_json(arguments.file))
        if arguments.command == "fingerprint":
            approval_directory = (
                policy.approval_directory or state_directory / "approvals"
            )
            _write(
                {
                    "schema_version": SCHEMA_VERSION,
                    "status": "ok",
                    "request_id": request.request_id,
                    "request_sha256": request.digest(),
                    "approval_path": str(
                        approval_directory / f"{request.request_id}.json"
                    ),
                    "approval_template": {
                        "schema_version": SCHEMA_VERSION,
                        "request_id": request.request_id,
                        "request_sha256": request.digest(),
                        "approved_by": "<trusted approver>",
                        "expires_at": "<UTC timestamp>",
                    },
                    "errors": [],
                },
                arguments.pretty,
            )
            return EXIT_OK

        response = execute_request(
            request, policy, AuditJournal(state_directory), state_directory
        )
        _write(response, arguments.pretty)
        if response["status"] == "ok":
            return EXIT_OK
        if response["status"] in {"denied", "approval_required"}:
            return EXIT_POLICY
        return EXIT_OPERATION
    except UsageError as error:
        _write(_error(error), pretty)
        return EXIT_USAGE
    except BrokerError as error:
        _write(_error(error), pretty)
        if error.code.startswith("policy_") or error.code.startswith("audit_"):
            return EXIT_DATA
        if error.code in {"invalid_request", "request_invalid", "duplicate_request"}:
            return EXIT_USAGE
        return EXIT_OPERATION


if __name__ == "__main__":
    raise SystemExit(main())
