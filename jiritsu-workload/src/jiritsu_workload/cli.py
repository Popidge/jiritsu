from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .assessment import assess_contracts
from .contracts import (
    discover_contracts,
    install_contract,
    load_contract,
    select_contracts,
    user_config_dir,
)
from .model import ContractError, RESULT_SCHEMA_VERSION


EXIT_OK = 0
EXIT_UNHEALTHY = 1
EXIT_DEGRADED = 2
EXIT_USAGE = 64
EXIT_CONFIG = 65


class UsageError(Exception):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(prog="jiritsu-workload")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    parser.add_argument(
        "--config-dir",
        type=Path,
        help="use this user contract directory instead of the standard location",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common_options(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--pretty",
            action="store_true",
            default=argparse.SUPPRESS,
            help="indent JSON output",
        )
        command_parser.add_argument(
            "--config-dir",
            type=Path,
            default=argparse.SUPPRESS,
            help="use this user contract directory instead of the standard location",
        )

    list_command = subparsers.add_parser(
        "list", help="list available workload contracts"
    )
    add_common_options(list_command)

    query = subparsers.add_parser("query", help="return complete workload contracts")
    add_common_options(query)
    query.add_argument("selectors", nargs="*", help="workload IDs; omit for all")

    assess = subparsers.add_parser("assess", help="assess one or more workloads")
    add_common_options(assess)
    assess.add_argument("selectors", nargs="*", help="workload IDs; omit for all")
    assess.add_argument(
        "--timeout", type=float, default=5.0, help="default command timeout in seconds"
    )
    assess.add_argument(
        "--state-source",
        choices=("auto", "direct"),
        default="auto",
        help="use stated when available or force direct probes",
    )
    assess.add_argument(
        "--stated-command",
        type=Path,
        help="use this jiritsu-stated executable",
    )

    validate = subparsers.add_parser("validate", help="validate contract TOML files")
    add_common_options(validate)
    validate.add_argument("files", nargs="+", type=Path)

    apply_command = subparsers.add_parser(
        "apply", help="create or update one user contract from a TOML file"
    )
    add_common_options(apply_command)
    apply_command.add_argument("file", type=Path)

    config_path = subparsers.add_parser(
        "config-path", help="return the user contract directory"
    )
    add_common_options(config_path)
    return parser


def _write(payload: dict[str, Any], pretty: bool) -> None:
    json.dump(payload, sys.stdout, indent=2 if pretty else None, sort_keys=pretty)
    sys.stdout.write("\n")


def _error(error: ContractError | UsageError) -> dict[str, Any]:
    if isinstance(error, ContractError):
        detail = error.public()
    else:
        detail = {"code": "invalid_request", "message": str(error)}
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "error",
        "errors": [detail],
    }


def _config_directory(arguments: argparse.Namespace) -> Path:
    return (
        arguments.config_dir if arguments.config_dir is not None else user_config_dir()
    )


def _list(arguments: argparse.Namespace) -> dict[str, Any]:
    contracts = discover_contracts(arguments.config_dir)
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "ok",
        "config_dir": str(_config_directory(arguments)),
        "workloads": [
            {
                "id": contract.contract_id,
                "title": contract.title,
                "description": contract.description,
                "capability_count": len(contract.capabilities),
                "source": {"kind": contract.source_kind, "path": contract.source},
            }
            for contract in contracts
        ],
        "errors": [],
    }


def _query(arguments: argparse.Namespace) -> dict[str, Any]:
    contracts = select_contracts(
        discover_contracts(arguments.config_dir), arguments.selectors
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "ok",
        "workloads": [contract.public() for contract in contracts],
        "errors": [],
    }


def _validate(arguments: argparse.Namespace) -> dict[str, Any]:
    contracts = [load_contract(path) for path in arguments.files]
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "ok",
        "contracts": [
            {"id": contract.contract_id, "path": contract.source, "valid": True}
            for contract in contracts
        ],
        "errors": [],
    }


def _apply(arguments: argparse.Namespace) -> dict[str, Any]:
    action, contract, destination = install_contract(
        arguments.file, _config_directory(arguments)
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "ok",
        "action": action,
        "contract": {"id": contract.contract_id, "path": str(destination)},
        "errors": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        if arguments.command == "list":
            payload = _list(arguments)
            exit_status = EXIT_OK
        elif arguments.command == "query":
            payload = _query(arguments)
            exit_status = EXIT_OK
        elif arguments.command == "assess":
            if arguments.timeout <= 0:
                raise UsageError("--timeout must be more than zero")
            contracts = select_contracts(
                discover_contracts(arguments.config_dir), arguments.selectors
            )
            payload = assess_contracts(
                contracts,
                arguments.timeout,
                stated_command=(
                    str(arguments.stated_command)
                    if arguments.stated_command is not None
                    else None
                ),
                use_stated=arguments.state_source == "auto",
            )
            if payload["status"] == "unhealthy":
                exit_status = EXIT_UNHEALTHY
            elif payload["status"] == "degraded":
                exit_status = EXIT_DEGRADED
            else:
                exit_status = EXIT_OK
        elif arguments.command == "validate":
            payload = _validate(arguments)
            exit_status = EXIT_OK
        elif arguments.command == "apply":
            payload = _apply(arguments)
            exit_status = EXIT_OK
        else:
            payload = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "status": "ok",
                "config_dir": str(_config_directory(arguments)),
                "errors": [],
            }
            exit_status = EXIT_OK
        _write(payload, arguments.pretty)
        return exit_status
    except UsageError as error:
        _write(_error(error), "--pretty" in (argv or sys.argv[1:]))
        return EXIT_USAGE
    except ContractError as error:
        _write(_error(error), "--pretty" in (argv or sys.argv[1:]))
        if error.code == "unknown_workload":
            return EXIT_USAGE
        return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
