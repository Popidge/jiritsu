from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal


SCHEMA_VERSION = "1.0"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    kind: Literal["command", "file"]
    locator: tuple[str, ...] | str

    def public(self) -> dict[str, str]:
        if isinstance(self.locator, tuple):
            locator = " ".join(self.locator)
        else:
            locator = self.locator
        return {"id": self.source_id, "kind": self.kind, "locator": locator}


@dataclass(frozen=True)
class Observation:
    source: SourceSpec
    text: str
    observed_at: datetime
    fixture: bool


Parser = Callable[[str], Any]


@dataclass(frozen=True)
class FactDefinition:
    fact_id: str
    description: str
    source: SourceSpec
    parser: Parser


class CollectionError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        source: SourceSpec | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.source = source
        self.retryable = retryable

    def public(self, fact_id: str | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }
        if fact_id is not None:
            result["fact_id"] = fact_id
        if self.source is not None:
            result["source"] = self.source.public()
        return result
