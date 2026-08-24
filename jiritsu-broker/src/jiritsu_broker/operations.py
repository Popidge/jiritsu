from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from .model import BrokerError, Request
from .providers import (
    ProviderResult,
    assess_workload,
    query_state,
    run_checkpoint,
    run_proposal,
)


PROPOSAL_ID = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
SELECTOR = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?$")
PROPOSAL_STATES = {
    "draft",
    "classified",
    "approved",
    "applying",
    "committed",
    "rejected",
    "rolled_back",
    "failed",
}


def _unknown(arguments: dict[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(arguments) - allowed)
    if extra:
        raise BrokerError(
            "invalid_arguments",
            f"unknown operation argument: {extra[0]}",
            field=f"arguments.{extra[0]}",
        )


def _timeout(arguments: dict[str, Any]) -> float:
    value = arguments.get("timeout_seconds", 5.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerError(
            "invalid_arguments",
            "timeout_seconds must be a number",
            field="arguments.timeout_seconds",
        )
    result = float(value)
    if result <= 0 or result > 30:
        raise BrokerError(
            "invalid_arguments",
            "timeout_seconds must be greater than zero and at most 30",
            field="arguments.timeout_seconds",
        )
    return result


def _selectors(arguments: dict[str, Any]) -> list[str]:
    value = arguments.get("selectors", [])
    if not isinstance(value, list) or len(value) > 32:
        raise BrokerError(
            "invalid_arguments",
            "selectors must be an array with at most 32 entries",
            field="arguments.selectors",
        )
    if any(not isinstance(item, str) or not SELECTOR.fullmatch(item) for item in value):
        raise BrokerError(
            "invalid_arguments",
            "each selector must be a supported lowercase identifier",
            field="arguments.selectors",
        )
    return list(dict.fromkeys(value))


def _proposal_id(arguments: dict[str, Any]) -> str:
    value = arguments.get("proposal_id")
    if not isinstance(value, str) or not PROPOSAL_ID.fullmatch(value):
        raise BrokerError(
            "invalid_arguments",
            "proposal_id must be a supported lowercase identifier",
            field="arguments.proposal_id",
        )
    return value


def _checkpoint_id(arguments: dict[str, Any]) -> str:
    value = arguments.get("checkpoint_id")
    if not isinstance(value, str) or not PROPOSAL_ID.fullmatch(value):
        raise BrokerError(
            "invalid_arguments",
            "checkpoint_id must be a supported lowercase identifier",
            field="arguments.checkpoint_id",
        )
    return value


def _validate_state(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"selectors", "timeout_seconds"})
    return {"selectors": _selectors(arguments), "timeout_seconds": _timeout(arguments)}


def _validate_workload(arguments: dict[str, Any]) -> dict[str, Any]:
    return _validate_state(arguments)


def _validate_create(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"intent", "actions", "proposal_id"})
    intent = arguments.get("intent")
    if not isinstance(intent, dict):
        raise BrokerError(
            "invalid_arguments", "intent must be an object", field="arguments.intent"
        )
    actions = arguments.get("actions")
    if not isinstance(actions, list) or not actions:
        raise BrokerError(
            "invalid_arguments",
            "actions must be a nonempty array",
            field="arguments.actions",
        )
    result: dict[str, Any] = {"intent": intent, "actions": actions}
    if "proposal_id" in arguments:
        result["proposal_id"] = _proposal_id(arguments)
    return result


def _validate_classify(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"proposal_id", "timeout_seconds"})
    return {
        "proposal_id": _proposal_id(arguments),
        "timeout_seconds": _timeout(arguments),
    }


def _validate_approve(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"proposal_id", "note"})
    result = {"proposal_id": _proposal_id(arguments)}
    note = arguments.get("note")
    if note is not None:
        if not isinstance(note, str) or not note.strip() or len(note) > 500:
            raise BrokerError(
                "invalid_arguments",
                "note must be a nonempty string with at most 500 characters",
                field="arguments.note",
            )
        result["note"] = note
    return result


def _validate_query(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"proposal_id"})
    return {"proposal_id": _proposal_id(arguments)}


def _validate_list(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"state"})
    state = arguments.get("state")
    if state is not None and state not in PROPOSAL_STATES:
        raise BrokerError(
            "invalid_arguments",
            f"state must be one of {sorted(PROPOSAL_STATES)}",
            field="arguments.state",
        )
    return {"state": state} if state is not None else {}


def _validate_promote(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"proposal_id", "timeout_seconds"})
    return {
        "proposal_id": _proposal_id(arguments),
        "timeout_seconds": _timeout(arguments),
    }


def _validate_checkpoint_inspect(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"timeout_seconds"})
    return {"timeout_seconds": _timeout(arguments)}


def _validate_checkpoint_list(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, set())
    return {}


def _validate_checkpoint_query(arguments: dict[str, Any]) -> dict[str, Any]:
    _unknown(arguments, {"checkpoint_id"})
    return {"checkpoint_id": _checkpoint_id(arguments)}


def _state(
    _request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    return query_state(arguments["selectors"], arguments["timeout_seconds"])


def _workload(
    _request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    return assess_workload(arguments["selectors"], arguments["timeout_seconds"])


def _create(
    request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    definition = {
        "schema_version": "1.0",
        "intent": arguments["intent"],
        "origin": {
            "kind": "agent",
            "actor": request.actor,
            "request_id": request.request_id,
        },
        "actions": arguments["actions"],
    }
    command = ["create", "-"]
    if "proposal_id" in arguments:
        command.extend(["--id", arguments["proposal_id"]])
    return run_proposal(command, 10.0, input_text=json.dumps(definition))


def _classify(
    request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    timeout = arguments["timeout_seconds"]
    return run_proposal(
        [
            "classify",
            arguments["proposal_id"],
            "--actor",
            f"broker:{request.actor}",
            "--timeout",
            f"{timeout:g}",
        ],
        max(timeout * 25 + 10, 20),
    )


def _approve(
    _request: Request, arguments: dict[str, Any], authorization: dict[str, Any]
) -> ProviderResult:
    approval = authorization.get("approval")
    approved_by = approval.get("approved_by") if isinstance(approval, dict) else None
    if not isinstance(approved_by, str) or not approved_by:
        principal = authorization.get("principal")
        if not isinstance(principal, str) or not principal:
            raise BrokerError(
                "approval_context_missing",
                "proposal approval requires an authorized approver identity",
            )
        approved_by = f"policy:{principal}"
    command = [
        "approve",
        arguments["proposal_id"],
        "--actor",
        approved_by,
    ]
    if "note" in arguments:
        command.extend(["--note", arguments["note"]])
    return run_proposal(command, 10.0)


def _query(
    _request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    return run_proposal(["show", arguments["proposal_id"]], 10.0)


def _list(
    _request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    command = ["list"]
    if "state" in arguments:
        command.extend(["--state", arguments["state"]])
    return run_proposal(command, 10.0)


def _promote(
    request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    timeout = arguments["timeout_seconds"]
    return run_proposal(
        [
            "promote",
            arguments["proposal_id"],
            "--actor",
            f"broker:{request.actor}",
            "--timeout",
            f"{timeout:g}",
        ],
        max(timeout * 25 + 10, 20),
    )


def _checkpoint_inspect(
    _request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    timeout = arguments["timeout_seconds"]
    return run_checkpoint(
        ["inspect", "--timeout", f"{timeout:g}"],
        max(timeout * 4 + 5, 10),
    )


def _checkpoint_list(
    _request: Request, _arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    return run_checkpoint(["list"], 10.0)


def _checkpoint_query(
    _request: Request, arguments: dict[str, Any], _authorization: dict[str, Any]
) -> ProviderResult:
    return run_checkpoint(["show", arguments["checkpoint_id"]], 10.0)


Validator = Callable[[dict[str, Any]], dict[str, Any]]
Handler = Callable[[Request, dict[str, Any], dict[str, Any]], ProviderResult]


@dataclass(frozen=True)
class Operation:
    operation_id: str
    description: str
    effect: str
    authorities: tuple[str, ...]
    provider: str
    arguments_schema: dict[str, Any]
    validator: Validator
    handler: Handler

    def public(self) -> dict[str, Any]:
        return {
            "id": self.operation_id,
            "description": self.description,
            "effect": self.effect,
            "required_authorities": list(self.authorities),
            "provider": self.provider,
            "arguments_schema": self.arguments_schema,
        }


OPERATIONS = (
    Operation(
        "state.query",
        "Read selected current-machine facts.",
        "read_only",
        ("machine_state.read",),
        "jiritsu-stated with Omarchy/Linux fallback",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selectors": {
                    "type": "array",
                    "maxItems": 32,
                    "items": {"type": "string", "pattern": SELECTOR.pattern},
                },
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30,
                    "default": 5,
                },
            },
        },
        _validate_state,
        _state,
    ),
    Operation(
        "workload.assess",
        "Assess selected local workload contracts.",
        "read_only",
        ("workload.assessment.read",),
        "jiritsu-workload",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "selectors": {
                    "type": "array",
                    "maxItems": 32,
                    "items": {"type": "string", "pattern": SELECTOR.pattern},
                },
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30,
                    "default": 5,
                },
            },
        },
        _validate_workload,
        _workload,
    ),
    Operation(
        "proposal.create",
        "Record agent intent as a draft typed proposal.",
        "durable_intent",
        ("proposal.intent.write",),
        "jiritsu-proposals",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["intent", "actions"],
            "properties": {
                "intent": {"type": "object"},
                "actions": {"type": "array", "minItems": 1},
                "proposal_id": {"type": "string", "pattern": PROPOSAL_ID.pattern},
            },
        },
        _validate_create,
        _create,
    ),
    Operation(
        "proposal.classify",
        "Classify a draft with current machine, workload, and recovery evidence.",
        "durable_classification",
        (
            "proposal.classification.write",
            "machine_state.read",
            "workload.assessment.read",
        ),
        "jiritsu-proposals",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["proposal_id"],
            "properties": {
                "proposal_id": {"type": "string", "pattern": PROPOSAL_ID.pattern},
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30,
                    "default": 5,
                },
            },
        },
        _validate_classify,
        _classify,
    ),
    Operation(
        "proposal.approve",
        "Record policy-authorized approval for the exact classified action set.",
        "durable_approval",
        ("proposal.approval.write",),
        "jiritsu-proposals",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["proposal_id"],
            "properties": {
                "proposal_id": {"type": "string", "pattern": PROPOSAL_ID.pattern},
                "note": {"type": "string", "minLength": 1, "maxLength": 500},
            },
        },
        _validate_approve,
        _approve,
    ),
    Operation(
        "proposal.query",
        "Read one complete proposal.",
        "read_only",
        ("proposal.read",),
        "jiritsu-proposals",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["proposal_id"],
            "properties": {
                "proposal_id": {"type": "string", "pattern": PROPOSAL_ID.pattern}
            },
        },
        _validate_query,
        _query,
    ),
    Operation(
        "proposal.list",
        "List proposal summaries, optionally by state.",
        "read_only",
        ("proposal.read",),
        "jiritsu-proposals",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "state": {"type": "string", "enum": sorted(PROPOSAL_STATES)}
            },
        },
        _validate_list,
        _list,
    ),
    Operation(
        "proposal.promote",
        "Apply and verify a proposal already approved in jiritsu-proposals.",
        "machine_change",
        ("user_config.write",),
        "jiritsu-proposals",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["proposal_id"],
            "properties": {
                "proposal_id": {"type": "string", "pattern": PROPOSAL_ID.pattern},
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30,
                    "default": 5,
                },
            },
        },
        _validate_promote,
        _promote,
    ),
    Operation(
        "checkpoint.inspect",
        "Inspect available checkpoint and restore providers.",
        "read_only",
        ("checkpoint.read",),
        "jiritsu-checkpoints",
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "timeout_seconds": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "maximum": 30,
                    "default": 5,
                }
            },
        },
        _validate_checkpoint_inspect,
        _checkpoint_inspect,
    ),
    Operation(
        "checkpoint.query",
        "Read one complete checkpoint record.",
        "read_only",
        ("checkpoint.read",),
        "jiritsu-checkpoints",
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["checkpoint_id"],
            "properties": {
                "checkpoint_id": {
                    "type": "string",
                    "pattern": PROPOSAL_ID.pattern,
                }
            },
        },
        _validate_checkpoint_query,
        _checkpoint_query,
    ),
    Operation(
        "checkpoint.list",
        "List checkpoint summaries.",
        "read_only",
        ("checkpoint.read",),
        "jiritsu-checkpoints",
        {"type": "object", "additionalProperties": False},
        _validate_checkpoint_list,
        _checkpoint_list,
    ),
)

OPERATION_MAP = {operation.operation_id: operation for operation in OPERATIONS}
