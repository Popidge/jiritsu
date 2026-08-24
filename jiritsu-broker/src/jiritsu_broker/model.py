from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "1.0"
ID_PATTERN = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9._:-]{0,126}[a-zA-Z0-9])?$")


def timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class BrokerError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.details = details

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            result["field"] = self.field
        if self.details:
            result["details"] = self.details
        return result


@dataclass(frozen=True)
class Request:
    request_id: str
    actor: str
    operation: str
    arguments: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": self.request_id,
            "actor": self.actor,
            "operation": self.operation,
            "arguments": self.arguments,
        }

    def digest(self) -> str:
        encoded = json.dumps(
            self.public(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def parse_request(payload: Any) -> Request:
    if not isinstance(payload, dict):
        raise BrokerError("invalid_request", "request root must be an object")
    allowed = {"schema_version", "request_id", "actor", "operation", "arguments"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise BrokerError(
            "invalid_request",
            f"unknown request field: {unknown[0]}",
            field=unknown[0],
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BrokerError(
            "invalid_request",
            f'schema_version must be "{SCHEMA_VERSION}"',
            field="schema_version",
        )
    values: dict[str, str] = {}
    for field in ("request_id", "actor", "operation"):
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise BrokerError(
                "invalid_request", f"{field} must be a nonempty string", field=field
            )
        if not ID_PATTERN.fullmatch(value):
            raise BrokerError(
                "invalid_request",
                f"{field} contains unsupported characters or is too long",
                field=field,
            )
        values[field] = value
    arguments = payload.get("arguments", {})
    if not isinstance(arguments, dict):
        raise BrokerError(
            "invalid_request", "arguments must be an object", field="arguments"
        )
    return Request(
        request_id=values["request_id"],
        actor=values["actor"],
        operation=values["operation"],
        arguments=arguments,
    )
