from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from jiritsu_checkpoints.backend import (  # noqa: E402
    apply_system_restore,
    create_system_snapshots,
    system_restore_plan,
)
from jiritsu_checkpoints.discovery import MachineState  # noqa: E402


class BackendTests(unittest.TestCase):
    def state(self, active: int | None = None) -> MachineState:
        return MachineState(
            ({"name": "root", "subvolume": "/"},),
            {
                "filesystem": "btrfs",
                "device": "/dev/root",
                "subvolume": f"/.snapshots/{active}/snapshot" if active else "/@",
                "snapper_snapshot_id": active,
            },
            "direct_probes",
            "disabled",
            (),
        )

    @patch("jiritsu_checkpoints.backend.shutil.which", return_value="/usr/bin/tool")
    def test_create_records_the_snapper_snapshot_id(self, _: object) -> None:
        commands: list[list[str]] = []

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            output = "42\n" if "create" in command else ""
            return subprocess.CompletedProcess(command, 0, output, "")

        snapshots, warnings = create_system_snapshots(
            "cp-test", "Before a test", "proposal-1", self.state(), runner=runner
        )

        self.assertEqual(42, snapshots[0]["snapshot_id"])
        self.assertEqual([], warnings)
        self.assertEqual("sudo", commands[0][0])
        self.assertIn("jiritsu_checkpoint=cp-test,proposal=proposal-1", commands[0])
        self.assertIn("cleanup", commands[1])

    @patch("jiritsu_checkpoints.backend.shutil.which")
    def test_omarchy_restore_requires_booting_the_target_snapshot(self, which: object) -> None:
        which.side_effect = lambda name: f"/usr/bin/{name}" if name == "omarchy" else None  # type: ignore[attr-defined]
        checkpoint = {
            "id": "cp-test",
            "scope": {
                "system": {
                    "snapshots": [
                        {"configuration": "root", "subvolume": "/", "snapshot_id": 42}
                    ]
                }
            },
        }

        plan = system_restore_plan(checkpoint, self.state(active=None))

        self.assertFalse(plan["ready_to_apply"])
        self.assertEqual("boot_snapshot_then_restore", plan["workflow"])
        self.assertIn("42", plan["instructions"][0])

    def test_apply_ready_omarchy_restore_uses_the_supported_command(self) -> None:
        plan = {
            "provider": "omarchy",
            "ready_to_apply": True,
            "target": {"configuration": "root", "snapshot_id": 42},
        }
        commands: list[list[str]] = []

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, "restored\n", "")

        result = apply_system_restore(plan, runner=runner)

        self.assertEqual("reboot_required", result["status"])
        self.assertEqual([["omarchy", "snapshot", "restore"]], commands)

    def test_cleanup_start_error_is_a_warning_after_snapshot_creation(self) -> None:
        calls = 0

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            nonlocal calls
            calls += 1
            if calls == 1:
                return subprocess.CompletedProcess(command, 0, "42\n", "")
            raise OSError("cleanup command failed to start")

        with patch("jiritsu_checkpoints.backend.shutil.which", return_value="/usr/bin/tool"):
            snapshots, warnings = create_system_snapshots(
                "cp-test", "Before a test", None, self.state(), runner=runner
            )

        self.assertEqual(42, snapshots[0]["snapshot_id"])
        self.assertEqual("snapshot_cleanup_failed", warnings[0]["code"])


if __name__ == "__main__":
    unittest.main()
