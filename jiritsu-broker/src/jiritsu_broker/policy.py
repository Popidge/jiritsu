from __future__ import annotations

import os
import pwd
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .model import BrokerError, SCHEMA_VERSION


DECISIONS = {"allow", "deny", "require_approval"}


def default_policy_path() -> Path:
    return Path(__file__).parent / "defaults" / "policy.toml"


def configured_policy_path(environment: dict[str, str] | None = None) -> Path | None:
    env = os.environ if environment is None else environment
    explicit = env.get("JIRITSU_BROKER_POLICY")
    if explicit:
        return Path(explicit).expanduser()
    xdg_root = env.get("XDG_CONFIG_HOME")
    root = Path(xdg_root).expanduser() if xdg_root else Path.home() / ".config"
    candidate = root / "jiritsu" / "broker-policy.toml"
    return candidate if candidate.is_file() else None


def current_principals() -> tuple[str, ...]:
    effective_uid = os.geteuid()
    try:
        user_name = pwd.getpwuid(effective_uid).pw_name
    except KeyError:
        return (f"uid:{effective_uid}",)
    return (f"uid:{effective_uid}", f"user:{user_name}")


def _string_list(
    value: Any, field: str, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not value and not allow_empty)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise BrokerError(
            "policy_invalid", f"{field} must be a nonempty string array", field=field
        )
    return tuple(value)


@dataclass(frozen=True)
class Rule:
    rule_id: str
    principals: tuple[str, ...]
    operations: tuple[str, ...]
    decision: str
    authorities: tuple[str, ...]

    def matches(self, principals: tuple[str, ...], operation: str) -> bool:
        principal_match = "*" in self.principals or bool(
            set(principals) & set(self.principals)
        )
        operation_match = "*" in self.operations or operation in self.operations
        return principal_match and operation_match


@dataclass(frozen=True)
class PolicyDecision:
    outcome: str
    rule_id: str | None
    principal: str
    required_authorities: tuple[str, ...]
    granted_authorities: tuple[str, ...]
    reason: str

    def public(self, source: str) -> dict[str, Any]:
        result: dict[str, Any] = {
            "outcome": self.outcome,
            "principal": self.principal,
            "required_authorities": list(self.required_authorities),
            "granted_authorities": list(self.granted_authorities),
            "reason": self.reason,
            "policy": {"provider": "toml", "source": source},
        }
        if self.rule_id is not None:
            result["rule_id"] = self.rule_id
        return result


@dataclass(frozen=True)
class Policy:
    source: Path
    default: str
    rules: tuple[Rule, ...]
    approval_directory: Path | None

    @classmethod
    def load(cls, path: Path | None = None) -> "Policy":
        source = path or configured_policy_path() or default_policy_path()
        try:
            metadata = source.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise BrokerError(
                    "policy_invalid", f"policy is not a regular file: {source}"
                )
            if metadata.st_uid not in {0, os.geteuid()}:
                raise BrokerError(
                    "policy_invalid", f"policy has an untrusted owner: {source}"
                )
            if metadata.st_mode & 0o022:
                raise BrokerError(
                    "policy_invalid",
                    f"policy is writable by group or other users: {source}",
                )
            with source.open("rb") as input_file:
                payload = tomllib.load(input_file)
        except BrokerError:
            raise
        except FileNotFoundError as error:
            raise BrokerError(
                "policy_unavailable", f"policy file does not exist: {source}"
            ) from error
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise BrokerError(
                "policy_invalid", f"cannot read policy {source}: {error}"
            ) from error
        if not isinstance(payload, dict):
            raise BrokerError("policy_invalid", "policy root must be a table")
        allowed_root = {"schema_version", "default", "approval_directory", "rules"}
        unknown = sorted(set(payload) - allowed_root)
        if unknown:
            raise BrokerError(
                "policy_invalid",
                f"unknown policy field: {unknown[0]}",
                field=unknown[0],
            )
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise BrokerError(
                "policy_invalid",
                f'policy schema_version must be "{SCHEMA_VERSION}"',
                field="schema_version",
            )
        default = payload.get("default")
        if default != "deny":
            raise BrokerError(
                "policy_invalid",
                'policy default must be "deny"; each granted authority needs a rule',
                field="default",
            )
        approval_value = payload.get("approval_directory")
        if approval_value is not None and (
            not isinstance(approval_value, str) or not approval_value
        ):
            raise BrokerError(
                "policy_invalid",
                "approval_directory must be a nonempty path string",
                field="approval_directory",
            )
        raw_rules = payload.get("rules", [])
        if not isinstance(raw_rules, list):
            raise BrokerError("policy_invalid", "rules must be an array", field="rules")
        rules: list[Rule] = []
        seen: set[str] = set()
        for index, raw in enumerate(raw_rules):
            location = f"rules[{index}]"
            if not isinstance(raw, dict):
                raise BrokerError(
                    "policy_invalid", f"{location} must be a table", field=location
                )
            allowed = {"id", "principals", "operations", "decision", "authorities"}
            extra = sorted(set(raw) - allowed)
            if extra:
                raise BrokerError(
                    "policy_invalid",
                    f"unknown rule field: {extra[0]}",
                    field=f"{location}.{extra[0]}",
                )
            rule_id = raw.get("id")
            if not isinstance(rule_id, str) or not rule_id:
                raise BrokerError(
                    "policy_invalid", f"{location}.id must be a nonempty string"
                )
            if rule_id in seen:
                raise BrokerError("policy_invalid", f"duplicate policy rule: {rule_id}")
            seen.add(rule_id)
            decision = raw.get("decision")
            if decision not in DECISIONS:
                raise BrokerError(
                    "policy_invalid",
                    f"{location}.decision must be one of {sorted(DECISIONS)}",
                )
            rules.append(
                Rule(
                    rule_id=rule_id,
                    principals=_string_list(
                        raw.get("principals"), f"{location}.principals"
                    ),
                    operations=_string_list(
                        raw.get("operations"), f"{location}.operations"
                    ),
                    decision=decision,
                    authorities=_string_list(
                        raw.get("authorities", []),
                        f"{location}.authorities",
                        allow_empty=decision == "deny",
                    ),
                )
            )
        approval_directory = None
        if approval_value is not None:
            approval_directory = Path(approval_value).expanduser()
            if not approval_directory.is_absolute():
                approval_directory = source.parent / approval_directory
        return cls(
            source=source,
            default=default,
            rules=tuple(rules),
            approval_directory=approval_directory,
        )

    def evaluate(
        self, operation: str, required_authorities: tuple[str, ...]
    ) -> PolicyDecision:
        principals = current_principals()
        principal = principals[0]
        rule = next(
            (item for item in self.rules if item.matches(principals, operation)), None
        )
        if rule is None:
            return PolicyDecision(
                outcome=self.default,
                rule_id=None,
                principal=principal,
                required_authorities=required_authorities,
                granted_authorities=(),
                reason=f"no rule matched; policy default is {self.default}",
            )
        missing = sorted(set(required_authorities) - set(rule.authorities))
        if missing:
            return PolicyDecision(
                outcome="deny",
                rule_id=rule.rule_id,
                principal=principal,
                required_authorities=required_authorities,
                granted_authorities=(),
                reason=f"matching rule does not grant required authority: {', '.join(missing)}",
            )
        return PolicyDecision(
            outcome=rule.decision,
            rule_id=rule.rule_id,
            principal=principal,
            required_authorities=required_authorities,
            granted_authorities=required_authorities,
            reason=f"first matching rule selected {rule.decision}",
        )
