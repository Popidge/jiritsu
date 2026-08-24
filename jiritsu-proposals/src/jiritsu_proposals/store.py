from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .model import ProposalError


def default_state_dir(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    override = env.get("JIRITSU_PROPOSALS_STATE_DIR")
    if override:
        return Path(override).expanduser()
    xdg_root = env.get("XDG_STATE_HOME")
    root = Path(xdg_root).expanduser() if xdg_root else Path.home() / ".local" / "state"
    return root / "jiritsu" / "proposals"


def default_config_root(environment: dict[str, str] | None = None) -> Path:
    env = os.environ if environment is None else environment
    override = env.get("JIRITSU_PROPOSALS_CONFIG_ROOT")
    if override:
        return Path(override).expanduser()
    xdg_root = env.get("XDG_CONFIG_HOME")
    return Path(xdg_root).expanduser() if xdg_root else Path.home() / ".config"


class ProposalStore:
    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.root.chmod(0o700)
        except OSError as error:
            raise ProposalError(
                "store_error", f"cannot secure proposal store {self.root}: {error}"
            ) from error

    def proposal_dir(self, proposal_id: str) -> Path:
        return self.root / proposal_id

    def proposal_path(self, proposal_id: str) -> Path:
        return self.proposal_dir(proposal_id) / "proposal.json"

    def backup_dir(self, proposal_id: str) -> Path:
        return self.proposal_dir(proposal_id) / "recovery"

    @contextmanager
    def lock(self, proposal_id: str, *, create: bool = False) -> Iterator[None]:
        self._ensure_root()
        directory = self.proposal_dir(proposal_id)
        if create:
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError as error:
                raise ProposalError(
                    "proposal_exists",
                    f"proposal already exists: {proposal_id}",
                    proposal_id=proposal_id,
                ) from error
        elif not directory.is_dir():
            raise ProposalError(
                "proposal_not_found",
                f"proposal does not exist: {proposal_id}",
                proposal_id=proposal_id,
            )
        lock_path = directory / ".lock"
        try:
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                yield
        except ProposalError:
            raise
        except OSError as error:
            raise ProposalError(
                "store_error",
                f"cannot lock proposal {proposal_id}: {error}",
                proposal_id=proposal_id,
            ) from error

    def load(self, proposal_id: str) -> dict[str, Any]:
        path = self.proposal_path(proposal_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise ProposalError(
                "proposal_not_found",
                f"proposal does not exist: {proposal_id}",
                proposal_id=proposal_id,
            ) from error
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ProposalError(
                "store_error",
                f"cannot read proposal {proposal_id}: {error}",
                proposal_id=proposal_id,
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != "1.0"
            or payload.get("id") != proposal_id
            or not isinstance(payload.get("state"), str)
            or not isinstance(payload.get("actions"), list)
            or not isinstance(payload.get("history"), list)
        ):
            raise ProposalError(
                "store_error",
                f"proposal record is invalid: {proposal_id}",
                proposal_id=proposal_id,
            )
        return payload

    def save(self, proposal: dict[str, Any]) -> None:
        proposal_id = proposal["id"]
        directory = self.proposal_dir(proposal_id)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        encoded = json.dumps(proposal, ensure_ascii=False, indent=2) + "\n"
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".proposal-", suffix=".tmp", dir=directory
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.proposal_path(proposal_id))
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass
            raise ProposalError(
                "store_error",
                f"cannot save proposal {proposal_id}: {error}",
                proposal_id=proposal_id,
            ) from error

    def list(self) -> list[dict[str, Any]]:
        self._ensure_root()
        proposals: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*/proposal.json")):
            proposal_id = path.parent.name
            proposals.append(self.load(proposal_id))
        proposals.sort(key=lambda item: (item["created_at"], item["id"]), reverse=True)
        return proposals
