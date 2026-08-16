from __future__ import annotations

import json
import os
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "google_analytics.py"


class CliTests(unittest.TestCase):
    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> tuple[int, dict]:
        result = subprocess.run(
            [sys.executable, "-I", "-B", "-X", "utf8", str(ENTRY), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
            env=env,
        )
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_version_envelope(self) -> None:
        code, payload = self.run_cli("version", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["cliVersion"], "0.7.0")

    def test_version_check_is_offline_without_configuration(self) -> None:
        code, payload = self.run_cli("version", "--check", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "not_configured")
        self.assertFalse(payload["data"]["networkUsed"])

    def test_invalid_arguments_are_json(self) -> None:
        code, payload = self.run_cli("unknown")
        self.assertEqual(code, 4)
        self.assertEqual(payload["errors"][0]["code"], "INVALID_ARGUMENTS")

    def test_consent_preview_contains_exact_complete_scope_set(self) -> None:
        code, payload = self.run_cli("auth", "consent-preview", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "authorization_required")
        self.assertEqual(
            payload["data"]["scopes"],
            [
                "openid",
                "email",
                "https://www.googleapis.com/auth/analytics.readonly",
                "https://www.googleapis.com/auth/analytics.edit",
                "https://www.googleapis.com/auth/tagmanager.readonly",
                "https://www.googleapis.com/auth/tagmanager.edit.containers",
                "https://www.googleapis.com/auth/tagmanager.edit.containerversions",
                "https://www.googleapis.com/auth/tagmanager.publish",
            ],
        )
        self.assertFalse(payload["data"]["mutationApprovalGranted"])

    def test_profile_list_does_not_require_secret_store_access(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ)
            env["GOOGLE_ANALYTICS_ADVISOR_HOME"] = temp
            code, payload = self.run_cli("auth", "profiles", "list", "--json", env=env)
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"], {"activeProfileId": None, "profiles": []})

    def test_site_inspect_is_local_and_baseline_requires_selection_before_auth(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "index.html").write_text("G-TEST123", encoding="utf-8")
            code, payload = self.run_cli("site", "inspect", "--project-root", str(root), "--json")
            self.assertEqual(code, 0)
            self.assertFalse(payload["data"]["networkUsed"])
            env = dict(os.environ)
            env["GOOGLE_ANALYTICS_ADVISOR_HOME"] = str(root / "runtime")
            code, payload = self.run_cli("audit", "baseline", "--profile", "profile-fixture", "--project-root", str(root), "--json", env=env)
            self.assertEqual(code, 4)
            self.assertEqual(payload["errors"][0]["code"], "SELECTION_REQUIRED")

    def test_measurement_context_and_blocked_draft_are_local_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            code, context = self.run_cli(
                "measurement", "context", "--project-root", str(root), "--profile", "profile-test",
                "--without-baseline", "--json",
            )
            self.assertEqual(code, 0)
            self.assertEqual(context["status"], "action_required")
            self.assertFalse(context["data"]["mutationPerformed"])
            code, draft = self.run_cli(
                "measurement", "draft", "--context", context["data"]["artifact"]["path"],
                "--output-dir", str(root / ".google-analytics-advisor"), "--json",
            )
            self.assertEqual(code, 0)
            self.assertEqual(draft["status"], "blocked")
            self.assertFalse(draft["data"]["mutationPerformed"])

    @unittest.skipUnless(os.name == "nt", "CLI DPAPI import is Windows-only")
    def test_client_import_uses_protected_storage_and_does_not_echo_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            client_file = root / "desktop-client.json"
            secret = "unit-test-client-secret"
            client_file.write_text(
                json.dumps({"installed": {
                    "client_id": "123456.apps.googleusercontent.com",
                    "client_secret": secret,
                    "project_id": "unit-test-project",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }}),
                encoding="utf-8",
            )
            runtime_home = root / "runtime"
            env = dict(os.environ)
            env["GOOGLE_ANALYTICS_ADVISOR_HOME"] = str(runtime_home)
            code, payload = self.run_cli(
                "auth", "client", "import", "--file", str(client_file.resolve()), "--json", env=env
            )
            protected = next((runtime_home / "state" / "secrets").glob("*.dpapi")).read_bytes()
        self.assertEqual(code, 0)
        rendered = json.dumps(payload)
        self.assertNotIn(secret, rendered)
        self.assertNotIn(secret.encode("utf-8"), protected)
        self.assertFalse(payload["data"]["sourceDeleted"])


if __name__ == "__main__":
    unittest.main()
