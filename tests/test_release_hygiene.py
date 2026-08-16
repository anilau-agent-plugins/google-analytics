from __future__ import annotations

import json
import re
import subprocess
import unittest
from pathlib import Path

from scripts.google_analytics_cli import __version__


ROOT = Path(__file__).resolve().parents[1]


class ReleaseHygieneTests(unittest.TestCase):
    def test_required_product_files(self) -> None:
        required = (
            ".codex-plugin/plugin.json",
            ".claude-plugin/plugin.json",
            "skills/google-analytics/SKILL.md",
            "skills/google-analytics/agents/openai.yaml",
            "skills/google-analytics/references/ga4-configuration.md",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "PRIVACY.md",
            "SUPPORT.md",
        )
        for relative in required:
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())

    def test_versions_and_repository_match(self) -> None:
        codex = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        claude = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(codex["version"].split("+", 1)[0], __version__)
        self.assertEqual(claude["version"], __version__)
        expected = "https://github.com/anilau-agent-plugins/google-analytics"
        self.assertEqual(codex["repository"], expected)
        self.assertEqual(claude["repository"], expected)

    def test_skill_frontmatter_is_minimal(self) -> None:
        text = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text.split("---", 2)[1]
        keys = [line.split(":", 1)[0] for line in frontmatter.splitlines() if ":" in line]
        self.assertEqual(keys, ["name", "description"])

    def test_oauth_onboarding_is_agent_led_and_uses_the_correct_client_type(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        setup = (
            ROOT / "skills" / "google-analytics" / "references" / "google-cloud-oauth-setup.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Never ask the user to perform a", skill)
        self.assertIn("step that the available tools can complete safely", skill)
        self.assertIn("gcloud services enable", setup)
        self.assertIn("Do not use `gcloud iam oauth-clients create`", setup)
        self.assertIn("Never list or scan Downloads", setup)
        self.assertIn("Desktop app", setup)

    def test_oauth_onboarding_requires_an_explicit_two_mode_choice(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        setup = (
            ROOT / "skills" / "google-analytics" / "references" / "google-cloud-oauth-setup.md"
        ).read_text(encoding="utf-8")
        for phrase in (
            "Detailed self-service",
            "Browser-assisted",
            "request explicit permission to control a browser",
            "already signed in",
            "Never ask for or enter a password",
            "Let the user switch modes at any point",
        ):
            self.assertIn(phrase, skill)
        self.assertIn("Do not silently choose browser control", setup)
        self.assertIn("https://console.cloud.google.com/projectcreate", setup)
        self.assertIn("https://console.cloud.google.com/auth/clients?project=<PROJECT_ID>", setup)

    def test_ga4_mutations_require_a_hash_and_forbid_automatic_retry(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        reference = (
            ROOT / "skills" / "google-analytics" / "references" / "ga4-configuration.md"
        ).read_text(encoding="utf-8")
        registry = (
            ROOT / "scripts" / "google_analytics_cli" / "admin_mutation_registry.py"
        ).read_text(encoding="utf-8")
        self.assertIn("full `planSha256`", skill)
        self.assertIn("do not retry", reference)
        self.assertNotIn('"DELETE"', registry)
        self.assertNotIn("delete.containers", registry)

    def test_working_material_and_local_paths_are_not_publishable(self) -> None:
        forbidden_names = {"planning", ".plugin-work", "DEVELOPMENT_PLAN.md"}
        for path in ROOT.rglob("*"):
            relative = path.relative_to(ROOT)
            self.assertFalse(any(part in forbidden_names for part in relative.parts), str(relative))
            if path.is_file() and path.suffix.lower() in {".md", ".json", ".yaml", ".yml", ".py", ".ps1", ".sh"}:
                text = path.read_text(encoding="utf-8", errors="replace")
                windows_workspace = re.compile(re.escape("C:" + "\\dev\\tools"), re.I)
                portable_workspace = re.compile(re.escape("C:" + "/dev/tools"), re.I)
                self.assertIsNone(windows_workspace.search(text), str(relative))
                self.assertIsNone(portable_workspace.search(text), str(relative))

    def test_release_tree_has_no_secrets_or_runtime_artifacts(self) -> None:
        forbidden_parts = {"__pycache__", ".pytest_cache", ".venv", "venv", ".google-analytics-advisor"}
        forbidden_suffixes = {".pyc", ".pyo", ".token", ".credentials"}
        for path in ROOT.rglob("*"):
            relative = path.relative_to(ROOT)
            if any(part == ".git" for part in relative.parts):
                continue
            forbidden = any(part in forbidden_parts for part in relative.parts) or path.suffix.lower() in forbidden_suffixes
            if forbidden:
                # Claude Code may compile an installed cache before tests start. Source-release
                # hygiene is enforced in the canonical Git checkout; installed copies have no Git
                # metadata and may contain host-generated bytecode without it being publishable.
                if not (ROOT / ".git").exists():
                    continue
                ignored = subprocess.run(
                    ["git", "check-ignore", "--quiet", str(relative)], cwd=ROOT, check=False
                )
                self.assertEqual(ignored.returncode, 0, f"runtime artifact is not ignored: {relative}")


if __name__ == "__main__":
    unittest.main()
