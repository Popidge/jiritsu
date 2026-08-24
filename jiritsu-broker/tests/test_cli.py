from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


MODULE_ROOT = Path(__file__).resolve().parents[1]
COMMAND = MODULE_ROOT / "bin" / "jiritsu-broker"


class BrokerCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "broker-state"
        self.environment = os.environ.copy()
        self.environment.update(
            {
                "HOME": str(self.root / "home"),
                "JIRITSU_BROKER_STATE_DIR": str(self.state),
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        request_id: str = "request-1",
        actor: str = "test-agent",
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "request_id": request_id,
            "actor": actor,
            "operation": operation,
            "arguments": arguments,
        }

    def run_broker(
        self,
        *arguments: str,
        payload: dict[str, Any] | None = None,
        environment: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        completed = subprocess.run(
            [str(COMMAND), *arguments],
            input=json.dumps(payload) if payload is not None else None,
            capture_output=True,
            text=True,
            check=False,
            env=environment or self.environment,
        )
        try:
            result = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            self.fail(
                f"invalid broker JSON: {error}\nstdout={completed.stdout!r}\nstderr={completed.stderr!r}"
            )
        return completed.returncode, result

    def fake_command(self, name: str, body: str) -> Path:
        path = self.root / name
        path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
        path.chmod(0o700)
        return path

    def write_approval(
        self,
        request: dict[str, Any],
        environment: dict[str, str],
        *,
        approved_by: str = "human:test",
    ) -> None:
        code, fingerprint = self.run_broker(
            "fingerprint", payload=request, environment=environment
        )
        self.assertEqual(0, code)
        approval_path = Path(fingerprint["approval_path"])
        approval_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        approval_path.parent.chmod(0o700)
        approval_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "request_id": request["request_id"],
                    "request_sha256": fingerprint["request_sha256"],
                    "approved_by": approved_by,
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        approval_path.chmod(0o600)

    def test_catalog_is_the_small_typed_tool_surface(self) -> None:
        code, payload = self.run_broker("catalog")

        self.assertEqual(0, code)
        self.assertEqual(
            [
                "state.query",
                "workload.assess",
                "proposal.create",
                "proposal.classify",
                "proposal.approve",
                "proposal.query",
                "proposal.list",
                "proposal.promote",
                "checkpoint.inspect",
                "checkpoint.query",
                "checkpoint.list",
            ],
            [tool["id"] for tool in payload["tools"]],
        )
        self.assertEqual(
            "effective operating-system user", payload["policy"]["principal_source"]
        )

    def test_state_query_uses_exact_adapter_and_records_four_events(self) -> None:
        capture = self.root / "arguments.json"
        fake = self.fake_command(
            "fake-stated",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{
  "schema_version": "1.0", "status": "ok", "facts": {{
    "system.hostname": {{"value": "fixture-host"}}
  }}, "errors": []
}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_STATED_COMMAND": str(fake)
        }

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "state.query",
                {"selectors": ["system.hostname"], "timeout_seconds": 2},
            ),
            environment=environment,
        )

        self.assertEqual(0, code)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("jiritsu-stated", payload["result"]["selected_provider"])
        self.assertEqual(
            ["query", "system.hostname", "--timeout", "2"],
            json.loads(capture.read_text()),
        )
        records = [
            json.loads(line)
            for line in (self.state / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            ["request", "decision", "action", "result"],
            [record["event"] for record in records],
        )
        self.assertFalse(records[2]["payload"]["shell"])
        self.assertEqual(0o700, stat.S_IMODE(self.state.stat().st_mode))
        self.assertEqual(
            0o600, stat.S_IMODE((self.state / "audit.jsonl").stat().st_mode)
        )
        audit_code, audit = self.run_broker("audit", "request-1")
        self.assertEqual(0, audit_code)
        self.assertEqual(4, len(audit["records"]))

    def test_state_query_falls_back_to_standard_linux(self) -> None:
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_STATED_COMMAND": str(self.root / "missing")
        }
        code, payload = self.run_broker(
            "request",
            payload=self.request("state.query", {"selectors": ["system.hostname"]}),
            environment=environment,
        )

        self.assertEqual(0, code)
        self.assertEqual("baseline", payload["result"]["selected_provider"])
        self.assertIn("system.hostname", payload["result"]["data"]["facts"])
        self.assertEqual(
            "provider_failed", payload["result"]["fallback_errors"][0]["code"]
        )

    def test_workload_assessment_preserves_a_degraded_module_result(self) -> None:
        capture = self.root / "workload-arguments.json"
        fake = self.fake_command(
            "fake-workload",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{"schema_version": "1.1", "status": "degraded", "workloads": [], "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_WORKLOAD_COMMAND": str(fake)
        }

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "workload.assess",
                {"selectors": ["agent-development"], "timeout_seconds": 2},
            ),
            environment=environment,
        )

        self.assertEqual(0, code)
        self.assertEqual("ok", payload["status"])
        self.assertEqual("degraded", payload["result"]["data"]["status"])
        self.assertEqual(
            ["assess", "agent-development", "--timeout", "2"],
            json.loads(capture.read_text()),
        )

    def test_rule_without_required_authority_denies_before_action(self) -> None:
        policy = self.root / "policy.toml"
        policy.write_text(
            """schema_version = "1.0"
default = "deny"
[[rules]]
id = "insufficient"
principals = ["*"]
operations = ["state.query"]
decision = "allow"
authorities = ["proposal.read"]
""",
            encoding="utf-8",
        )

        code, payload = self.run_broker(
            "request",
            "--policy",
            str(policy),
            payload=self.request("state.query", {"selectors": ["system.hostname"]}),
        )

        self.assertEqual(3, code)
        self.assertEqual("denied", payload["status"])
        self.assertIn("machine_state.read", payload["decision"]["reason"])
        records = [
            json.loads(line)
            for line in (self.state / "audit.jsonl").read_text().splitlines()
        ]
        self.assertEqual(
            ["request", "decision", "result"], [item["event"] for item in records]
        )

    def test_environment_username_cannot_claim_a_policy_principal(self) -> None:
        policy = self.root / "policy.toml"
        policy.write_text(
            """schema_version = "1.0"
default = "deny"
[[rules]]
id = "forged-user"
principals = ["user:forged-agent"]
operations = ["state.query"]
decision = "allow"
authorities = ["machine_state.read"]
""",
            encoding="utf-8",
        )
        environment = self.environment | {"USER": "forged-agent"}

        code, payload = self.run_broker(
            "request",
            "--policy",
            str(policy),
            payload=self.request("state.query", {"selectors": ["system.hostname"]}),
            environment=environment,
        )

        self.assertEqual(3, code)
        self.assertEqual("denied", payload["status"])
        self.assertTrue(payload["decision"]["principal"].startswith("uid:"))

    def test_request_cannot_embed_approval_or_extra_authority(self) -> None:
        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "proposal.promote",
                {"proposal_id": "p-one", "approval": True},
            ),
        )

        self.assertEqual(1, code)
        self.assertEqual("error", payload["status"])
        self.assertEqual("invalid_arguments", payload["errors"][0]["code"])
        self.assertIsNone(payload["action"])

    def test_matching_external_approval_allows_sensitive_operation(self) -> None:
        capture = self.root / "proposal-arguments.json"
        fake = self.fake_command(
            "fake-proposals",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{"schema_version": "1.0", "status": "ok", "proposal": {{"state": "committed"}}, "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_PROPOSALS_COMMAND": str(fake)
        }
        request = self.request(
            "proposal.promote",
            {"proposal_id": "p-one", "timeout_seconds": 1},
            request_id="promote-1",
        )

        first_code, first = self.run_broker(
            "request", payload=request, environment=environment
        )
        self.assertEqual(3, first_code)
        self.assertEqual("approval_required", first["status"])
        fingerprint_code, fingerprint = self.run_broker(
            "fingerprint", payload=request, environment=environment
        )
        self.assertEqual(0, fingerprint_code)
        approval_path = Path(fingerprint["approval_path"])
        approval_path.parent.mkdir(mode=0o700, parents=True)
        approval_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "request_id": "promote-1",
                    "request_sha256": fingerprint["request_sha256"],
                    "approved_by": "human:test",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        approval_path.chmod(0o600)

        second_code, second = self.run_broker(
            "request", payload=request, environment=environment
        )

        self.assertEqual(0, second_code)
        self.assertEqual("ok", second["status"])
        self.assertTrue(second["decision"]["approval"]["approved"])
        self.assertEqual(
            ["promote", "p-one", "--actor", "broker:test-agent", "--timeout", "1"],
            json.loads(capture.read_text()),
        )

    def test_group_writable_approval_cannot_grant_authority(self) -> None:
        marker = self.root / "provider-ran"
        fake = self.fake_command(
            "fake-proposals",
            f"""import json, pathlib
pathlib.Path({str(marker)!r}).touch()
print(json.dumps({{"schema_version": "1.0", "status": "ok", "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_PROPOSALS_COMMAND": str(fake)
        }
        request = self.request(
            "proposal.promote",
            {"proposal_id": "p-one"},
            request_id="unsafe-approval-1",
        )
        _, fingerprint = self.run_broker(
            "fingerprint", payload=request, environment=environment
        )
        approval_path = Path(fingerprint["approval_path"])
        approval_path.parent.mkdir(mode=0o700, parents=True)
        approval_path.write_text(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "request_id": request["request_id"],
                    "request_sha256": fingerprint["request_sha256"],
                    "approved_by": "human:test",
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=5)
                    ).isoformat(),
                }
            ),
            encoding="utf-8",
        )
        approval_path.chmod(0o666)

        code, payload = self.run_broker(
            "request", payload=request, environment=environment
        )

        self.assertEqual(3, code)
        self.assertEqual("approval_required", payload["status"])
        self.assertIn("group or other", payload["decision"]["approval"]["reason"])
        self.assertFalse(marker.exists())

    def test_proposal_create_injects_agent_provenance(self) -> None:
        capture = self.root / "definition.json"
        fake = self.fake_command(
            "fake-proposals",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(sys.stdin.read())
print(json.dumps({{"schema_version": "1.0", "status": "ok", "proposal": {{"state": "draft"}}, "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_PROPOSALS_COMMAND": str(fake)
        }

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "proposal.create",
                {
                    "intent": {"summary": "Test", "rationale": "Verify provenance."},
                    "actions": [{"type": "config.mkdir", "path": "broker-test"}],
                    "proposal_id": "p-broker-test",
                },
                request_id="create-1",
                actor="agent-one",
            ),
            environment=environment,
        )

        self.assertEqual(0, code)
        self.assertEqual("ok", payload["status"])
        definition = json.loads(capture.read_text())
        self.assertEqual(
            {"kind": "agent", "actor": "agent-one", "request_id": "create-1"},
            definition["origin"],
        )

    def test_proposal_classify_uses_the_integrated_provider_contract(self) -> None:
        capture = self.root / "classify-arguments.json"
        fake = self.fake_command(
            "fake-proposals",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{"schema_version": "1.0", "status": "ok", "proposal": {{"state": "classified"}}, "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_PROPOSALS_COMMAND": str(fake)
        }

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "proposal.classify",
                {"proposal_id": "p-one", "timeout_seconds": 2},
                request_id="classify-1",
            ),
            environment=environment,
        )

        self.assertEqual(0, code)
        self.assertEqual("ok", payload["status"])
        self.assertEqual(
            ["classify", "p-one", "--actor", "broker:test-agent", "--timeout", "2"],
            json.loads(capture.read_text()),
        )

    def test_proposal_approval_records_the_external_approver(self) -> None:
        capture = self.root / "approve-arguments.json"
        fake = self.fake_command(
            "fake-proposals",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{"schema_version": "1.0", "status": "ok", "proposal": {{"state": "approved"}}, "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_PROPOSALS_COMMAND": str(fake)
        }
        request = self.request(
            "proposal.approve",
            {"proposal_id": "p-one", "note": "Reviewed exact actions."},
            request_id="approve-1",
        )

        code, payload = self.run_broker(
            "request", payload=request, environment=environment
        )
        self.assertEqual(3, code)
        self.assertEqual("approval_required", payload["status"])
        self.write_approval(request, environment, approved_by="human:reviewer")

        code, payload = self.run_broker(
            "request", payload=request, environment=environment
        )

        self.assertEqual(0, code)
        self.assertEqual("ok", payload["status"])
        self.assertEqual(
            [
                "approve",
                "p-one",
                "--actor",
                "human:reviewer",
                "--note",
                "Reviewed exact actions.",
            ],
            json.loads(capture.read_text()),
        )

    def test_policy_can_grant_proposal_approval_to_its_principal(self) -> None:
        capture = self.root / "policy-approve-arguments.json"
        fake = self.fake_command(
            "fake-proposals",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{"schema_version": "1.0", "status": "ok", "proposal": {{"state": "approved"}}, "errors": []}}))
""",
        )
        policy = self.root / "approve-policy.toml"
        policy.write_text(
            """schema_version = "1.0"
default = "deny"
[[rules]]
id = "local-approver"
principals = ["*"]
operations = ["proposal.approve"]
decision = "allow"
authorities = ["proposal.approval.write"]
""",
            encoding="utf-8",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_PROPOSALS_COMMAND": str(fake)
        }

        code, payload = self.run_broker(
            "request",
            "--policy",
            str(policy),
            payload=self.request(
                "proposal.approve",
                {"proposal_id": "p-one"},
                request_id="policy-approve-1",
            ),
            environment=environment,
        )

        self.assertEqual(0, code)
        self.assertEqual("ok", payload["status"])
        arguments = json.loads(capture.read_text())
        self.assertEqual(["approve", "p-one", "--actor"], arguments[:3])
        self.assertTrue(arguments[3].startswith("policy:uid:"))

    def test_checkpoint_inspect_uses_the_checkpoint_adapter(self) -> None:
        capture = self.root / "checkpoint-arguments.json"
        fake = self.fake_command(
            "fake-checkpoints",
            f"""import json, pathlib, sys
pathlib.Path({str(capture)!r}).write_text(json.dumps(sys.argv[1:]))
print(json.dumps({{"schema_version": "1.0", "status": "ok", "action": "inspect", "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_CHECKPOINTS_COMMAND": str(fake)
        }

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "checkpoint.inspect",
                {"timeout_seconds": 2},
                request_id="checkpoint-inspect-1",
            ),
            environment=environment,
        )

        self.assertEqual(0, code)
        self.assertEqual("jiritsu-checkpoints", payload["result"]["selected_provider"])
        self.assertEqual(
            ["inspect", "--timeout", "2"], json.loads(capture.read_text())
        )

    def test_missing_checkpoint_provider_is_scoped_to_checkpoint_operations(self) -> None:
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_CHECKPOINTS_COMMAND": str(
                self.root / "missing-checkpoints"
            ),
            "JIRITSU_BROKER_JIRITSU_STATED_COMMAND": str(self.root / "missing-stated"),
        }

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "checkpoint.list", {}, request_id="missing-checkpoint-1"
            ),
            environment=environment,
        )
        self.assertEqual(1, code)
        self.assertEqual("error", payload["status"])
        self.assertEqual("provider_failed", payload["errors"][0]["code"])

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "state.query",
                {"selectors": ["system.hostname"]},
                request_id="state-after-missing-checkpoint-1",
            ),
            environment=environment,
        )
        self.assertEqual(0, code)
        self.assertEqual("baseline", payload["result"]["selected_provider"])

    def test_real_five_module_happy_path_commits_with_a_linked_checkpoint(self) -> None:
        config_root = self.root / "config"
        config_root.mkdir()
        environment = self.environment | {
            "XDG_CONFIG_HOME": str(config_root),
            "XDG_STATE_HOME": str(self.root / "state"),
            "JIRITSU_PROPOSALS_CONFIG_ROOT": str(config_root),
        }
        create = self.request(
            "proposal.create",
            {
                "proposal_id": "p-five-module",
                "intent": {
                    "summary": "Create the five-module integration fixture.",
                    "rationale": "Verify the complete preferred-provider path.",
                },
                "actions": [
                    {"type": "config.mkdir", "path": "jiritsu-integration"},
                    {
                        "type": "config.write",
                        "path": "jiritsu-integration/result.conf",
                        "content": "integrated=true\n",
                    },
                ],
            },
            request_id="five-create-1",
        )
        code, payload = self.run_broker(
            "request", payload=create, environment=environment
        )
        self.assertEqual(0, code)
        self.assertEqual("draft", payload["result"]["data"]["proposal"]["state"])

        classify = self.request(
            "proposal.classify",
            {"proposal_id": "p-five-module", "timeout_seconds": 3},
            request_id="five-classify-1",
        )
        code, payload = self.run_broker(
            "request", payload=classify, environment=environment
        )
        self.assertEqual(0, code)
        proposal = payload["result"]["data"]["proposal"]
        self.assertEqual("classified", proposal["state"])
        self.assertEqual(
            "jiritsu-stated",
            proposal["classification"]["machine_state"]["selected_provider"],
        )
        self.assertEqual(
            "jiritsu-workload",
            proposal["classification"]["workload_state"]["selected_provider"],
        )
        self.assertEqual(
            "jiritsu-checkpoints", proposal["recovery"]["selected_provider"]
        )

        approve = self.request(
            "proposal.approve",
            {"proposal_id": "p-five-module", "note": "Integration fixture only."},
            request_id="five-approve-1",
        )
        self.write_approval(approve, environment, approved_by="human:integration")
        code, payload = self.run_broker(
            "request", payload=approve, environment=environment
        )
        self.assertEqual(0, code)
        self.assertEqual(
            "human:integration",
            payload["result"]["data"]["proposal"]["approval"]["approved_by"],
        )

        promote = self.request(
            "proposal.promote",
            {"proposal_id": "p-five-module", "timeout_seconds": 3},
            request_id="five-promote-1",
        )
        self.write_approval(promote, environment, approved_by="human:integration")
        code, payload = self.run_broker(
            "request", payload=promote, environment=environment
        )
        self.assertEqual(0, code)
        proposal = payload["result"]["data"]["proposal"]
        self.assertEqual("committed", proposal["state"])
        checkpoint = proposal["promotion"]["checkpoint"]
        self.assertEqual("jiritsu-checkpoints", checkpoint["selected_provider"])
        checkpoint_id = checkpoint["checkpoint_id"]
        self.assertEqual(
            "integrated=true\n",
            (config_root / "jiritsu-integration" / "result.conf").read_text(),
        )

        code, payload = self.run_broker(
            "request",
            payload=self.request(
                "checkpoint.query",
                {"checkpoint_id": checkpoint_id},
                request_id="five-checkpoint-query-1",
            ),
            environment=environment,
        )
        self.assertEqual(0, code)
        checkpoint_record = payload["result"]["data"]["checkpoint"]
        self.assertEqual("p-five-module", checkpoint_record["proposal_id"])
        self.assertEqual("ready", checkpoint_record["status"])

    def test_terminal_request_id_cannot_execute_twice(self) -> None:
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_STATED_COMMAND": str(self.root / "missing")
        }
        request = self.request("state.query", {"selectors": ["system.hostname"]})
        self.assertEqual(
            0, self.run_broker("request", payload=request, environment=environment)[0]
        )

        code, payload = self.run_broker(
            "request", payload=request, environment=environment
        )

        self.assertEqual(64, code)
        self.assertEqual("duplicate_request", payload["errors"][0]["code"])

    def test_concurrent_duplicate_request_executes_once(self) -> None:
        capture = self.root / "provider-runs"
        fake = self.fake_command(
            "fake-stated",
            f"""import json, pathlib, time
with pathlib.Path({str(capture)!r}).open("a") as output:
    output.write("run\\n")
time.sleep(0.2)
print(json.dumps({{"schema_version": "1.0", "status": "ok", "facts": {{}}, "errors": []}}))
""",
        )
        environment = self.environment | {
            "JIRITSU_BROKER_JIRITSU_STATED_COMMAND": str(fake)
        }
        request_path = self.root / "request.json"
        request_path.write_text(
            json.dumps(self.request("state.query", {"selectors": ["system.hostname"]})),
            encoding="utf-8",
        )
        processes = [
            subprocess.Popen(
                [str(COMMAND), "request", str(request_path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
            for _ in range(2)
        ]

        results = [process.communicate(timeout=5) for process in processes]

        self.assertEqual([0, 64], sorted(process.returncode for process in processes))
        self.assertTrue(
            all(
                json.loads(stdout)["status"] in {"ok", "error"} for stdout, _ in results
            )
        )
        self.assertEqual(["run"], capture.read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
