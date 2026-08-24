from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import Request, SCHEMA_VERSION


@dataclass(frozen=True)
class ApprovalResult:
    approved: bool
    source: str
    approved_by: str | None
    reason: str

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "approved": self.approved,
            "provider": "external_file",
            "source": self.source,
            "reason": self.reason,
        }
        if self.approved_by is not None:
            result["approved_by"] = self.approved_by
        return result


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def check_approval(request: Request, directory: Path) -> ApprovalResult:
    path = directory / f"{request.request_id}.json"
    source = str(path)
    try:
        directory_metadata = directory.lstat()
        if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(
            directory_metadata.st_mode
        ):
            return ApprovalResult(
                False, source, None, "approval directory is not a regular directory"
            )
        if directory_metadata.st_uid not in {0, os.geteuid()}:
            return ApprovalResult(
                False, source, None, "approval directory has an untrusted owner"
            )
        if directory_metadata.st_mode & 0o022:
            return ApprovalResult(
                False,
                source,
                None,
                "approval directory is writable by group or other users",
            )
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return ApprovalResult(False, source, None, "approval is not a regular file")
        if metadata.st_uid not in {0, os.geteuid()}:
            return ApprovalResult(
                False, source, None, "approval has an untrusted owner"
            )
        if metadata.st_mode & 0o022:
            return ApprovalResult(
                False, source, None, "approval is writable by group or other users"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return ApprovalResult(False, source, None, "no external approval exists")
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return ApprovalResult(False, source, None, f"approval cannot be read: {error}")
    if not isinstance(payload, dict):
        return ApprovalResult(False, source, None, "approval root is not an object")
    allowed = {
        "schema_version",
        "request_id",
        "request_sha256",
        "approved_by",
        "expires_at",
    }
    if set(payload) - allowed:
        return ApprovalResult(False, source, None, "approval contains unknown fields")
    if payload.get("schema_version") != SCHEMA_VERSION:
        return ApprovalResult(False, source, None, "approval schema version is invalid")
    if payload.get("request_id") != request.request_id:
        return ApprovalResult(False, source, None, "approval request ID does not match")
    if payload.get("request_sha256") != request.digest():
        return ApprovalResult(
            False, source, None, "approval request digest does not match"
        )
    approved_by = payload.get("approved_by")
    if not isinstance(approved_by, str) or not approved_by:
        return ApprovalResult(False, source, None, "approved_by is missing")
    expires_at = _parse_time(payload.get("expires_at"))
    if expires_at is None:
        return ApprovalResult(False, source, None, "approval expiry is invalid")
    if expires_at <= datetime.now(timezone.utc):
        return ApprovalResult(False, source, approved_by, "approval has expired")
    return ApprovalResult(
        True, source, approved_by, "external approval matches this request"
    )
