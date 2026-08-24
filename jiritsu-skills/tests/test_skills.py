from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


MODULE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = MODULE_ROOT / "skills"
INSTALLER = MODULE_ROOT / "bin" / "jiritsu-skills-install"
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCAL_LINK_PATTERN = re.compile(r"]\((?!https?://)([^)#]+)(?:#[^)]+)?\)")


def skill_files() -> list[Path]:
    return sorted(SKILLS_ROOT.glob("*/SKILL.md"))


def frontmatter(path: Path) -> dict[str, str]:
    content = path.read_text(encoding="utf-8")
    parts = content.split("---", 2)
    if len(parts) != 3 or parts[0] != "":
        raise AssertionError(f"{path} has invalid frontmatter delimiters")

    values: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            raise AssertionError(f"{path} has unsupported multiline frontmatter")
        values[key.strip()] = value.strip()
    return values


def prose_without_code(content: str) -> str:
    chunks = content.split("```")
    return "\n".join(chunks[::2])


def fenced_blocks(content: str, language: str) -> list[str]:
    pattern = re.compile(rf"```{language}\n(.*?)\n```", re.DOTALL)
    return pattern.findall(content)


class SkillStructureTests(unittest.TestCase):
    def test_expected_skills_exist(self) -> None:
        self.assertEqual(
            [path.parent.name for path in skill_files()],
            [
                "jiritsu-broker-admin",
                "jiritsu-change",
                "jiritsu-observe",
                "jiritsu-recover",
                "jiritsu-workloads",
            ],
        )

    def test_metadata_is_discoverable(self) -> None:
        for path in skill_files():
            with self.subTest(skill=path.parent.name):
                metadata = frontmatter(path)
                self.assertEqual(metadata.get("name"), path.parent.name)
                self.assertRegex(metadata["name"], NAME_PATTERN)
                description = metadata.get("description", "")
                self.assertGreater(len(description), 0)
                self.assertLessEqual(len(description), 1024)
                self.assertIn("Use for", description)
                self.assertIn("Do not use", description)

    def test_local_markdown_links_exist(self) -> None:
        for path in sorted(SKILLS_ROOT.glob("**/*.md")):
            content = path.read_text(encoding="utf-8")
            for target in LOCAL_LINK_PATTERN.findall(content):
                with self.subTest(skill=path.parent.name, target=target):
                    self.assertTrue((path.parent / target).is_file())

    def test_skill_directories_have_portable_structure(self) -> None:
        allowed = {"SKILL.md", "agents", "assets", "references", "scripts"}
        for path in skill_files():
            with self.subTest(skill=path.parent.name):
                entries = {entry.name for entry in path.parent.iterdir()}
                self.assertFalse(entries - allowed)
                self.assertNotIn("README.md", entries)
                self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 500)

                for resource_name in {"agents", "assets", "references", "scripts"}:
                    resource = path.parent / resource_name
                    if resource.exists():
                        self.assertFalse(
                            [entry for entry in resource.rglob("*") if entry.is_dir()]
                        )

    def test_embedded_data_templates_parse(self) -> None:
        for path in sorted(SKILLS_ROOT.glob("**/*.md")):
            content = path.read_text(encoding="utf-8")
            for block in fenced_blocks(content, "json"):
                with self.subTest(path=path, language="json"):
                    json.loads(block)
            for block in fenced_blocks(content, "toml"):
                with self.subTest(path=path, language="toml"):
                    tomllib.loads(block)

    def test_instructions_avoid_ambiguous_modals_and_placeholders(self) -> None:
        pattern = re.compile(r"\b(?:should|would|may|might|could|shall)\b", re.IGNORECASE)
        for path in skill_files():
            prose = prose_without_code(path.read_text(encoding="utf-8"))
            with self.subTest(skill=path.parent.name):
                self.assertNotRegex(prose, pattern)
                self.assertNotIn("TODO", prose)

    def test_installer_dry_run_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "skills"
            result = subprocess.run(
                [INSTALLER, "--dry-run", "--target", target],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("plan: link", result.stdout)
            self.assertFalse(target.exists())

    def test_installer_links_skills_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "skills"
            command = [INSTALLER, "--target", target]
            subprocess.run(command, check=True, capture_output=True, text=True)
            subprocess.run(command, check=True, capture_output=True, text=True)

            for skill_file in skill_files():
                installed = target / skill_file.parent.name
                self.assertTrue(installed.is_symlink())
                self.assertEqual(installed.resolve(), skill_file.parent.resolve())

    def test_installer_stops_before_a_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            target = Path(temporary_directory) / "skills"
            target.mkdir()
            conflict = target / "jiritsu-observe"
            conflict.write_text("keep", encoding="utf-8")

            result = subprocess.run(
                [INSTALLER, "--target", target],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(conflict.read_text(encoding="utf-8"), "keep")
            self.assertEqual(list(target.iterdir()), [conflict])

    def test_installer_is_executable(self) -> None:
        self.assertTrue(os.access(INSTALLER, os.X_OK))


if __name__ == "__main__":
    unittest.main()
