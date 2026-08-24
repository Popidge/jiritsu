from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


SCHEMA_VERSION = "1.0"
RESULT_SCHEMA_VERSION = "1.0"
TERMINAL_STATES = {"committed", "failed", "rejected", "rolled_back"}


def timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class ProposalError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        proposal_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.proposal_id = proposal_id
        self.details = details

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            result["field"] = self.field
        if self.proposal_id is not None:
            result["proposal_id"] = self.proposal_id
        if self.details:
            result["details"] = self.details
        return result


def event(
    sequence: int,
    event_type: str,
    actor: str,
    *,
    from_state: str | None = None,
    to_state: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "sequence": sequence,
        "at": timestamp(),
        "type": event_type,
        "actor": actor,
    }
    if from_state is not None:
        result["from_state"] = from_state
    if to_state is not None:
        result["to_state"] = to_state
    if details:
        result["details"] = details
    return result


def transition(
    proposal: dict[str, Any],
    to_state: str,
    event_type: str,
    actor: str,
    *,
    details: dict[str, Any] | None = None,
) -> None:
    old_state = proposal["state"]
    now = timestamp()
    proposal["state"] = to_state
    proposal["updated_at"] = now
    proposal["revision"] += 1
    proposal["history"].append(
        event(
            len(proposal["history"]) + 1,
            event_type,
            actor,
            from_state=old_state,
            to_state=to_state,
            details=details,
        )
    )
