from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .model import ProposalError, RESULT_SCHEMA_VERSION
from .operations import (
    approve_proposal,
    classify_proposal,
    create_proposal,
    promote_proposal,
    proposal_summary,
    reject_proposal,
)
from .store import ProposalStore, default_config_root, default_state_dir


EXIT_OK = 0
EXIT_OPERATION = 1
EXIT_USAGE = 64
EXIT_DATA = 65


class UsageError(Exception):
    pass


class JsonParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _parser() -> JsonParser:
    parser = JsonParser(prog="jiritsu-proposals")
    parser.add_argument("--pretty", action="store_true", help="indent JSON output")
    parser.add_argument("--state-dir", type=Path, help="select the proposal store")
    parser.add_argument(
        "--config-root", type=Path, help="select the user config directory"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser, *, config: bool = False) -> None:
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
            help="select the proposal store",
        )
        if config:
            command.add_argument(
                "--config-root",
                type=Path,
                default=argparse.SUPPRESS,
                help="select the user config directory",
            )

    create = subparsers.add_parser(
        "create", help="create a draft from a JSON definition"
    )
    common(create)
    create.add_argument(
        "definition", help="JSON definition path, or - for standard input"
    )
    create.add_argument("--id", dest="proposal_id", help="use this proposal ID")

    classify = subparsers.add_parser("classify", help="classify a draft proposal")
    common(classify, config=True)
    classify.add_argument("proposal_id")
    classify.add_argument("--actor", required=True)
    classify.add_argument("--timeout", type=float, default=5.0)

    approve = subparsers.add_parser("approve", help="approve classified actions")
    common(approve)
    approve.add_argument("proposal_id")
    approve.add_argument("--actor", required=True)
    approve.add_argument("--note")

    reject = subparsers.add_parser(
        "reject", help="reject a draft or classified proposal"
    )
    common(reject)
    reject.add_argument("proposal_id")
    reject.add_argument("--actor", required=True)
    reject.add_argument("--reason", required=True)

    promote = subparsers.add_parser(
        "promote", help="apply and verify an approved proposal"
    )
    common(promote, config=True)
    promote.add_argument("proposal_id")
    promote.add_argument("--actor", required=True)
    promote.add_argument("--timeout", type=float, default=5.0)

    show = subparsers.add_parser("show", help="show one complete proposal")
    common(show)
    show.add_argument("proposal_id")

    history = subparsers.add_parser("history", help="show the history of one proposal")
    common(history)
    history.add_argument("proposal_id")

    listing = subparsers.add_parser("list", help="list proposal summaries")
    common(listing)
    listing.add_argument(
        "--state",
        choices=(
            "draft",
            "classified",
            "approved",
            "applying",
            "committed",
            "rejected",
            "rolled_back",
            "failed",
        ),
    )

    paths = subparsers.add_parser(
        "paths", help="show the active store and config paths"
    )
    common(paths, config=True)
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


def _result(**values: Any) -> dict[str, Any]:
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "ok",
        **values,
        "errors": [],
    }


def _error(error: Exception) -> dict[str, Any]:
    public = (
        error.public()
        if isinstance(error, ProposalError)
        else {
            "code": "invalid_request",
            "message": str(error),
        }
    )
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "status": "error",
        "errors": [public],
    }


def _read_definition(path: str) -> Any:
    try:
        text = (
            sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        )
        return json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProposalError(
            "definition_invalid", f"cannot read proposal definition: {error}"
        ) from error


def main(argv: Sequence[str] | None = None) -> int:
    arguments_list = list(argv) if argv is not None else sys.argv[1:]
    try:
        arguments = _parser().parse_args(arguments_list)
        if hasattr(arguments, "timeout") and arguments.timeout <= 0:
            raise UsageError("--timeout must be greater than zero")
        if hasattr(arguments, "actor") and not arguments.actor.strip():
            raise UsageError("--actor must not be empty")
        if hasattr(arguments, "reason") and not arguments.reason.strip():
            raise UsageError("--reason must not be empty")
        state_dir = arguments.state_dir or default_state_dir()
        config_root = arguments.config_root or default_config_root()
        store = ProposalStore(state_dir)

        if arguments.command == "create":
            proposal = create_proposal(
                store, _read_definition(arguments.definition), arguments.proposal_id
            )
            payload = _result(proposal=proposal)
        elif arguments.command == "classify":
            proposal = classify_proposal(
                store,
                arguments.proposal_id,
                arguments.actor,
                config_root,
                arguments.timeout,
            )
            payload = _result(proposal=proposal)
        elif arguments.command == "approve":
            proposal = approve_proposal(
                store, arguments.proposal_id, arguments.actor, arguments.note
            )
            payload = _result(proposal=proposal)
        elif arguments.command == "reject":
            proposal = reject_proposal(
                store, arguments.proposal_id, arguments.actor, arguments.reason
            )
            payload = _result(proposal=proposal)
        elif arguments.command == "promote":
            proposal = promote_proposal(
                store,
                arguments.proposal_id,
                arguments.actor,
                config_root,
                arguments.timeout,
            )
            outcome = "ok" if proposal["state"] == "committed" else "error"
            payload = {
                "schema_version": RESULT_SCHEMA_VERSION,
                "status": outcome,
                "proposal": proposal,
                "errors": [] if outcome == "ok" else [proposal["promotion"]["failure"]],
            }
            _write(payload, arguments.pretty)
            return EXIT_OK if outcome == "ok" else EXIT_OPERATION
        elif arguments.command == "show":
            with store.lock(arguments.proposal_id):
                proposal = store.load(arguments.proposal_id)
            payload = _result(proposal=proposal)
        elif arguments.command == "history":
            with store.lock(arguments.proposal_id):
                proposal = store.load(arguments.proposal_id)
            payload = _result(
                proposal_id=proposal["id"],
                state=proposal["state"],
                history=proposal["history"],
            )
        elif arguments.command == "list":
            proposals = store.list()
            if arguments.state:
                proposals = [
                    item for item in proposals if item["state"] == arguments.state
                ]
            payload = _result(proposals=[proposal_summary(item) for item in proposals])
        else:
            payload = _result(
                state_dir=str(state_dir.expanduser().resolve()),
                config_root=str(config_root.expanduser().resolve()),
            )
        _write(payload, arguments.pretty)
        return EXIT_OK
    except UsageError as error:
        _write(_error(error), "--pretty" in arguments_list)
        return EXIT_USAGE
    except ProposalError as error:
        _write(_error(error), "--pretty" in arguments_list)
        if error.code in {
            "definition_invalid",
            "proposal_invalid",
            "store_error",
        }:
            return EXIT_DATA
        return EXIT_OPERATION


if __name__ == "__main__":
    raise SystemExit(main())
