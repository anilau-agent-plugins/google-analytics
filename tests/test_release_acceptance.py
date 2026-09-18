from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from tests.verify_release_candidate import metadata_versions, verify_version


ROOT = Path(__file__).resolve().parents[1]


class ReleaseAcceptanceTests(unittest.TestCase):
    def test_release_data_quality_catalog_is_closed_and_synthetic(self) -> None:
        value = json.loads(
            (ROOT / "tests" / "fixtures" / "release-data-quality-scenarios.json").read_text(encoding="utf-8")
        )
        self.assertTrue(value["synthetic"])
        self.assertEqual(
            {item["state"] for item in value["scenarios"]},
            {"empty", "small", "incomplete", "hourly", "truncated", "conflicting", "ambiguous", "malformed"},
        )
        self.assertEqual(len(value["scenarios"]), 8)
        self.assertEqual(len({item["id"] for item in value["scenarios"]}), 8)
        self.assertEqual(
            value["invariants"],
            {
                "mutationPerformed": False,
                "productionNetwork": False,
                "containsCustomerData": False,
                "containsCredentials": False,
            },
        )
        raw = json.dumps(value, sort_keys=True)
        for forbidden in ("access_token", "refresh_token", "client_secret", "authorization"):
            self.assertNotIn(forbidden, raw.lower())

    def test_release_metadata_has_one_exact_version(self) -> None:
        self.assertEqual(set(metadata_versions().values()), {"0.20.0"})
        verify_version("0.20.0")
        with self.assertRaises(ValueError):
            verify_version("0.20")

    def test_release_workflow_is_manual_read_only_and_exact(self) -> None:
        path = ROOT / ".github" / "workflows" / "release-acceptance.yml"
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        on_index = lines.index("on:")
        trigger_lines: list[str] = []
        for line in lines[on_index + 1:]:
            if line and not line.startswith(" "):
                break
            if re.match(r"^  [a-zA-Z_][a-zA-Z0-9_-]*:\s*$", line):
                trigger_lines.append(line.strip()[:-1])
        self.assertEqual(trigger_lines, ["workflow_dispatch"])
        for forbidden in ("push:", "pull_request:", "release:", "schedule:", "workflow_run:", "repository_dispatch:"):
            self.assertNotIn(f"  {forbidden}", text)
        self.assertRegex(text, r"(?m)^permissions:\n  contents: read$")
        self.assertNotIn("${{ secrets.", text)
        self.assertNotIn("contents: write", text)
        self.assertNotIn("id-token:", text)
        self.assertIn("release_version:", text)
        self.assertIn("release_commit:", text)
        self.assertIn("ref: ${{ inputs.release_commit }}", text)
        self.assertIn("persist-credentials: false", text)
        for action in re.findall(r"uses:\s*([^\s]+)", text):
            self.assertRegex(action, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")
        for runner in ("windows-2025", "ubuntu-24.04", "macos-15-intel"):
            self.assertIn(f"- {runner}", text)
        for version in ("3.10", "3.11", "3.12", "3.13"):
            self.assertIn(f'- "{version}"', text)
        windows_runner = (ROOT / "tests" / "run_python_limited.ps1").read_text(encoding="utf-8")
        self.assertLess(windows_runner.index('@{ File = "python"'), windows_runner.index('@{ File = "py"'))
        for forbidden_command in ("gh release", "git push", "git tag", "curl ", "Invoke-WebRequest"):
            self.assertNotIn(forbidden_command, text)


if __name__ == "__main__":
    unittest.main()
