from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


SCHEMA_VERSION = "1.0"
RESULT_SCHEMA_VERSION = "1.1"


class ContractError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.field = field

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path is not None:
            result["path"] = self.path
        if self.field is not None:
            result["field"] = self.field
        return result


@dataclass(frozen=True)
class Check:
    check_id: str
    check_type: Literal[
        "command",
        "command_available",
        "environment",
        "path",
        "stated_fact",
        "systemd_unit",
    ]
    description: str
    parameters: dict[str, Any]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.check_id,
            "type": self.check_type,
            "description": self.description,
            **self.parameters,
        }


@dataclass(frozen=True)
class Capability:
    capability_id: str
    title: str
    description: str
    importance: Literal["critical", "useful"]
    checks: tuple[Check, ...]

    def public(self) -> dict[str, Any]:
        return {
            "id": self.capability_id,
            "title": self.title,
            "description": self.description,
            "importance": self.importance,
            "checks": [check.public() for check in self.checks],
        }


@dataclass(frozen=True)
class WorkloadContract:
    contract_id: str
    title: str
    description: str
    capabilities: tuple[Capability, ...]
    source: str
    source_kind: Literal["default", "user", "explicit"]

    def public(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "id": self.contract_id,
            "title": self.title,
            "description": self.description,
            "capabilities": [capability.public() for capability in self.capabilities],
            "source": {"kind": self.source_kind, "path": self.source},
        }
