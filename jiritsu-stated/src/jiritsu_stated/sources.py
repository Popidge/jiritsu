from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .model import CollectionError, Observation, SourceSpec, parse_timestamp, utc_now


class LiveSourceProvider:
    """Run read-only probes and cache each result for this query only."""

    def __init__(self, timeout_seconds: float = 5.0) -> None:
        self.timeout_seconds = timeout_seconds
        self._cache: dict[str, Observation] = {}

    def observe(self, source: SourceSpec) -> Observation:
        if source.source_id in self._cache:
            return self._cache[source.source_id]
        if source.kind == "command":
            observation = self._run_command(source)
        else:
            observation = self._read_file(source)
        self._cache[source.source_id] = observation
        return observation

    def _run_command(self, source: SourceSpec) -> Observation:
        assert isinstance(source.locator, tuple)
        environment = os.environ.copy()
        environment["LC_ALL"] = "C.UTF-8"
        try:
            result = subprocess.run(
                source.locator,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=environment,
                check=False,
            )
        except FileNotFoundError as error:
            raise CollectionError(
                "source_unavailable",
                f"Required command is not installed: {source.locator[0]}",
                source=source,
            ) from error
        except subprocess.TimeoutExpired as error:
            raise CollectionError(
                "source_timeout",
                f"Source did not respond within {self.timeout_seconds:g} seconds",
                source=source,
                retryable=True,
            ) from error
        except OSError as error:
            raise CollectionError(
                "source_unavailable",
                f"Could not run source: {error.strerror or error}",
                source=source,
            ) from error

        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if len(detail) > 500:
                detail = detail[:497] + "..."
            suffix = f": {detail}" if detail else ""
            raise CollectionError(
                "source_failed",
                f"Source exited with status {result.returncode}{suffix}",
                source=source,
                retryable=True,
            )
        return Observation(source, result.stdout, utc_now(), False)

    def _read_file(self, source: SourceSpec) -> Observation:
        assert isinstance(source.locator, str)
        try:
            text = Path(source.locator).read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise CollectionError(
                "source_unavailable",
                "Required system file does not exist",
                source=source,
            ) from error
        except PermissionError as error:
            raise CollectionError(
                "source_denied",
                "Permission was denied while reading the source",
                source=source,
            ) from error
        except (OSError, UnicodeError) as error:
            raise CollectionError(
                "source_failed",
                f"Could not read source: {error}",
                source=source,
                retryable=True,
            ) from error
        return Observation(source, text, utc_now(), False)


class FixtureSourceProvider:
    """Replay captured source payloads through the production parsers."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.sources = payload["sources"]
        self._cache: dict[str, Observation] = {}

    @classmethod
    def load(cls, path: str) -> "FixtureSourceProvider":
        try:
            with Path(path).open(encoding="utf-8") as fixture_file:
                payload = json.load(fixture_file)
        except FileNotFoundError as error:
            raise CollectionError(
                "fixture_not_found", f"Fixture does not exist: {path}"
            ) from error
        except PermissionError as error:
            raise CollectionError(
                "fixture_denied", f"Cannot read fixture: {path}"
            ) from error
        except json.JSONDecodeError as error:
            raise CollectionError(
                "fixture_invalid",
                f"Fixture is not valid JSON at line {error.lineno}, column {error.colno}",
            ) from error
        except OSError as error:
            raise CollectionError(
                "fixture_invalid", f"Cannot read fixture: {error}"
            ) from error

        cls._validate(payload)
        return cls(payload)

    @staticmethod
    def _validate(payload: Any) -> None:
        if not isinstance(payload, dict):
            raise CollectionError("fixture_invalid", "Fixture root must be an object")
        if payload.get("schema_version") != "1.0":
            raise CollectionError(
                "fixture_invalid", 'Fixture schema_version must be "1.0"'
            )
        sources = payload.get("sources")
        if not isinstance(sources, dict):
            raise CollectionError(
                "fixture_invalid", "Fixture sources must be an object"
            )
        for source_id, entry in sources.items():
            if not isinstance(source_id, str) or not isinstance(entry, dict):
                raise CollectionError(
                    "fixture_invalid", "Each fixture source must be an object"
                )
            if entry.get("kind") not in {"command", "file"}:
                raise CollectionError(
                    "fixture_invalid",
                    f"Fixture source {source_id!r} has an invalid kind",
                )
            observed_at = entry.get("observed_at")
            if not isinstance(observed_at, str):
                raise CollectionError(
                    "fixture_invalid", f"Fixture source {source_id!r} needs observed_at"
                )
            try:
                parse_timestamp(observed_at)
            except (TypeError, ValueError) as error:
                raise CollectionError(
                    "fixture_invalid",
                    f"Fixture source {source_id!r} has an invalid observed_at",
                ) from error
            if "error" not in entry:
                field = "stdout" if entry["kind"] == "command" else "content"
                if not isinstance(entry.get(field), str):
                    raise CollectionError(
                        "fixture_invalid",
                        f"Fixture source {source_id!r} needs string {field}",
                    )
            if "exit_code" in entry and not isinstance(entry["exit_code"], int):
                raise CollectionError(
                    "fixture_invalid",
                    f"Fixture source {source_id!r} has invalid exit_code",
                )

    def observe(self, source: SourceSpec) -> Observation:
        if source.source_id in self._cache:
            return self._cache[source.source_id]
        entry = self.sources.get(source.source_id)
        if entry is None:
            raise CollectionError(
                "fixture_source_missing",
                "Fixture has no payload for this source",
                source=source,
            )
        if entry["kind"] != source.kind:
            raise CollectionError(
                "fixture_source_mismatch",
                f"Fixture source kind is {entry['kind']!r}, expected {source.kind!r}",
                source=source,
            )
        if "error" in entry:
            raise CollectionError(
                "source_failed",
                str(entry["error"]),
                source=source,
                retryable=bool(entry.get("retryable", False)),
            )
        exit_code = entry.get("exit_code", 0)
        if source.kind == "command" and exit_code != 0:
            detail = str(entry.get("stderr", "")).strip()
            suffix = f": {detail}" if detail else ""
            raise CollectionError(
                "source_failed",
                f"Source exited with status {exit_code}{suffix}",
                source=source,
                retryable=bool(entry.get("retryable", False)),
            )
        field = "stdout" if source.kind == "command" else "content"
        observation = Observation(
            source=source,
            text=entry[field],
            observed_at=parse_timestamp(entry["observed_at"]),
            fixture=True,
        )
        self._cache[source.source_id] = observation
        return observation
