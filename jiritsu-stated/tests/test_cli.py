from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MODULE_ROOT / "src"))

from jiritsu_stated.cli import (  # noqa: E402
    EXIT_DATA,
    EXIT_ERROR,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_USAGE,
    main,
)


FIXTURE = MODULE_ROOT / "tests" / "fixtures" / "healthy.json"


class CliTests(unittest.TestCase):
    def invoke(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with redirect_stdout(output):
            status = main(arguments)
        return status, json.loads(output.getvalue())

    def load_fixture(self) -> dict:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def write_fixture(self, payload: dict) -> str:
        fixture_file = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        )
        with fixture_file:
            json.dump(payload, fixture_file)
        self.addCleanup(Path(fixture_file.name).unlink, missing_ok=True)
        return fixture_file.name

    def test_full_fixture_query_returns_every_fact(self) -> None:
        status, result = self.invoke("query", "--fixture", str(FIXTURE))

        self.assertEqual(EXIT_OK, status)
        self.assertEqual("ok", result["status"])
        self.assertEqual(13, len(result["facts"]))
        self.assertEqual("test-machine", result["facts"]["system.hostname"]["value"])
        self.assertEqual(3, result["facts"]["packages.installed"]["value"]["count"])
        self.assertEqual(
            8, result["facts"]["hardware.cpu"]["value"]["logical_cpu_count"]
        )
        self.assertEqual(
            "wlan0", result["facts"]["networks.active"]["value"]["interface"]
        )
        self.assertEqual(
            [{"name": "root", "subvolume": "/"}],
            result["facts"]["snapshots.configurations"]["value"],
        )
        self.assertEqual(
            42,
            result["facts"]["snapshots.active_root"]["value"]["snapper_snapshot_id"],
        )
        collected_at = datetime.fromisoformat(
            result["collected_at"].replace("Z", "+00:00")
        )
        for fact in result["facts"].values():
            self.assertTrue(fact["fixture"])
            self.assertIn("source", fact)
            self.assertIn("observed_at", fact)
            self.assertGreaterEqual(fact["age_seconds"], 0)
            observed_at = datetime.fromisoformat(
                fact["observed_at"].replace("Z", "+00:00")
            )
            self.assertLessEqual(observed_at, collected_at)

    def test_category_selector_only_returns_category(self) -> None:
        status, result = self.invoke("query", "system", "--fixture", str(FIXTURE))

        self.assertEqual(EXIT_OK, status)
        self.assertEqual(
            {
                "system.hostname",
                "system.os",
                "system.kernel",
                "system.omarchy.version",
            },
            set(result["facts"]),
        )

    def test_missing_fixture_source_returns_partial_result(self) -> None:
        payload = self.load_fixture()
        del payload["sources"]["system.uname"]
        path = self.write_fixture(payload)

        status, result = self.invoke("query", "system", "--fixture", path)

        self.assertEqual(EXIT_PARTIAL, status)
        self.assertEqual("partial", result["status"])
        self.assertEqual(3, len(result["facts"]))
        self.assertEqual("fixture_source_missing", result["errors"][0]["code"])
        self.assertEqual("system.kernel", result["errors"][0]["fact_id"])

    def test_failed_source_returns_a_structured_error(self) -> None:
        payload = self.load_fixture()
        payload["sources"]["services.system_state"].update(
            {"exit_code": 1, "stderr": "manager unavailable"}
        )
        path = self.write_fixture(payload)

        status, result = self.invoke("query", "services", "--fixture", path)

        self.assertEqual(EXIT_PARTIAL, status)
        self.assertEqual("source_failed", result["errors"][0]["code"])
        self.assertIn("manager unavailable", result["errors"][0]["message"])
        self.assertIn("services.running", result["facts"])

    def test_failed_single_fact_returns_error_status(self) -> None:
        payload = self.load_fixture()
        payload["sources"]["services.system_state"].update(
            {"exit_code": 1, "stderr": "manager unavailable"}
        )
        path = self.write_fixture(payload)

        status, result = self.invoke(
            "query", "services.system_state", "--fixture", path
        )

        self.assertEqual(EXIT_ERROR, status)
        self.assertEqual("error", result["status"])
        self.assertEqual({}, result["facts"])
        self.assertEqual("source_failed", result["errors"][0]["code"])

    def test_malformed_source_payload_returns_parse_error(self) -> None:
        payload = self.load_fixture()
        payload["sources"]["hardware.lscpu"]["stdout"] = "not-json"
        path = self.write_fixture(payload)

        status, result = self.invoke("query", "hardware", "--fixture", path)

        self.assertEqual(EXIT_PARTIAL, status)
        self.assertEqual("parse_error", result["errors"][0]["code"])
        self.assertIn("hardware.memory", result["facts"])

    def test_unknown_selector_is_a_request_error(self) -> None:
        status, result = self.invoke("query", "not-a-fact", "--fixture", str(FIXTURE))

        self.assertEqual(EXIT_USAGE, status)
        self.assertEqual("error", result["status"])
        self.assertEqual("unknown_selector", result["errors"][0]["code"])

    def test_nonpositive_timeout_is_a_request_error(self) -> None:
        status, result = self.invoke("query", "system", "--timeout", "0")

        self.assertEqual(EXIT_USAGE, status)
        self.assertEqual("invalid_request", result["errors"][0]["code"])

    def test_invalid_fixture_is_a_data_error(self) -> None:
        path = self.write_fixture({"schema_version": "2.0", "sources": {}})

        status, result = self.invoke("query", "system", "--fixture", path)

        self.assertEqual(EXIT_DATA, status)
        self.assertEqual("fixture_invalid", result["errors"][0]["code"])

    def test_catalog_does_not_collect_sources(self) -> None:
        status, result = self.invoke("catalog")

        self.assertEqual(EXIT_OK, status)
        self.assertEqual(13, len(result["facts"]))
        self.assertEqual("system.hostname", result["facts"][0]["id"])


if __name__ == "__main__":
    unittest.main()
