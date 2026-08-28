from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SecurityAcceptanceTests(unittest.TestCase):
    def _release_files(self) -> list[Path]:
        if (ROOT / ".git").exists():
            result = subprocess.run(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                cwd=ROOT, capture_output=True, check=True,
            )
            values = [item for item in result.stdout.decode("utf-8").split("\0") if item]
            return [ROOT / item for item in values if (ROOT / item).is_file()]
        forbidden_parts = {".git", "__pycache__", ".pytest_cache", ".venv", "venv", ".google-analytics-advisor"}
        forbidden_suffixes = {".pyc", ".pyo", ".tmp", ".bak"}
        return [
            path for path in ROOT.rglob("*")
            if path.is_file()
            and not any(part in forbidden_parts for part in path.relative_to(ROOT).parts)
            and path.suffix.lower() not in forbidden_suffixes
        ]

    def test_canonical_runner_enforces_loopback_only_for_child_processes(self) -> None:
        script = (
            "from scripts.google_analytics_cli.http import JsonTransport; "
            "from scripts.google_analytics_cli.errors import AdvisorError; "
            "\ntry: JsonTransport(timeout=.01).request('GET','https://analyticsdata.googleapis.com/',max_attempts=1)"
            "\nexcept AdvisorError as exc: raise SystemExit(0 if exc.code == 'TEST_NETWORK_BLOCKED' else 2)"
            "\nraise SystemExit(3)"
        )
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))

    def test_direct_stdlib_network_is_blocked_before_dns(self) -> None:
        with self.assertRaises(OSError) as caught:
            urllib.request.urlopen("https://example.invalid/stage11-canary", timeout=0.01)
        self.assertIn("blocked non-loopback network host", str(caught.exception))

    def test_release_candidate_has_only_expected_top_level_entries(self) -> None:
        allowed = {
            ".agents", ".claude-plugin", ".codex-plugin", ".github", ".gitignore", "CHANGELOG.md",
            "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "LICENSE", "PRIVACY.md", "README.md",
            "SECURITY.md", "SUPPORT.md", "contracts", "docs", "scripts", "skills", "tests",
        }
        unexpected = sorted({path.relative_to(ROOT).parts[0] for path in self._release_files()} - allowed)
        self.assertEqual(unexpected, [])

    def test_release_candidate_archive_excludes_runtime_and_working_material(self) -> None:
        forbidden_parts = {".git", ".plugin-work", ".google-analytics-advisor", "__pycache__", ".venv", "planning"}
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "candidate.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for path in self._release_files():
                    relative = path.relative_to(ROOT)
                    output.write(path, relative.as_posix())
            with zipfile.ZipFile(archive) as source:
                names = source.namelist()
                self.assertTrue(names)
                for name in names:
                    self.assertFalse(any(part in forbidden_parts for part in Path(name).parts), name)

    def test_release_candidate_contains_no_plausible_live_credentials(self) -> None:
        patterns = (
            re.compile(rb"ya29\.[A-Za-z0-9._-]{20,}"),
            re.compile(rb"1//(?!test|unit-test)[A-Za-z0-9._-]{20,}"),
            re.compile(rb"GOCSPX-(?!test)[A-Za-z0-9_-]{20,}"),
            re.compile(rb"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
        )
        for path in self._release_files():
            if path.suffix.lower() not in {".json", ".md", ".py", ".ps1", ".sh", ".yaml", ".yml"}:
                continue
            raw = path.read_bytes()
            for pattern in patterns:
                with self.subTest(path=str(path.relative_to(ROOT)), pattern=pattern.pattern):
                    self.assertIsNone(pattern.search(raw))
            if path.suffix.lower() == ".json" and "fixtures" not in path.parts and "tests" not in path.parts:
                try:
                    value = json.loads(raw.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                self.assertFalse(isinstance(value, dict) and "installed" in value and "client_secret" in value.get("installed", {}))

    def test_release_records_local_validation_exception_and_has_no_github_workflow(self) -> None:
        workflow_root = ROOT / ".github" / "workflows"
        workflows = [] if not workflow_root.exists() else [
            path for path in workflow_root.iterdir() if path.suffix.lower() in {".yml", ".yaml"}
        ]
        self.assertEqual(workflows, [])
        decision = (
            ROOT / "docs" / "decisions" / "0001-release-validation-without-github-ci.md"
        ).read_text(encoding="utf-8")
        for phrase in ("Version 0.11.0", "without GitHub Actions", "Windows", "0.20.0"):
            self.assertIn(phrase, decision)
        self.assertEqual(os.environ.get("GOOGLE_ANALYTICS_ADVISOR_NETWORK_POLICY"), "loopback-only")


if __name__ == "__main__":
    unittest.main()
