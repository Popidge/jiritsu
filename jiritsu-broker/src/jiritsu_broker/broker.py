from __future__ import annotations

from pathlib import Path
from typing import Any

from .approvals import check_approval
from .audit import AuditJournal
from .model import BrokerError, Request, SCHEMA_VERSION, timestamp
from .operations import OPERATION_MAP
from .policy import Policy


def _response(
    request: Request,
    status: str,
    *,
    decision: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "request_id": request.request_id,
        "operation": request.operation,
        "finished_at": timestamp(),
        "decision": decision,
        "action": action,
        "result": result,
        "errors": errors or [],
    }


def _record_result(
    journal: AuditJournal, request: Request, response: dict[str, Any]
) -> dict[str, Any]:
    journal.append(request.request_id, "result", response)
    return response


def execute_request(
    request: Request,
    policy: Policy,
    journal: AuditJournal,
    state_directory: Path,
) -> dict[str, Any]:
    with journal.request_lock(request.request_id):
        return _execute_locked(request, policy, journal, state_directory)


def _execute_locked(
    request: Request,
    policy: Policy,
    journal: AuditJournal,
    state_directory: Path,
) -> dict[str, Any]:
    if journal.is_complete(request.request_id):
        raise BrokerError(
            "duplicate_request",
            f"request ID already has a terminal result: {request.request_id}",
            field="request_id",
        )
    journal.append(request.request_id, "request", request.public())
    operation = OPERATION_MAP.get(request.operation)
    if operation is None:
        decision = {
            "outcome": "deny",
            "reason": "operation is not in the broker tool catalog",
            "policy": {"provider": "operation_registry", "source": "built-in"},
        }
        journal.append(request.request_id, "decision", decision)
        return _record_result(
            journal,
            request,
            _response(
                request,
                "error",
                decision=decision,
                errors=[
                    {
                        "code": "unknown_operation",
                        "message": f"unknown operation: {request.operation}",
                    }
                ],
            ),
        )
    try:
        arguments = operation.validator(request.arguments)
    except BrokerError as error:
        decision = {
            "outcome": "deny",
            "reason": "operation arguments did not match the typed tool contract",
            "policy": {"provider": "operation_registry", "source": "built-in"},
        }
        journal.append(request.request_id, "decision", decision)
        return _record_result(
            journal,
            request,
            _response(request, "error", decision=decision, errors=[error.public()]),
        )
    evaluated = policy.evaluate(request.operation, operation.authorities)
    decision = evaluated.public(str(policy.source))
    if evaluated.outcome == "require_approval":
        approval_directory = policy.approval_directory or state_directory / "approvals"
        approval = check_approval(request, approval_directory)
        decision["approval"] = approval.public()
        if approval.approved:
            decision["outcome"] = "allow"
            decision["reason"] = "matching external approval permits this request"
    journal.append(request.request_id, "decision", decision)
    if decision["outcome"] == "deny":
        return _record_result(
            journal,
            request,
            _response(
                request,
                "denied",
                decision=decision,
                errors=[{"code": "policy_denied", "message": decision["reason"]}],
            ),
        )
    if decision["outcome"] == "require_approval":
        return _record_result(
            journal,
            request,
            _response(
                request,
                "approval_required",
                decision=decision,
                errors=[
                    {
                        "code": "approval_required",
                        "message": decision["approval"]["reason"],
                    }
                ],
            ),
        )
    action = {
        "operation": operation.operation_id,
        "effect": operation.effect,
        "authority": list(operation.authorities),
        "adapter": operation.provider,
        "shell": False,
    }
    journal.append(request.request_id, "action", action)
    try:
        provider_result = operation.handler(request, arguments, decision)
    except Exception as error:
        failure = {
            "code": "operation_failed",
            "message": f"operation adapter failed: {error}",
        }
        return _record_result(
            journal,
            request,
            _response(
                request,
                "error",
                decision=decision,
                action=action,
                errors=[failure],
            ),
        )
    public_result = provider_result.public()
    errors: list[dict[str, Any]] = []
    if provider_result.status == "error":
        errors.extend(provider_result.fallback_errors)
        if provider_result.data and isinstance(
            provider_result.data.get("errors"), list
        ):
            errors.extend(provider_result.data["errors"])
        if not errors:
            errors.append(
                {"code": "operation_failed", "message": "provider reported an error"}
            )
    return _record_result(
        journal,
        request,
        _response(
            request,
            "ok" if provider_result.status == "ok" else "error",
            decision=decision,
            action=action,
            result=public_result,
            errors=errors,
        ),
    )
