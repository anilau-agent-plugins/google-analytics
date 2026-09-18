from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.google_analytics_cli.advisor_assessment_policy import assessment_request_sha256
from scripts.google_analytics_cli.advisor_assessment_renderer import render_assessment
from scripts.google_analytics_cli.advisor_assessment_service import AdvisorAssessmentService
from scripts.google_analytics_cli.artifact_store import ArtifactStore
from scripts.google_analytics_cli.errors import AdvisorError, EXIT_INPUT


NOW = datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc)
PROFILE = "profile-0123456789abcdef"
PROPERTY = "properties/200"
STREAM = "properties/200/dataStreams/300"
SITE = "sc-domain:example.test"
PERIODS = [
    {"label": "current", "from": "2026-08-18", "to": "2026-09-14", "complete": True},
    {"label": "previous", "from": "2026-07-21", "to": "2026-08-17", "complete": True},
]


def write_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class FakeSourceService:
    def __init__(self, root: Path, source: str, *, fail_run: bool = False) -> None:
        self.root = root
        self.source = source
        self.fail_run = fail_run
        self.plan_calls = 0
        self.run_calls = 0
        self.last_request: dict[str, Any] | None = None

    def plan(self, profile_id: str, resource: str, request_path: Path) -> dict[str, Any]:
        self.plan_calls += 1
        self.last_request = json.loads(request_path.read_text(encoding="utf-8"))
        plan = {
            "artifactType": f"{self.source}-fake-plan",
            "planId": f"{self.source}-plan-12345678",
            "planSha256": ("a" if self.source == "ga4" else "b") * 64,
            "expiresAt": "2026-09-18T09:00:00Z",
            "periods": PERIODS,
            "queries": [{"queryId": f"{self.source}-query"}],
            "budget": {"maxRequests": 20, "maxRows": 12000},
            "blockers": [],
        }
        path = write_json(self.root / ".google-analytics-advisor" / "fake-plans" / f"{self.source}.json", plan)
        return {"status": "ready", "plan": plan, "artifact": {"path": str(path)}}

    def run(self, plan_path: Path) -> dict[str, Any]:
        self.run_calls += 1
        if self.fail_run:
            raise AdvisorError(f"{self.source.upper()}_TEMPORARY", f"{self.source} is temporarily unavailable.", EXIT_INPUT, retryable=True)
        presets = (
            ["overview", "acquisition", "landing", "content", "device", "geo", "events", "key-events"]
            if self.source == "ga4" else ["overview", "pages", "devices"]
        )
        report = {
            "artifactType": "report" if self.source == "ga4" else "search-console-report",
            "reportId": f"{self.source}-report-12345678",
            "reportSha256": ("c" if self.source == "ga4" else "d") * 64,
            "generatedAt": "2026-09-18T08:10:00Z",
            "status": "ready",
            "qualityTier": "reliable_for_description",
            "periods": PERIODS,
            "datasets": [{"preset": preset} for preset in presets],
            "facts": [{"statement": f"{self.source} fact", "evidenceRefs": [f"dataset:{self.source}"]}],
            "calculations": [],
            "interpretations": [],
            "findings": [],
            "limitations": [],
            "recommendations": [
                {"problem": f"{self.source} issue {index}", "category": "investigation", "evidenceRefs": [f"dataset:{self.source}"]}
                for index in range(1, 5)
            ],
            "questions": [],
        }
        path = write_json(self.root / ".google-analytics-advisor" / "fake-reports" / f"{self.source}.json", report)
        return {"status": "ready", "report": report, "artifact": {"path": str(path)}}


class FakeBaselineService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.calls = 0

    def audit(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        audit = {
            "artifactType": "baseline-report", "auditId": "baseline-audit-12345678",
            "generatedAt": "2026-09-18T08:05:00Z", "status": "ready",
            "findings": [], "limitations": [],
            "recommendations": [{"problem": "Tag coverage issue", "category": "measurement", "evidenceRefs": ["tag-scan"]}],
        }
        path = write_json(self.root / ".google-analytics-advisor" / "fake-reports" / "baseline.json", audit)
        return {"status": "ready", "audit": audit, "artifact": {"path": str(path)}}


class FakeCrossSourceService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.plan_calls = 0
        self.run_calls = 0

    def plan(self, request_path: Path) -> dict[str, Any]:
        self.plan_calls += 1
        plan = {"artifactType": "cross-source-analysis-plan", "planSha256": "e" * 64, "blockers": []}
        path = write_json(self.root / ".google-analytics-advisor" / "fake-plans" / "cross.json", plan)
        return {"status": "ready", "plan": plan, "artifact": {"path": str(path)}}

    def run(self, plan_path: Path) -> dict[str, Any]:
        self.run_calls += 1
        report = {
            "artifactType": "cross-source-analysis-report", "reportId": "cross-report-12345678",
            "reportSha256": "f" * 64, "generatedAt": "2026-09-18T08:12:00Z",
            "status": "ready", "qualityTier": "directional_only", "facts": [], "calculations": [],
            "interpretations": [], "findings": [{"statement": "Organic handoff is directionally consistent", "evidenceRefs": ["mapping:1"]}],
            "limitations": [], "recommendations": [], "questions": [],
        }
        path = write_json(self.root / ".google-analytics-advisor" / "fake-reports" / "cross.json", report)
        return {"status": "ready", "report": report, "artifact": {"path": str(path)}}


class AdvisorAssessmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "index.html").write_text("<html></html>", encoding="utf-8")
        self.ga4 = FakeSourceService(self.root, "ga4")
        self.search = FakeSourceService(self.root, "search-console")
        self.cross = FakeCrossSourceService(self.root)
        self.baseline = FakeBaselineService(self.root)
        self.service = AdvisorAssessmentService(
            report_service=self.ga4, search_console_service=self.search,
            cross_source_service=self.cross, baseline_service=self.baseline, now=lambda: NOW,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, *, search_console: bool = True, language: str = "ru") -> Path:
        value = {
            "schemaVersion": 1, "artifactType": "advisor-assessment-request", "createdAt": "2026-09-18T07:55:00Z",
            "requestId": "advisor-request-test1234", "projectRoot": str(self.root), "profileId": PROFILE,
            "property": PROPERTY, "webStream": STREAM, "site": SITE if search_console else None,
            "verifiedOrigin": "https://example.test", "question": "Что там с моим сайтом?", "language": language,
            "mode": "broad", "period": {"mode": "last-complete-days", "days": 28, "from": None, "to": None},
            "comparisons": ["previous-period"], "businessModel": "lead_generation", "baselineRef": None,
            "measurementPlanRef": None, "gtmContainer": None, "knownChangesAfter": None,
            "capabilities": {"searchConsole": "ready" if search_console else "unavailable"}, "contentSha256": "",
        }
        value["contentSha256"] = assessment_request_sha256(value)
        return write_json(self.root / "advisor-request.json", value)

    def test_plan_aligns_sources_and_has_complete_bounded_matrix(self) -> None:
        result = self.service.plan(self.request())
        self.assertEqual(result["status"], "ready")
        self.assertEqual([item["stepId"] for item in result["plan"]["steps"]], ["baseline", "ga4", "search-console", "cross-source", "synthesis"])
        self.assertEqual(len(result["plan"]["domains"]), 14)
        self.assertEqual(self.ga4.last_request["period"], {"mode": "explicit", "days": None, "from": PERIODS[0]["from"], "to": PERIODS[0]["to"]})
        self.assertEqual(self.ga4.run_calls + self.search.run_calls, 0)
        self.assertFalse(result["plan"]["mutationPerformed"])

    def test_run_creates_resumable_chain_and_caps_recommendations(self) -> None:
        planned = self.service.plan(self.request())
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertIn(result["status"], {"ready", "partial"})
        self.assertEqual(len(result["report"]["domains"]), 14)
        self.assertLessEqual(len(result["report"]["recommendations"]), 5)
        self.assertTrue(result["report"]["safeNextStep"])
        self.assertFalse(result["report"]["mutationPerformed"])
        latest = json.loads(Path(result["checkpoint"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(latest["sequence"], 5)
        self.assertEqual(latest["status"], "ready")
        plain = self.service.show(Path(result["artifact"]["path"]), "auto")["plain"]
        self.assertIn("Коротко: что происходит", plain)

    def test_russian_renderer_explains_live_shaped_evidence_without_english_templates(self) -> None:
        report = {
            "diagnosis": "GA4 и Search Console объединены в одну проверяемую картину сайта.",
            "qualityTier": "directional_only",
            "confidence": {
                "level": "directional_only",
                "reasons": [
                    "Checked 14 of 14 domains.",
                    "GA4 and Search Console keep separate definitions and timezone limitations.",
                ],
            },
            "domains": [
                {"domainId": f"domain-{index}", "state": "checked", "reason": "Checked."}
                for index in range(14)
            ],
            "facts": [
                {
                    "factId": "fact:overview:activeUsers:current",
                    "technicalName": "activeUsers",
                    "value": 294,
                    "statement": "Current activeUsers: 294",
                },
                {
                    "factId": "fact:overview:engagementRate:current",
                    "technicalName": "engagementRate",
                    "value": 0.4278,
                    "statement": "Current engagementRate: 0.4278",
                },
            ],
            "calculations": [
                {
                    "calculationId": "calc:search:clicks",
                    "name": "Change in clicks",
                    "inputs": {"current": 40.0, "previous": 49.0},
                    "result": {"absolute": -9.0, "relative": -0.183673},
                },
                {
                    "calculationId": "calculation:search-console:clicks",
                    "name": "Duplicate clicks change",
                    "result": {"current": 40.0, "previous": 49.0, "absolute": -9.0, "relative": -0.183673},
                },
            ],
            "findings": [
                {
                    "findingId": "finding:clicks-without-measured-sessions",
                    "statement": "9 exactly mapped pages have Search Console clicks but no measured GA4 sessions.",
                    "evidenceRefs": [f"page:{index}" for index in range(9)],
                },
                {
                    "code": "KEY_EVENT_NOT_OBSERVED",
                    "eventName": "signup",
                    "statement": "A configured key event was not observed.",
                },
            ],
            "interpretations": [
                {
                    "interpretationId": "interpretation:metric-boundary",
                    "statement": "Search Console clicks are not GA4 sessions.",
                }
            ],
            "limitations": [
                {"code": "TOP_ROWS_ONLY", "message": "This detailed dataset contains top rows."},
                {"type": "small-data", "message": "At least one period has fewer than 20 key events."},
            ],
            "recommendations": [
                {
                    "priority": 1,
                    "problem": "Some clicked pages have no corresponding measured GA4 sessions in the bounded evidence.",
                    "verification": "Validate collection locally.",
                    "requiresMutationWorkflow": True,
                }
            ],
            "safeNextStep": "Prepare a separate safe plan.",
        }

        plain = render_assessment(report, "ru")

        self.assertIn("только для понимания направления", plain)
        self.assertIn("Проверено 14 из 14 областей", plain)
        self.assertIn("Клики из поиска Google (clicks): 49 → 40; изменение -9 (-18.4%)", plain)
        self.assertEqual(plain.count("Клики из поиска Google (clicks):"), 1)
        self.assertIn("Активные пользователи (activeUsers): 294", plain)
        self.assertIn("Доля сессий с взаимодействием (engagementRate): 42.78%", plain)
        self.assertIn("У 9 точно сопоставленных страниц", plain)
        self.assertIn("ключевое событие `signup`", plain)
        self.assertIn("проверены все запланированные области", plain)
        self.assertIn("Подготовить отдельный безопасный план", plain)
        self.assertNotIn("Some clicked pages", plain)
        self.assertNotIn("Validate collection locally", plain)
        self.assertNotIn("Checked 14 of 14", plain)

    def test_missing_search_console_keeps_ga4_and_marks_cross_source_unavailable(self) -> None:
        planned = self.service.plan(self.request(search_console=False))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(self.search.plan_calls, 0)
        self.assertEqual(self.ga4.run_calls, 1)
        states = {item["domainId"]: item["state"] for item in result["report"]["domains"]}
        self.assertEqual(states["search-console-performance"], "unavailable")
        self.assertEqual(states["cross-source-context"], "unavailable")

    def test_resume_reuses_completed_sources_and_only_retries_failed_source(self) -> None:
        self.search.fail_run = True
        planned = self.service.plan(self.request())
        partial = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(partial["status"], "partial")
        baseline_calls, ga_calls = self.baseline.calls, self.ga4.run_calls
        self.search.fail_run = False
        resumed_plan = self.service.resume_plan(Path(partial["checkpoint"]["path"]))
        resumed = self.service.run(Path(resumed_plan["artifact"]["path"]))
        self.assertEqual(self.baseline.calls, baseline_calls)
        self.assertEqual(self.ga4.run_calls, ga_calls)
        self.assertEqual(self.search.run_calls, 2)
        self.assertIn(resumed["status"], {"ready", "partial"})

    def test_tampered_child_plan_is_blocked_before_source_read(self) -> None:
        planned = self.service.plan(self.request())
        ga_step = next(item for item in planned["plan"]["steps"] if item["stepId"] == "ga4")
        child = Path(ga_step["planRef"]["path"])
        child.write_text(child.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaises(AdvisorError) as caught:
            self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(caught.exception.code, "ADVISOR_PLAN_TAMPERED")
        self.assertEqual(self.ga4.run_calls, 0)

    def test_tampered_checkpoint_is_rejected(self) -> None:
        planned = self.service.plan(self.request())
        result = self.service.run(Path(planned["artifact"]["path"]))
        checkpoint = Path(result["checkpoint"]["path"])
        value = json.loads(checkpoint.read_text(encoding="utf-8"))
        value["status"] = "partial"
        write_json(checkpoint, value)
        with self.assertRaises(AdvisorError) as caught:
            self.service.resume_plan(checkpoint)
        self.assertEqual(caught.exception.code, "ADVISOR_CHECKPOINT_TAMPERED")

    def test_scenario_catalog_keeps_stage_invariants_explicit(self) -> None:
        catalog = json.loads((Path(__file__).parent / "fixtures" / "advisor-full-picture-scenarios.json").read_text(encoding="utf-8"))
        self.assertEqual({item["id"] for item in catalog["scenarios"]}, {
            "full-ga4-search-console", "ga4-only", "search-console-temporary-failure",
            "resume-after-search-console-failure", "tampered-child-plan", "tampered-checkpoint-chain",
        })
        self.assertEqual(catalog["invariants"]["domainCount"], 14)
        self.assertFalse(catalog["invariants"]["mutations"])


if __name__ == "__main__":
    unittest.main()
