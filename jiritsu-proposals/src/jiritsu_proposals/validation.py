from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .model import ProposalError, SCHEMA_VERSION


ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
MODE_PATTERN = re.compile(r"^0[0-7]{3}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ACTION_TYPES = {"config.mkdir", "config.write"}
ORIGIN_KINDS = {"agent", "human"}


def _string(payload: dict[str, Any], field: str, *, location: str = "") -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        name = f"{location}.{field}" if location else field
        raise ProposalError(
            "proposal_invalid", f"{name} must be a nonempty string", field=name
        )
    return value


def _relative_config_path(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProposalError(
            "proposal_invalid", f"{field} must be a nonempty string", field=field
        )
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or value.startswith("~")
        or any(part in {"", ".", ".."} for part in pure.parts)
        or value != pure.as_posix()
    ):
        raise ProposalError(
            "proposal_invalid",
            f"{field} must be a normalized path relative to the user config directory",
            field=field,
        )
    return pure.as_posix()


def _mode(payload: dict[str, Any], default: str, field: str) -> str:
    value = payload.get("mode", default)
    if not isinstance(value, str) or not MODE_PATTERN.fullmatch(value):
        raise ProposalError(
            "proposal_invalid", f"{field} must be a four-digit octal mode", field=field
        )
    return value


def parse_definition(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ProposalError("proposal_invalid", "proposal root must be an object")
    allowed_root = {"schema_version", "intent", "origin", "actions"}
    unknown = sorted(set(payload) - allowed_root)
    if unknown:
        raise ProposalError(
            "proposal_invalid",
            f"unknown proposal field: {unknown[0]}",
            field=unknown[0],
        )
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ProposalError(
            "proposal_invalid",
            f'schema_version must be "{SCHEMA_VERSION}"',
            field="schema_version",
        )

    intent = payload.get("intent")
    if not isinstance(intent, dict):
        raise ProposalError(
            "proposal_invalid", "intent must be an object", field="intent"
        )
    unknown_intent = sorted(set(intent) - {"summary", "rationale"})
    if unknown_intent:
        raise ProposalError(
            "proposal_invalid",
            f"unknown intent field: {unknown_intent[0]}",
            field=f"intent.{unknown_intent[0]}",
        )
    normalized_intent = {
        "summary": _string(intent, "summary", location="intent"),
        "rationale": _string(intent, "rationale", location="intent"),
    }

    origin = payload.get("origin")
    if not isinstance(origin, dict):
        raise ProposalError(
            "proposal_invalid", "origin must be an object", field="origin"
        )
    unknown_origin = sorted(set(origin) - {"kind", "actor", "request_id"})
    if unknown_origin:
        raise ProposalError(
            "proposal_invalid",
            f"unknown origin field: {unknown_origin[0]}",
            field=f"origin.{unknown_origin[0]}",
        )
    kind = origin.get("kind")
    if kind not in ORIGIN_KINDS:
        raise ProposalError(
            "proposal_invalid",
            f"origin.kind must be one of {sorted(ORIGIN_KINDS)}",
            field="origin.kind",
        )
    normalized_origin = {
        "kind": kind,
        "actor": _string(origin, "actor", location="origin"),
    }
    if "request_id" in origin:
        normalized_origin["request_id"] = _string(
            origin, "request_id", location="origin"
        )

    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ProposalError(
            "proposal_invalid",
            "actions must contain at least one action",
            field="actions",
        )
    if len(actions) > 16:
        raise ProposalError(
            "proposal_invalid",
            "actions cannot contain more than 16 actions",
            field="actions",
        )
    normalized_actions: list[dict[str, Any]] = []
    paths: set[str] = set()
    for index, action in enumerate(actions):
        location = f"actions[{index}]"
        if not isinstance(action, dict):
            raise ProposalError(
                "proposal_invalid", f"{location} must be an object", field=location
            )
        action_type = action.get("type")
        if action_type not in ACTION_TYPES:
            raise ProposalError(
                "proposal_invalid",
                f"{location}.type must be one of {sorted(ACTION_TYPES)}",
                field=f"{location}.type",
            )
        path = _relative_config_path(action.get("path"), f"{location}.path")
        if path in paths:
            raise ProposalError(
                "proposal_invalid",
                f"duplicate action path: {path}",
                field=f"{location}.path",
            )
        paths.add(path)
        if action_type == "config.mkdir":
            allowed = {"type", "path", "mode"}
            normalized = {
                "type": action_type,
                "path": path,
                "mode": _mode(action, "0700", f"{location}.mode"),
            }
        else:
            allowed = {"type", "path", "content", "mode", "expected_sha256"}
            content = action.get("content")
            if not isinstance(content, str):
                raise ProposalError(
                    "proposal_invalid",
                    f"{location}.content must be a string",
                    field=f"{location}.content",
                )
            normalized = {
                "type": action_type,
                "path": path,
                "content": content,
                "mode": _mode(action, "0600", f"{location}.mode"),
            }
            if "expected_sha256" in action:
                digest = action["expected_sha256"]
                if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
                    raise ProposalError(
                        "proposal_invalid",
                        f"{location}.expected_sha256 must be a lowercase SHA-256 digest",
                        field=f"{location}.expected_sha256",
                    )
                normalized["expected_sha256"] = digest
        unknown_action = sorted(set(action) - allowed)
        if unknown_action:
            raise ProposalError(
                "proposal_invalid",
                f"unknown action field: {unknown_action[0]}",
                field=f"{location}.{unknown_action[0]}",
            )
        normalized_actions.append(normalized)
    return {
        "schema_version": SCHEMA_VERSION,
        "intent": normalized_intent,
        "origin": normalized_origin,
        "actions": normalized_actions,
    }


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def action_digest(actions: list[dict[str, Any]]) -> str:
    import json

    encoded = json.dumps(actions, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(encoded)


def target_path(config_root: Path, relative: str) -> Path:
    root = config_root.resolve()
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    parent = candidate.parent.resolve(strict=False)
    if parent != root and root not in parent.parents:
        raise ProposalError(
            "unsafe_target",
            "the action target resolves outside the user config directory",
            details={"path": relative, "config_root": str(root)},
        )
    return candidate
