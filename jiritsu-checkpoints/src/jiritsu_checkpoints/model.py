from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Any


CHECKPOINT_SCHEMA_VERSION = "1.0"
RESULT_SCHEMA_VERSION = "1.0"
ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def new_checkpoint_id() -> str:
    date = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"cp-{date}-{secrets.token_hex(3)}"


def validate_id(value: str, field: str = "checkpoint_id") -> str:
    if not ID_PATTERN.fullmatch(value):
        raise CheckpointError(
            "invalid_request",
            f"{field} must contain 1 to 64 lowercase letters, digits, dots, dashes, or underscores",
            field=field,
        )
    return value


class CheckpointError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        field: str | None = None,
        checkpoint_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.field = field
        self.checkpoint_id = checkpoint_id
        self.details = details

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field is not None:
            result["field"] = self.field
        if self.checkpoint_id is not None:
            result["checkpoint_id"] = self.checkpoint_id
        if self.details:
            result["details"] = self.details
        return result
