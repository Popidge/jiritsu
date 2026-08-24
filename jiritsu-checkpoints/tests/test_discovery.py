from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from jiritsu_checkpoints.discovery import discover_machine_state  # noqa: E402


class DiscoveryTests(unittest.TestCase):
    def test_uses_stated_for_snapshot_state(self) -> None:
        payload = {
            "schema_version": "1.0",
            "status": "ok",
            "facts": {
                "snapshots.configurations": {
                    "value": [{"name": "root", "subvolume": "/"}]
                },
                "snapshots.active_root": {
                    "value": {
                        "filesystem": "btrfs",
                        "device": "/dev/root",
                        "subvolume": "/@",
                        "snapper_snapshot_id": None,
                    }
                },
            },
            "errors": [],
        }
        commands: list[list[str]] = []

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            commands.append(command)
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

        state = discover_machine_state(stated_command="/fake/stated", runner=runner)

        self.assertEqual("jiritsu-stated", state.source)
        self.assertEqual("used", state.stated_status)
        self.assertEqual(({"name": "root", "subvolume": "/"},), state.configurations)
        self.assertEqual(1, len(commands))

    def test_invalid_stated_falls_back_to_direct_probes(self) -> None:
        snapper = {"configs": [{"config": "root", "subvolume": "/"}]}
        findmnt = {
            "filesystems": [
                {
                    "source": "/dev/root[/@]",
                    "fstype": "btrfs",
                    "options": "rw,subvol=/@",
                }
            ]
        }

        def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
            if command[0] == "/fake/stated":
                return subprocess.CompletedProcess(command, 0, "not-json", "")
            if command[0] == "snapper":
                return subprocess.CompletedProcess(command, 0, json.dumps(snapper), "")
            return subprocess.CompletedProcess(command, 0, json.dumps(findmnt), "")

        state = discover_machine_state(stated_command="/fake/stated", runner=runner)

        self.assertEqual("direct_probes", state.source)
        self.assertEqual("failed", state.stated_status)
        self.assertEqual("stated_invalid", state.fallback_errors[0]["code"])
        self.assertEqual("/@", state.active_root["subvolume"])


if __name__ == "__main__":
    unittest.main()
