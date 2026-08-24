from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from jiritsu_checkpoints.cli import (  # noqa: E402
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    main,
)
from jiritsu_checkpoints.discovery import MachineState  # noqa: E402
from jiritsu_checkpoints.model import CheckpointError  # noqa: E402


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.state_dir = self.root / "state"
        self.config_root = self.root / "config"
        self.config_root.mkdir()
        self.policy = self.root / "policy.toml"
        self.policy.write_text(
            'schema_version = "1.0"\ninclude = ["app/settings.ini", "app/missing.ini"]\n',
            encoding="utf-8",
        )
        (self.config_root / "app").mkdir()
        (self.config_root / "app" / "settings.ini").write_text("before\n", encoding="utf-8")

    def invoke(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main((*arguments, "--state-dir", str(self.state_dir)))
        return status, json.loads(output.getvalue())

    def direct_state(self) -> MachineState:
        return MachineState((), None, "unavailable", "disabled", ())

    def create_user_checkpoint(self) -> tuple[int, dict]:
        with patch("jiritsu_checkpoints.cli.discover_machine_state", return_value=self.direct_state()):
            return self.invoke(
                "create",
                "--id",
                "cp-test",
                "--reason",
                "Before changing app settings",
                "--proposal",
                "proposal-1",
                "--system",
                "off",
                "--policy",
                str(self.policy),
                "--config-root",
                str(self.config_root),
            )

    def test_create_show_and_list_explain_the_checkpoint(self) -> None:
        status, created = self.create_user_checkpoint()

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("ready", created["status"])
        checkpoint = created["checkpoint"]
        self.assertEqual("Before changing app settings", checkpoint["reason"])
        self.assertEqual("proposal-1", checkpoint["proposal_id"])
        self.assertEqual("captured", checkpoint["scope"]["user_config"]["status"])
        self.assertEqual("missing", checkpoint["scope"]["user_config"]["entries"][1]["state"])

        status, listing = self.invoke("list")
        self.assertEqual(EXIT_OK, status)
        self.assertEqual("cp-test", listing["checkpoints"][0]["id"])
        self.assertEqual("Before changing app settings", listing["checkpoints"][0]["reason"])

        status, shown = self.invoke("show", "cp-test")
        self.assertEqual(EXIT_OK, status)
        self.assertEqual("python:file-copy", shown["checkpoint"]["scope"]["user_config"]["provider"])

    def test_user_config_restore_is_planned_then_applied(self) -> None:
        self.create_user_checkpoint()
        settings = self.config_root / "app" / "settings.ini"
        missing = self.config_root / "app" / "missing.ini"
        settings.write_text("after\n", encoding="utf-8")
        missing.write_text("new\n", encoding="utf-8")

        status, plan = self.invoke("restore", "cp-test", "--scope", "user-config")
        self.assertEqual(EXIT_OK, status)
        self.assertEqual("planned", plan["status"])
        self.assertEqual("after\n", settings.read_text(encoding="utf-8"))

        status, restored = self.invoke(
            "restore", "cp-test", "--scope", "user-config", "--apply"
        )
        self.assertEqual(EXIT_OK, status)
        self.assertEqual("restored", restored["status"])
        self.assertEqual("before\n", settings.read_text(encoding="utf-8"))
        self.assertFalse(missing.exists())
        self.assertTrue(Path(restored["restoration"]["pre_restore_backup"]).is_dir())

        _, shown = self.invoke("show", "cp-test")
        self.assertEqual("restored", shown["checkpoint"]["restore_history"][0]["status"])

    def test_dry_run_does_not_create_a_record(self) -> None:
        with patch("jiritsu_checkpoints.cli.discover_machine_state", return_value=self.direct_state()):
            status, result = self.invoke(
                "create",
                "--id",
                "cp-dry",
                "--reason",
                "Preview capture",
                "--system",
                "off",
                "--policy",
                str(self.policy),
                "--config-root",
                str(self.config_root),
                "--dry-run",
            )

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("planned", result["status"])
        self.assertEqual(
            ["app/settings.ini", "app/missing.ini"],
            result["checkpoint"]["recovery"]["recoverable"][0]["paths"],
        )
        self.assertFalse((self.state_dir / "cp-dry").exists())

    def test_inspect_returns_provider_status(self) -> None:
        with patch("jiritsu_checkpoints.cli.discover_machine_state", return_value=self.direct_state()):
            status, result = self.invoke("inspect")

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("ok", result["status"])
        self.assertEqual("available", result["provider_status"])

    def test_unsafe_policy_is_a_usage_error(self) -> None:
        self.policy.write_text('schema_version = "1.0"\ninclude = ["../secret"]\n', encoding="utf-8")

        status, result = self.invoke(
            "create", "--reason", "Unsafe", "--system", "off", "--policy", str(self.policy)
        )

        self.assertEqual(EXIT_USAGE, status)
        self.assertEqual("policy_invalid", result["errors"][0]["code"])

    def test_system_failure_keeps_a_successful_user_capture(self) -> None:
        state = MachineState(
            ({"name": "root", "subvolume": "/"},),
            {"filesystem": "btrfs", "snapper_snapshot_id": None},
            "jiritsu-stated",
            "used",
            (),
        )
        capabilities = {
            "provider": "snapper",
            "source": "linux:snapper",
            "selection": {"snapper_create": {"available": True}},
        }
        failure = CheckpointError(
            "snapshot_create_failed", "Snapper failed before capture"
        )
        with (
            patch("jiritsu_checkpoints.cli.discover_machine_state", return_value=state),
            patch("jiritsu_checkpoints.service.backend_capabilities", return_value=capabilities),
            patch("jiritsu_checkpoints.service.create_system_snapshots", side_effect=failure),
        ):
            status, result = self.invoke(
                "create",
                "--id",
                "cp-partial",
                "--reason",
                "Before a partial capture test",
                "--policy",
                str(self.policy),
                "--config-root",
                str(self.config_root),
            )

        self.assertEqual(EXIT_PARTIAL, status)
        self.assertEqual("partial", result["status"])
        self.assertEqual("snapshot_create_failed", result["errors"][0]["code"])
        self.assertEqual(
            "captured", result["checkpoint"]["scope"]["user_config"]["status"]
        )


if __name__ == "__main__":
    unittest.main()
