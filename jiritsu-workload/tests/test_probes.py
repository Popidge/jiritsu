from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from jiritsu_workload.model import Check  # noqa: E402
from jiritsu_workload.probes import run_check  # noqa: E402


class SystemdProbeTests(unittest.TestCase):
    def systemd_check(self, state: str) -> Check:
        return Check(
            check_id="unit-state",
            check_type="systemd_unit",
            description="Read one unit state.",
            parameters={
                "unit": "example.service",
                "scope": "user",
                "state": state,
            },
        )

    @patch("jiritsu_workload.probes.subprocess.run")
    def test_active_state_uses_the_exact_systemd_value(self, run: object) -> None:
        run.return_value = subprocess.CompletedProcess([], 0, "failed\n", "")  # type: ignore[attr-defined]

        result = run_check(self.systemd_check("active"))

        self.assertEqual("fail", result["status"])
        self.assertEqual("failed", result["details"]["actual_state"])
        command = run.call_args.args[0]  # type: ignore[attr-defined]
        self.assertEqual(
            [
                "systemctl",
                "--user",
                "show",
                "--property=ActiveState",
                "--value",
                "example.service",
            ],
            command,
        )


if __name__ == "__main__":
    unittest.main()
