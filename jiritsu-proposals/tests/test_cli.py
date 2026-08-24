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

from jiritsu_proposals.cli import (  # noqa: E402
    EXIT_DATA,
    EXIT_OK,
    EXIT_OPERATION,
    EXIT_USAGE,
    main,
)
from jiritsu_proposals.validation import sha256_bytes  # noqa: E402


MACHINE_STATE = {
    "status": "ok",
    "selected_provider": "jiritsu-stated",
    "source": "/test/jiritsu-stated",
    "requested_facts": ["system.hostname"],
    "facts": {"system.hostname": {"value": "test-host"}},
    "fallback_errors": [],
}
HEALTHY_WORKLOADS = {
    "status": "healthy",
    "selected_provider": "jiritsu-workload",
    "source": "/test/jiritsu-workload",
    "critical_failures": [],
    "fallback_errors": [],
}
RECOVERY = {
    "required": False,
    "status": "ready",
    "selected_provider": "action_local_backup",
    "source": "jiritsu-proposals",
    "jiritsu_checkpoints": None,
    "fallback_errors": [],
}


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state_dir = self.root / "state"
        self.config_root = self.root / "config"
        self.config_root.mkdir()
        self.definition_path = self.root / "definition.json"
        self.provider_patches = [
            patch(
                "jiritsu_proposals.operations.collect_machine_state",
                return_value=MACHINE_STATE,
            ),
            patch(
                "jiritsu_proposals.operations.assess_workloads",
                return_value=HEALTHY_WORKLOADS,
            ),
            patch(
                "jiritsu_proposals.operations.recovery_provider",
                return_value=RECOVERY,
            ),
            patch(
                "jiritsu_proposals.operations.prepare_recovery",
                side_effect=lambda plan, **_: {**plan, "status": "ready"},
            ),
        ]
        for provider_patch in self.provider_patches:
            provider_patch.start()
            self.addCleanup(provider_patch.stop)

    def invoke(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(
                (
                    "--state-dir",
                    str(self.state_dir),
                    "--config-root",
                    str(self.config_root),
                    *arguments,
                )
            )
        return status, json.loads(output.getvalue())

    def definition(
        self,
        *,
        kind: str = "agent",
        actor: str = "test-agent",
        actions: list[dict] | None = None,
    ) -> dict:
        return {
            "schema_version": "1.0",
            "intent": {
                "summary": "Add a deterministic test configuration.",
                "rationale": "The test needs one durable user configuration file.",
            },
            "origin": {"kind": kind, "actor": actor, "request_id": "request-7"},
            "actions": actions
            or [
                {"type": "config.mkdir", "path": "jiritsu-test", "mode": "0700"},
                {
                    "type": "config.write",
                    "path": "jiritsu-test/example.conf",
                    "content": "enabled=true\n",
                    "mode": "0600",
                },
            ],
        }

    def create(
        self, definition: dict | None = None, proposal_id: str = "test-1"
    ) -> dict:
        self.definition_path.write_text(
            json.dumps(definition or self.definition()), encoding="utf-8"
        )
        status, result = self.invoke(
            "create", str(self.definition_path), "--id", proposal_id
        )
        self.assertEqual(EXIT_OK, status, result)
        return result["proposal"]

    def classify_and_approve(self, proposal_id: str = "test-1") -> None:
        status, result = self.invoke("classify", proposal_id, "--actor", "classifier")
        self.assertEqual(EXIT_OK, status, result)
        status, result = self.invoke(
            "approve", proposal_id, "--actor", "human-owner", "--note", "Reviewed."
        )
        self.assertEqual(EXIT_OK, status, result)

    def test_complete_lifecycle_commits_typed_actions(self) -> None:
        created = self.create()
        self.assertEqual("draft", created["state"])
        self.assertEqual("agent", created["origin"]["kind"])
        self.classify_and_approve()

        status, result = self.invoke("promote", "test-1", "--actor", "operator")

        self.assertEqual(EXIT_OK, status, result)
        proposal = result["proposal"]
        self.assertEqual("committed", proposal["state"])
        self.assertEqual("low", proposal["classification"]["risk"]["level"])
        self.assertEqual("approved", proposal["approval"]["status"])
        self.assertTrue(
            all(
                item["status"] == "pass"
                for item in proposal["promotion"]["verification"]
            )
        )
        target = self.config_root / "jiritsu-test" / "example.conf"
        self.assertEqual("enabled=true\n", target.read_text(encoding="utf-8"))
        self.assertEqual(0o600, target.stat().st_mode & 0o777)
        self.assertEqual(
            ["created", "classified", "approved", "promotion_started", "committed"],
            [item["type"] for item in proposal["history"]],
        )

    def test_list_uses_summaries_and_does_not_disclose_action_content(self) -> None:
        self.create()

        status, result = self.invoke("list")

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("test-1", result["proposals"][0]["id"])
        self.assertNotIn("actions", result["proposals"][0])
        self.assertNotIn("enabled=true", json.dumps(result))

    def test_arbitrary_command_action_is_rejected(self) -> None:
        definition = self.definition(
            actions=[{"type": "command", "command": ["sh", "-c", "true"]}]
        )
        self.definition_path.write_text(json.dumps(definition), encoding="utf-8")

        status, result = self.invoke("create", str(self.definition_path))

        self.assertEqual(EXIT_DATA, status)
        self.assertEqual("proposal_invalid", result["errors"][0]["code"])
        self.assertFalse(self.state_dir.exists())

    def test_non_normalized_action_path_is_rejected(self) -> None:
        definition = self.definition(
            actions=[{"type": "config.mkdir", "path": "one//two"}]
        )
        self.definition_path.write_text(json.dumps(definition), encoding="utf-8")

        status, result = self.invoke("create", str(self.definition_path))

        self.assertEqual(EXIT_DATA, status)
        self.assertEqual("proposal_invalid", result["errors"][0]["code"])

    def test_replacing_a_file_requires_an_exact_precondition(self) -> None:
        target = self.config_root / "existing.conf"
        target.write_text("old\n", encoding="utf-8")
        self.create(
            self.definition(
                actions=[
                    {
                        "type": "config.write",
                        "path": "existing.conf",
                        "content": "new\n",
                    }
                ]
            )
        )

        status, result = self.invoke("classify", "test-1", "--actor", "classifier")

        self.assertEqual(EXIT_OPERATION, status)
        self.assertEqual("precondition_required", result["errors"][0]["code"])
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))

    def test_target_drift_invalidates_the_approval(self) -> None:
        self.create()
        self.classify_and_approve()
        target_dir = self.config_root / "jiritsu-test"
        target_dir.mkdir()

        status, result = self.invoke("promote", "test-1", "--actor", "operator")

        self.assertEqual(EXIT_OPERATION, status)
        self.assertEqual("target_state_changed", result["errors"][0]["code"])
        status, shown = self.invoke("show", "test-1")
        self.assertEqual(EXIT_OK, status)
        self.assertEqual("approved", shown["proposal"]["state"])

    def test_verification_failure_restores_all_applied_actions(self) -> None:
        self.create()
        self.classify_and_approve()
        failed = [
            {
                "type": "config.content_sha256",
                "path": "jiritsu-test/example.conf",
                "expected": "0" * 64,
                "actual": "1" * 64,
                "status": "fail",
            }
        ]

        with patch("jiritsu_proposals.operations.verify_actions", return_value=failed):
            status, result = self.invoke("promote", "test-1", "--actor", "operator")

        self.assertEqual(EXIT_OPERATION, status)
        self.assertEqual("rolled_back", result["proposal"]["state"])
        self.assertEqual("verification_failed", result["errors"][0]["code"])
        self.assertFalse((self.config_root / "jiritsu-test").exists())
        self.assertTrue(
            all(
                item["status"] == "restored"
                for item in result["proposal"]["promotion"]["rollback"]
            )
        )

    def test_new_critical_workload_failure_rolls_back_the_actions(self) -> None:
        self.create()
        self.classify_and_approve()
        unhealthy = {
            **HEALTHY_WORKLOADS,
            "status": "unhealthy",
            "critical_failures": ["omarchy-desktop/session"],
        }

        with patch(
            "jiritsu_proposals.operations.assess_workloads",
            side_effect=[HEALTHY_WORKLOADS, unhealthy],
        ):
            status, result = self.invoke("promote", "test-1", "--actor", "operator")

        self.assertEqual(EXIT_OPERATION, status)
        self.assertEqual("rolled_back", result["proposal"]["state"])
        self.assertEqual("workload_regression", result["errors"][0]["code"])
        self.assertFalse((self.config_root / "jiritsu-test").exists())

    def test_newly_available_workload_provider_does_not_report_a_regression(
        self,
    ) -> None:
        self.create()
        self.classify_and_approve()
        unavailable = {
            **HEALTHY_WORKLOADS,
            "status": "unavailable",
            "selected_provider": "none",
        }
        unhealthy = {
            **HEALTHY_WORKLOADS,
            "status": "unhealthy",
            "critical_failures": ["omarchy-desktop/session"],
        }

        with patch(
            "jiritsu_proposals.operations.assess_workloads",
            side_effect=[unavailable, unhealthy],
        ):
            status, result = self.invoke("promote", "test-1", "--actor", "operator")

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("committed", result["proposal"]["state"])

    def test_existing_file_is_restored_after_a_failed_promotion(self) -> None:
        target = self.config_root / "existing.conf"
        target.write_text("old\n", encoding="utf-8")
        digest = sha256_bytes(b"old\n")
        self.create(
            self.definition(
                actions=[
                    {
                        "type": "config.write",
                        "path": "existing.conf",
                        "content": "new\n",
                        "expected_sha256": digest,
                    }
                ]
            )
        )
        self.classify_and_approve()
        with patch(
            "jiritsu_proposals.operations.verify_actions",
            return_value=[{"status": "fail"}],
        ):
            status, result = self.invoke("promote", "test-1", "--actor", "operator")

        self.assertEqual(EXIT_OPERATION, status)
        self.assertEqual("rolled_back", result["proposal"]["state"])
        self.assertEqual("old\n", target.read_text(encoding="utf-8"))
        backup = self.state_dir / "test-1" / "recovery" / "00.file"
        self.assertEqual(0o600, backup.stat().st_mode & 0o777)

    def test_rejection_records_actor_reason_and_terminal_state(self) -> None:
        self.create(self.definition(kind="human", actor="jamie"))

        status, result = self.invoke(
            "reject", "test-1", "--actor", "reviewer", "--reason", "Not required."
        )

        self.assertEqual(EXIT_OK, status)
        proposal = result["proposal"]
        self.assertEqual("rejected", proposal["state"])
        self.assertEqual("reviewer", proposal["approval"]["rejected_by"])
        self.assertEqual("Not required.", proposal["history"][-1]["details"]["reason"])

    def test_empty_lifecycle_actor_is_a_usage_error(self) -> None:
        self.create()

        status, result = self.invoke("classify", "test-1", "--actor", " ")

        self.assertEqual(EXIT_USAGE, status)
        self.assertEqual("invalid_request", result["errors"][0]["code"])

    def test_proposal_record_and_store_are_private(self) -> None:
        self.create()
        proposal_path = self.state_dir / "test-1" / "proposal.json"

        self.assertEqual(0o700, self.state_dir.stat().st_mode & 0o777)
        self.assertEqual(0o700, proposal_path.parent.stat().st_mode & 0o777)
        self.assertEqual(0o600, proposal_path.stat().st_mode & 0o777)

    def test_invalid_stored_record_returns_structured_data_error(self) -> None:
        self.create()
        proposal_path = self.state_dir / "test-1" / "proposal.json"
        proposal_path.write_text('{"id":"test-1"}\n', encoding="utf-8")

        status, result = self.invoke("show", "test-1")

        self.assertEqual(EXIT_DATA, status)
        self.assertEqual("store_error", result["errors"][0]["code"])


if __name__ == "__main__":
    unittest.main()
