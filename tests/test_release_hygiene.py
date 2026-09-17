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
            ".agents/plugins/marketplace.json",
            ".claude-plugin/marketplace.json",
            "skills/google-analytics/SKILL.md",
            "skills/google-analytics/agents/openai.yaml",
            "skills/google-analytics/references/ga4-configuration.md",
            "skills/google-analytics/references/gtm-management.md",
            "skills/google-analytics/references/proactive-advisor-playbook.md",
            "skills/google-analytics/references/reporting-advisor.md",
            "skills/google-analytics/references/search-console-indexing.md",
            "skills/google-analytics/references/search-console-linking.md",
            "README.md",
            "CHANGELOG.md",
            "LICENSE",
            "PRIVACY.md",
            "SUPPORT.md",
            "SECURITY.md",
            "CONTRIBUTING.md",
            "CODE_OF_CONDUCT.md",
            "docs/decisions/0001-release-validation-without-github-ci.md",
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
        self.assertEqual(codex["version"], __version__)
        self.assertEqual(codex["license"], "MIT")
        self.assertEqual(claude["license"], "MIT")
        codex_marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        claude_marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(codex_marketplace["name"], "anilau-google-analytics")
        self.assertEqual(codex_marketplace["plugins"][0]["name"], "google-analytics")
        self.assertEqual(codex_marketplace["plugins"][0]["source"]["path"], "./")
        self.assertEqual(claude_marketplace["name"], "anilau-google-analytics")
        self.assertEqual(claude_marketplace["plugins"][0]["name"], "google-analytics")
        self.assertEqual(claude_marketplace["plugins"][0]["version"], __version__)
        self.assertEqual(claude_marketplace["plugins"][0]["source"], "./")

    def test_public_documentation_matches_open_source_release(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        support = (ROOT / "SUPPORT.md").read_text(encoding="utf-8")
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("free, open-source", readme)
        self.assertIn("How to install", readme)
        self.assertIn("How updates work", readme)
        self.assertIn("Uninstall and rollback", readme)
        self.assertIn("GitHub Issues", support)
        self.assertTrue(license_text.startswith("MIT License"))
        for phrase in ("LicenseRef-Anilau-Commercial", "not yet available for commercial"):
            self.assertNotIn(phrase, readme)
            self.assertNotIn(phrase, support)

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

    def test_search_console_capability_is_read_only_and_exactly_bounded(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        setup = (
            ROOT / "skills" / "google-analytics" / "references" / "google-cloud-oauth-setup.md"
        ).read_text(encoding="utf-8")
        discovery = (
            ROOT / "skills" / "google-analytics" / "references" / "search-console-discovery.md"
        ).read_text(encoding="utf-8")
        oauth = (ROOT / "scripts" / "google_analytics_cli" / "oauth.py").read_text(encoding="utf-8")
        registry = (ROOT / "scripts" / "google_analytics_cli" / "read_operation.py").read_text(encoding="utf-8")
        self.assertIn("auth upgrade --profile <id> --json", skill)
        self.assertIn("search-console sites list --profile <profile-id> --json", discovery)
        self.assertIn("searchconsole.googleapis.com", setup)
        self.assertIn('"https://www.googleapis.com/auth/webmasters.readonly"', oauth)
        self.assertNotIn('"https://www.googleapis.com/auth/webmasters",', oauth)
        self.assertIn('"searchconsole.sites.list", "GET"', registry)
        self.assertNotIn('"searchconsole.sites.add"', registry)
        self.assertNotIn('"searchconsole.sites.delete"', registry)

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

    def test_gtm_lifecycle_is_separate_and_never_automatic(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "skills" / "google-analytics" / "references" / "gtm-management.md").read_text(encoding="utf-8")
        service = (ROOT / "scripts" / "google_analytics_cli" / "gtm_mutation_service.py").read_text(encoding="utf-8")
        for stage in ("WORKSPACE_CREATE", "WORKSPACE_SYNC", "ENTITY_BULK_UPDATE", "QUICK_PREVIEW", "VERSION_CREATE", "PUBLISH"):
            self.assertIn(stage, reference)
            self.assertIn(stage, service)
        self.assertIn("Never resolve conflicts", skill)
        self.assertIn("publish automatically", skill)
        self.assertNotIn('"DELETE"', service)
        self.assertNotIn("resolve_conflict", service)
        self.assertIn('"automaticPublish": False', service)

    def test_reporting_advisor_is_bounded_and_read_only(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "skills" / "google-analytics" / "references" / "reporting-advisor.md").read_text(encoding="utf-8")
        registry = (ROOT / "scripts" / "google_analytics_cli" / "read_operation.py").read_text(encoding="utf-8")
        service = (ROOT / "scripts" / "google_analytics_cli" / "report_service.py").read_text(encoding="utf-8")
        self.assertIn("Reporting advisor workflow", skill)
        self.assertIn("restricted metric is unavailable, not zero", reference)
        for operation in ("data.report.run", "data.report.realtime", "data.report.funnel"):
            self.assertIn(operation, registry)

    def test_search_console_reporting_is_bounded_read_only_and_separate(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "skills" / "google-analytics" / "references" / "search-console-performance.md").read_text(encoding="utf-8")
        registry = (ROOT / "scripts" / "google_analytics_cli" / "read_operation.py").read_text(encoding="utf-8")
        service = (ROOT / "scripts" / "google_analytics_cli" / "search_console_report_service.py").read_text(encoding="utf-8")
        self.assertIn("Search Console performance workflow", skill)
        self.assertIn("20 performance requests and 12,000 rows", reference)
        self.assertIn("searchconsole.searchanalytics.query", registry)
        self.assertIn("max_attempts=1", registry)
        self.assertIn("MAX_REQUESTS = 20", service)
        self.assertIn("MAX_ROWS = 12_000", service)
        self.assertNotIn('"PATCH"', service)
        self.assertNotIn('"DELETE"', service)
        for method in ('method="PATCH"', 'method="PUT"', 'method="DELETE"'):
            self.assertNotIn(method, service)

    def test_search_console_indexing_is_bounded_read_only_and_single_use(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "skills" / "google-analytics" / "references" / "search-console-indexing.md").read_text(encoding="utf-8")
        registry = (ROOT / "scripts" / "google_analytics_cli" / "read_operation.py").read_text(encoding="utf-8")
        service = (ROOT / "scripts" / "google_analytics_cli" / "search_console_indexing_service.py").read_text(encoding="utf-8")
        self.assertIn("Search Console sitemap and URL Inspection workflow", skill)
        self.assertIn("not a live fetch", reference)
        for operation in (
            "searchconsole.sitemaps.list", "searchconsole.sitemaps.get",
            "searchconsole.urlinspection.inspect",
        ):
            self.assertIn(operation, registry)
        for forbidden in ("searchconsole.sitemaps.submit", "searchconsole.sitemaps.delete", "indexing.googleapis.com"):
            self.assertNotIn(forbidden, registry)
        self.assertIn("MAX_INSPECTION_URLS = 10", service)
        self.assertIn("DEFAULT_INSPECTION_URLS = 5", service)
        self.assertIn("plan_was_consumed", service)
        self.assertIn('"automaticRetries": 0', service)
        for method in ('method="PATCH"', 'method="PUT"', 'method="DELETE"'):
            self.assertNotIn(method, service)

    def test_search_console_linking_is_ui_guarded_and_never_auto_replaces(self) -> None:
        skill = (ROOT / "skills" / "google-analytics" / "SKILL.md").read_text(encoding="utf-8")
        reference = (ROOT / "skills" / "google-analytics" / "references" / "search-console-linking.md").read_text(encoding="utf-8")
        service = (ROOT / "scripts" / "google_analytics_cli" / "search_console_link_service.py").read_text(encoding="utf-8")
        for phrase in (
            "Detailed self-service", "Browser-assisted", "fresh explicit permission",
            "full `planSha256`", "Do not retry a click", "unpublished in GA4 navigation by default",
        ):
            self.assertIn(phrase, reference)
        self.assertIn("Search Console and GA4 linking workflow", skill)
        self.assertIn("prior OAuth setup permission never carries over", skill)
        self.assertIn("PLAN_TTL = timedelta(minutes=30)", service)
        self.assertIn('"UI_SUBMIT_CREATE_LINK"', service)
        for forbidden in ('method="POST"', 'method="PATCH"', 'method="PUT"', 'method="DELETE"'):
            self.assertNotIn(forbidden, service)
        self.assertNotIn("analyticsadmin.googleapis.com", service)

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
