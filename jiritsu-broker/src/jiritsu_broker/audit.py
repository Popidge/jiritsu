from __future__ import annotations

import fcntl
import json
import os
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .model import BrokerError, SCHEMA_VERSION, timestamp


class AuditJournal:
    def __init__(self, state_directory: Path) -> None:
        self.state_directory = state_directory.expanduser()
        self.path = self.state_directory / "audit.jsonl"

    def _ensure(self) -> None:
        try:
            self.state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            metadata = self.state_directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise BrokerError(
                    "audit_unavailable",
                    f"broker state path is not a directory: {self.state_directory}",
                )
            if metadata.st_uid != os.geteuid():
                raise BrokerError(
                    "audit_unavailable",
                    f"broker state directory has an untrusted owner: {self.state_directory}",
                )
            self.state_directory.chmod(0o700)
        except BrokerError:
            raise
        except OSError as error:
            raise BrokerError(
                "audit_unavailable",
                f"cannot secure broker state directory {self.state_directory}: {error}",
            ) from error

    def append(self, request_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self._ensure()
        event = {
            "schema_version": SCHEMA_VERSION,
            "recorded_at": timestamp(),
            "request_id": request_id,
            "event": event_type,
            "payload": payload,
        }
        encoded = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            descriptor = os.open(
                self.path,
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "a", encoding="utf-8") as output:
                metadata = os.fstat(output.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                ):
                    raise BrokerError(
                        "audit_unavailable",
                        "audit journal is not a trusted regular file",
                    )
                os.fchmod(output.fileno(), 0o600)
                fcntl.flock(output.fileno(), fcntl.LOCK_EX)
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
                fcntl.flock(output.fileno(), fcntl.LOCK_UN)
        except BrokerError:
            raise
        except OSError as error:
            raise BrokerError(
                "audit_unavailable", f"cannot append broker audit journal: {error}"
            ) from error

    @contextmanager
    def request_lock(self, request_id: str) -> Iterator[None]:
        self._ensure()
        lock_directory = self.state_directory / "locks"
        try:
            lock_directory.mkdir(mode=0o700, exist_ok=True)
            directory_metadata = lock_directory.lstat()
            if stat.S_ISLNK(directory_metadata.st_mode) or not stat.S_ISDIR(
                directory_metadata.st_mode
            ):
                raise BrokerError(
                    "audit_unavailable", "request lock path is not a directory"
                )
            if directory_metadata.st_uid != os.geteuid():
                raise BrokerError(
                    "audit_unavailable", "request lock directory has an untrusted owner"
                )
            lock_directory.chmod(0o700)
            lock_path = lock_directory / f"{request_id}.lock"
            descriptor = os.open(
                lock_path,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "a+", encoding="utf-8") as lock_file:
                metadata = os.fstat(lock_file.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                ):
                    raise BrokerError(
                        "audit_unavailable",
                        "request lock is not a trusted regular file",
                    )
                os.fchmod(lock_file.fileno(), 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except BrokerError:
            raise
        except OSError as error:
            raise BrokerError(
                "audit_unavailable", f"cannot lock broker request {request_id}: {error}"
            ) from error

    def records(self, request_id: str | None = None) -> list[dict[str, Any]]:
        self._ensure()
        records: list[dict[str, Any]] = []
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "r", encoding="utf-8") as input_file:
                metadata = os.fstat(input_file.fileno())
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                ):
                    raise BrokerError(
                        "audit_unavailable",
                        "audit journal is not a trusted regular file",
                    )
                fcntl.flock(input_file.fileno(), fcntl.LOCK_SH)
                lines = input_file.read().splitlines()
                fcntl.flock(input_file.fileno(), fcntl.LOCK_UN)
            for line_number, line in enumerate(lines, start=1):
                try:
                    item = json.loads(line)
                except json.JSONDecodeError as error:
                    raise BrokerError(
                        "audit_corrupt",
                        f"audit record {line_number} is invalid JSON: {error}",
                    ) from error
                if not isinstance(item, dict):
                    raise BrokerError(
                        "audit_corrupt", f"audit record {line_number} is not an object"
                    )
                if request_id is None or item.get("request_id") == request_id:
                    records.append(item)
        except FileNotFoundError:
            return []
        except BrokerError:
            raise
        except (OSError, UnicodeError) as error:
            raise BrokerError(
                "audit_unavailable", f"cannot read broker audit journal: {error}"
            ) from error
        return records

    def is_complete(self, request_id: str) -> bool:
        for record in self.records(request_id):
            if record.get("event") != "result":
                continue
            payload = record.get("payload")
            if isinstance(payload, dict) and payload.get("status") in {
                "ok",
                "error",
                "denied",
            }:
                return True
        return False
