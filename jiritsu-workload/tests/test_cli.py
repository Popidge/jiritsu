from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from jiritsu_workload.cli import (  # noqa: E402
    EXIT_CONFIG,
    EXIT_DEGRADED,
    EXIT_OK,
    EXIT_UNHEALTHY,
    EXIT_USAGE,
    main,
)


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.config_dir = Path(self.temporary_directory.name) / "config"

    def invoke(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(("--config-dir", str(self.config_dir), *arguments))
        return status, json.loads(output.getvalue())

    def write_contract(
        self,
        contract_id: str,
        *,
        importance: str = "critical",
        check: str,
        extra_root: str = "",
    ) -> Path:
        path = Path(self.temporary_directory.name) / f"{contract_id}.toml"
        path.write_text(
            textwrap.dedent(
                f'''\
                schema_version = "1.0"
                id = "{contract_id}"
                title = "Test contract"
                description = "A deterministic contract for the test suite."
                {extra_root}

                [[capabilities]]
                id = "test-capability"
                title = "Test capability"
                description = "The test capability has one direct check."
                importance = "{importance}"

                [[capabilities.checks]]
                id = "test-check"
                description = "Run a deterministic direct check."
                {check}
                '''
            ),
            encoding="utf-8",
        )
        return path

    def apply(self, path: Path) -> tuple[int, dict]:
        return self.invoke("apply", str(path))

    def test_list_returns_packaged_defaults(self) -> None:
        status, result = self.invoke("list")

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("ok", result["status"])
        self.assertEqual(
            {"agent-development", "omarchy-desktop"},
            {workload["id"] for workload in result["workloads"]},
        )
        self.assertTrue(
            all(workload["source"]["kind"] == "default" for workload in result["workloads"])
        )

    def test_query_returns_complete_contract(self) -> None:
        status, result = self.invoke("query", "agent-development")

        self.assertEqual(EXIT_OK, status)
        contract = result["workloads"][0]
        self.assertEqual("agent-development", contract["id"])
        self.assertEqual("1.0", contract["schema_version"])
        self.assertEqual({"critical", "useful"}, {item["importance"] for item in contract["capabilities"]})

    def test_common_options_work_after_the_subcommand(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(("list", "--config-dir", str(self.config_dir), "--pretty"))

        self.assertEqual(EXIT_OK, status)
        self.assertTrue(output.getvalue().startswith("{\n"))

    def test_assess_passes_a_direct_command_without_stated(self) -> None:
        contract = self.write_contract(
            "passing-test",
            check='type = "command"\ncommand = ["python3", "-c", "print(\'ready\')"]\nstdout = "nonempty"',
        )
        self.apply(contract)

        status, result = self.invoke("assess", "passing-test")

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("healthy", result["status"])
        self.assertEqual("direct_probes", result["machine_state"]["source"])
        self.assertEqual("not_used", result["machine_state"]["jiritsu_stated"])
        check = result["workloads"][0]["capabilities"][0]["checks"][0]
        self.assertEqual("pass", check["status"])
        self.assertEqual("ready", check["details"]["stdout"])

    def test_critical_failure_is_unhealthy(self) -> None:
        contract = self.write_contract(
            "critical-failure",
            check='type = "command_available"\ncommand = "command-that-does-not-exist-jiritsu"',
        )
        self.apply(contract)

        status, result = self.invoke("assess", "critical-failure")

        self.assertEqual(EXIT_UNHEALTHY, status)
        self.assertEqual("unhealthy", result["status"])
        self.assertEqual("fail", result["workloads"][0]["capabilities"][0]["status"])

    def test_useful_failure_is_degraded(self) -> None:
        contract = self.write_contract(
            "useful-failure",
            importance="useful",
            check='type = "path"\npath = "/path/that/does/not/exist"\nkind = "file"',
        )
        self.apply(contract)

        status, result = self.invoke("assess", "useful-failure")

        self.assertEqual(EXIT_DEGRADED, status)
        self.assertEqual("degraded", result["status"])

    def test_environment_check_does_not_expose_the_value(self) -> None:
        contract = self.write_contract(
            "environment-test",
            check='type = "environment"\nname = "JIRITSU_TEST_SECRET"\nnonempty = true',
        )
        self.apply(contract)

        with patch.dict(os.environ, {"JIRITSU_TEST_SECRET": "secret-value"}):
            status, result = self.invoke("assess", "environment-test")

        self.assertEqual(EXIT_OK, status)
        serialized = json.dumps(result)
        self.assertNotIn("secret-value", serialized)

    def test_invalid_contract_returns_a_structured_config_error(self) -> None:
        contract = self.write_contract(
            "invalid-test",
            check='type = "command_available"\ncommand = "git"',
            extra_root='surprise = "field"',
        )

        status, result = self.invoke("validate", str(contract))

        self.assertEqual(EXIT_CONFIG, status)
        self.assertEqual("error", result["status"])
        self.assertEqual("contract_invalid", result["errors"][0]["code"])
        self.assertEqual("surprise", result["errors"][0]["field"])

    def test_apply_creates_then_updates_a_user_contract(self) -> None:
        contract = self.write_contract(
            "managed-test",
            check='type = "command_available"\ncommand = "git"',
        )

        first_status, first = self.apply(contract)
        second_status, second = self.apply(contract)

        self.assertEqual(EXIT_OK, first_status)
        self.assertEqual("created", first["action"])
        self.assertEqual(EXIT_OK, second_status)
        self.assertEqual("updated", second["action"])
        self.assertTrue((self.config_dir / "managed-test.toml").is_file())

    def test_user_contract_overrides_a_default_with_the_same_id(self) -> None:
        contract = self.write_contract(
            "agent-development",
            check='type = "command_available"\ncommand = "git"',
        )
        self.apply(contract)

        status, result = self.invoke("query", "agent-development")

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("user", result["workloads"][0]["source"]["kind"])
        self.assertEqual(1, len(result["workloads"][0]["capabilities"]))

    def test_duplicate_user_contract_ids_are_a_config_error(self) -> None:
        first = self.write_contract(
            "duplicate-test",
            check='type = "command_available"\ncommand = "git"',
        )
        self.apply(first)
        duplicate = self.config_dir / "second-file.toml"
        duplicate.write_text(first.read_text(encoding="utf-8"), encoding="utf-8")

        status, result = self.invoke("list")

        self.assertEqual(EXIT_CONFIG, status)
        self.assertEqual("duplicate_workload", result["errors"][0]["code"])

    def test_unknown_selector_is_a_usage_error(self) -> None:
        status, result = self.invoke("query", "unknown-workload")

        self.assertEqual(EXIT_USAGE, status)
        self.assertEqual("unknown_workload", result["errors"][0]["code"])


if __name__ == "__main__":
    unittest.main()
