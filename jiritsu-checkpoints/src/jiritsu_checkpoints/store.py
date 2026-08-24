from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .model import CHECKPOINT_SCHEMA_VERSION, CheckpointError, validate_id


def default_state_dir(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    override = env.get("JIRITSU_CHECKPOINTS_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg_root = env.get("XDG_STATE_HOME")
    root = Path(xdg_root).expanduser() if xdg_root else Path.home() / ".local" / "state"
    return root / "jiritsu" / "checkpoints"


class CheckpointStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()

    def _ensure_root(self) -> None:
        try:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.root.chmod(0o700)
        except OSError as error:
            raise CheckpointError(
                "store_error", f"cannot secure checkpoint store {self.root}: {error}"
            ) from error

    def checkpoint_dir(self, checkpoint_id: str) -> Path:
        return self.root / validate_id(checkpoint_id)

    def record_path(self, checkpoint_id: str) -> Path:
        return self.checkpoint_dir(checkpoint_id) / "checkpoint.json"

    @contextmanager
    def lock(self, checkpoint_id: str, *, create: bool = False) -> Iterator[Path]:
        self._ensure_root()
        directory = self.checkpoint_dir(checkpoint_id)
        if create:
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError as error:
                raise CheckpointError(
                    "checkpoint_exists",
                    f"checkpoint already exists: {checkpoint_id}",
                    checkpoint_id=checkpoint_id,
                ) from error
        elif not directory.is_dir():
            raise CheckpointError(
                "checkpoint_not_found",
                f"checkpoint does not exist: {checkpoint_id}",
                checkpoint_id=checkpoint_id,
            )
        lock_path = directory / ".lock"
        try:
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield directory
        except CheckpointError:
            raise
        except OSError as error:
            raise CheckpointError(
                "store_error",
                f"cannot lock checkpoint {checkpoint_id}: {error}",
                checkpoint_id=checkpoint_id,
            ) from error

    def load(self, checkpoint_id: str) -> dict[str, Any]:
        checkpoint_id = validate_id(checkpoint_id)
        path = self.record_path(checkpoint_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise CheckpointError(
                "checkpoint_not_found",
                f"checkpoint does not exist: {checkpoint_id}",
                checkpoint_id=checkpoint_id,
            ) from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise CheckpointError(
                "store_error",
                f"cannot read checkpoint {checkpoint_id}: {error}",
                checkpoint_id=checkpoint_id,
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
            or payload.get("id") != checkpoint_id
        ):
            raise CheckpointError(
                "store_error",
                f"checkpoint record is invalid: {checkpoint_id}",
                checkpoint_id=checkpoint_id,
            )
        return payload

    def save(self, checkpoint: dict[str, Any]) -> None:
        checkpoint_id = validate_id(str(checkpoint.get("id", "")))
        directory = self.checkpoint_dir(checkpoint_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        encoded = json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n"
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".checkpoint-", suffix=".tmp", dir=directory
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.record_path(checkpoint_id))
        except OSError as error:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            raise CheckpointError(
                "store_error",
                f"cannot save checkpoint {checkpoint_id}: {error}",
                checkpoint_id=checkpoint_id,
            ) from error

    def list(self) -> list[dict[str, Any]]:
        self._ensure_root()
        checkpoints = [self.load(path.parent.name) for path in self.root.glob("*/checkpoint.json")]
        checkpoints.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return checkpoints
