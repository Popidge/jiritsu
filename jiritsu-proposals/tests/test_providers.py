from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from jiritsu_proposals.providers import (  # noqa: E402
    assess_workloads,
    collect_machine_state,
    prepare_recovery,
)


class ProviderTests(unittest.TestCase):
    def test_machine_state_uses_baselines_when_stated_is_unavailable(self) -> None:
        baseline = (
            {
                "system.hostname": "fallback-host",
                "system.omarchy.version": "4.0-test",
            },
            [],
        )
        with (
            patch("jiritsu_proposals.providers.resolve_command", return_value=None),
            patch("jiritsu_proposals.providers._baseline_facts", return_value=baseline),
        ):
            result = collect_machine_state()

        self.assertEqual("ok", result["status"])
        self.assertEqual("baseline", result["selected_provider"])
        self.assertEqual("provider_unavailable", result["fallback_errors"][0]["code"])

    def test_workload_provider_reports_standalone_fallback(self) -> None:
        with patch("jiritsu_proposals.providers.resolve_command", return_value=None):
            result = assess_workloads()

        self.assertEqual("unavailable", result["status"])
        self.assertEqual("none", result["selected_provider"])
        self.assertEqual("provider_unavailable", result["fallback_errors"][0]["code"])

    def test_workload_provider_extracts_critical_failures(self) -> None:
        payload = {
            "schema_version": "1.1",
            "status": "unhealthy",
            "workloads": [
                {
                    "id": "desktop",
                    "capabilities": [
                        {"id": "session", "importance": "critical", "status": "fail"},
                        {"id": "audio", "importance": "useful", "status": "fail"},
                    ],
                }
            ],
            "summary": {"workload_count": 1},
        }
        completed = subprocess.CompletedProcess([], 1, json.dumps(payload), "")
        with (
            patch(
                "jiritsu_proposals.providers.resolve_command",
                return_value="/test/jiritsu-workload",
            ),
            patch("jiritsu_proposals.providers._run", return_value=completed),
        ):
            result = assess_workloads()

        self.assertEqual("unhealthy", result["status"])
        self.assertEqual(["desktop/session"], result["critical_failures"])
        self.assertEqual("jiritsu-workload", result["selected_provider"])

    def test_checkpoint_provider_captures_minimal_nonoverlapping_paths(self) -> None:
        checkpoint = {
            "id": "cp-test",
            "scope": {"user_config": {"status": "captured"}},
        }
        completed = subprocess.CompletedProcess(
            [],
            0,
            json.dumps(
                {
                    "schema_version": "1.0",
                    "status": "ready",
                    "checkpoint": checkpoint,
                    "errors": [],
                }
            ),
            "",
        )
        plan = {
            "required": False,
            "status": "planned",
            "selected_provider": "jiritsu-checkpoints",
            "source": "/test/jiritsu-checkpoints",
            "jiritsu_checkpoints": "/test/jiritsu-checkpoints",
            "fallback_provider": "action_local_backup",
            "fallback_errors": [],
        }
        actions = [
            {"type": "config.mkdir", "path": "desktop", "mode": "0700"},
            {
                "type": "config.write",
                "path": "desktop/theme.conf",
                "content": "dark=true\n",
                "mode": "0600",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            recovery = root / "recovery"
            config.mkdir()
            with patch(
                "jiritsu_proposals.providers._run", return_value=completed
            ) as invoke:
                result = prepare_recovery(
                    plan,
                    proposal_id="proposal-1",
                    summary="Change the theme.",
                    actions=actions,
                    config_root=config,
                    recovery_dir=recovery,
                    timeout=5.0,
                )
            policy = (recovery / "checkpoint-policy.toml").read_text()

        self.assertEqual("jiritsu-checkpoints", result["selected_provider"])
        self.assertEqual("cp-test", result["checkpoint_id"])
        self.assertEqual(["desktop"], result["policy"]["include"])
        self.assertIn('  "desktop",', policy)
        command = invoke.call_args.args[0]
        self.assertIn("--system", command)
        self.assertEqual("off", command[command.index("--system") + 1])
        self.assertEqual("proposal-1", command[command.index("--proposal") + 1])

    def test_checkpoint_failure_selects_action_local_recovery(self) -> None:
        plan = {
            "required": False,
            "status": "planned",
            "selected_provider": "jiritsu-checkpoints",
            "source": "/test/jiritsu-checkpoints",
            "jiritsu_checkpoints": "/test/jiritsu-checkpoints",
            "fallback_provider": "action_local_backup",
            "fallback_errors": [],
        }
        completed = subprocess.CompletedProcess([], 1, "not-json", "capture failed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config"
            config.mkdir()
            with patch("jiritsu_proposals.providers._run", return_value=completed):
                result = prepare_recovery(
                    plan,
                    proposal_id="proposal-1",
                    summary="Change one file.",
                    actions=[
                        {
                            "type": "config.write",
                            "path": "app.conf",
                            "content": "ready=true\n",
                            "mode": "0600",
                        }
                    ],
                    config_root=config,
                    recovery_dir=root / "recovery",
                    timeout=5.0,
                )

        self.assertEqual("action_local_backup", result["selected_provider"])
        self.assertEqual("provider_failed", result["fallback_errors"][-1]["code"])


if __name__ == "__main__":
    unittest.main()
