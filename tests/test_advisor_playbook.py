from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.google_analytics_cli.oauth import BASE_SCOPES, SEARCH_CONSOLE_SCOPES, TARGET_SCOPES


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "advisor-playbook-scenarios.json"
SKILL = ROOT / "skills" / "google-analytics" / "SKILL.md"
PLAYBOOK = ROOT / "skills" / "google-analytics" / "references" / "proactive-advisor-playbook.md"


class AdvisorPlaybookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalog = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.scenarios = cls.catalog["scenarios"]

    def test_catalog_covers_the_approved_realistic_scenarios(self) -> None:
        expected = {
            "broad-site-health",
            "broad-fewer-customers",
            "targeted-yesterday-purchases",
            "targeted-realtime-purchase",
            "ambiguous-duplicate-property-names",
            "broad-no-baseline",
            "broad-no-measurement-plan",
            "broad-ecommerce-not-applicable",
            "broad-duplicate-tag",
            "broad-low-quota",
            "broad-unreliable-data",
            "broad-analysis-plus-fix",
            "broad-no-search-console",
            "targeted-follow-up-after-broad",
        }
        self.assertEqual({item["id"] for item in self.scenarios}, expected)

    def test_catalog_uses_closed_modes_states_and_domain_sets(self) -> None:
        self.assertEqual(set(self.catalog["modes"]), {"broad", "targeted", "ambiguous"})
        self.assertEqual(
            set(self.catalog["completenessStates"]),
            {"checked", "not_applicable", "unavailable", "blocked", "stale", "not_checked"},
        )
        domain_sets = self.catalog["domainSets"]
        full = set(domain_sets["full-ga4"])
        self.assertEqual(len(full), 12)
        self.assertIn("data-quality-and-quota", full)
        self.assertIn("unanswered-business-questions", full)
        for scenario in self.scenarios:
            self.assertIn(scenario["expectedMode"], self.catalog["modes"])
            selected = scenario["requiredDomainSet"]
            if selected is not None:
                self.assertIn(selected, domain_sets)
            self.assertTrue(scenario["allowedReads"])
            self.assertTrue(scenario["mustDemonstrate"])

    def test_every_broad_scenario_requires_the_full_ga4_matrix(self) -> None:
        broad = [item for item in self.scenarios if item["expectedMode"] == "broad"]
        self.assertGreaterEqual(len(broad), 9)
        self.assertTrue(all(item["requiredDomainSet"] == "full-ga4" for item in broad))

    def test_targeted_and_ambiguous_scenarios_do_not_request_the_full_suite(self) -> None:
        targeted = [item for item in self.scenarios if item["expectedMode"] == "targeted"]
        self.assertTrue(targeted)
        for scenario in targeted:
            self.assertNotEqual(scenario["requiredDomainSet"], "full-ga4")
            self.assertNotIn("reports-core-suite", scenario["allowedReads"])
        ambiguous = next(item for item in self.scenarios if item["expectedMode"] == "ambiguous")
        self.assertIsNone(ambiguous["requiredDomainSet"])
        self.assertEqual(ambiguous["allowedReads"], ["resources-list"])

    def test_stage_one_catalog_remains_mutation_safe_after_stage_two_scope_addition(self) -> None:
        boundary = self.catalog["stageBoundary"]
        self.assertTrue(boundary)
        self.assertTrue(all(value is False for value in boundary.values()))
        forbidden = set(self.catalog["globallyForbiddenActions"])
        self.assertTrue(
            {"mutation", "search-console-access", "oauth-scope-change", "invent-missing-evidence"}.issubset(forbidden)
        )
        self.assertFalse(any("webmasters" in scope or "search-console" in scope for scope in BASE_SCOPES))
        self.assertEqual(SEARCH_CONSOLE_SCOPES, ("https://www.googleapis.com/auth/webmasters.readonly",))
        self.assertNotIn("https://www.googleapis.com/auth/webmasters", TARGET_SCOPES)

    def test_skill_routes_to_a_discoverable_single_source_playbook(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        playbook = PLAYBOOK.read_text(encoding="utf-8")
        self.assertIn("Advisor request routing", skill)
        self.assertIn("references/proactive-advisor-playbook.md", skill)
        self.assertIn("Route the request by meaning", playbook)
        self.assertIn("Track completeness", playbook)
        self.assertIn("Resume without repeating work", playbook)
        self.assertNotIn("`https://www.googleapis.com/auth/webmasters`", skill + playbook)


if __name__ == "__main__":
    unittest.main()
