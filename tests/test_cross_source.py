from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.google_analytics_cli.artifact_store import canonical_json
from scripts.google_analytics_cli.cross_source_policy import normalize_ga4_landing, normalize_search_console_page, origin_belongs_to_site
from scripts.google_analytics_cli.cross_source_service import CrossSourceService, cross_source_request_sha256
from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.report_service import report_sha256
from scripts.google_analytics_cli.search_console_report_service import search_console_report_sha256


NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
PROFILE = "profile-0123456789abcdef"
PROPERTY = "properties/200"
STREAM = "properties/200/dataStreams/300"
SITE = "sc-domain:example.test"
PERIODS = [
    {"label": "current", "from": "2026-08-17", "to": "2026-09-13", "complete": True},
    {"label": "previous", "from": "2026-07-20", "to": "2026-08-16", "complete": True},
]


def ga_metrics(**values: float) -> dict[str, Any]:
    return {name: {"raw": str(value), "value": value, "type": "TYPE_FLOAT"} for name, value in values.items()}


def filter_payload() -> dict[str, Any]:
    return {"dimensionFilter": {"andGroup": {"expressions": [
        {"filter": {"fieldName": "streamId", "stringFilter": {"matchType": "EXACT", "value": "300"}}},
        {"filter": {"fieldName": "sessionSource", "stringFilter": {"matchType": "EXACT", "value": "google"}}},
        {"filter": {"fieldName": "sessionMedium", "stringFilter": {"matchType": "EXACT", "value": "organic"}}},
    ]}}}


class CrossSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.service = CrossSourceService(now=lambda: NOW)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, name: str, value: dict[str, Any]) -> Path:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _ga4(self) -> Path:
        datasets = [
            {"datasetId": "dataset:query-google-organic-overview", "queryId": "query-google-organic-overview", "preset": "google-organic-overview", "dimensionHeaders": ["dateRange"], "metricHeaders": [], "rows": [
                {"dimensions": {"dateRange": "0"}, "metrics": ga_metrics(sessions=90, engagedSessions=60, engagementRate=.66, keyEvents=12, sessionKeyEventRate=.13)},
                {"dimensions": {"dateRange": "1"}, "metrics": ga_metrics(sessions=80, engagedSessions=50, engagementRate=.62, keyEvents=10, sessionKeyEventRate=.12)},
            ], "totals": [], "rowCount": 2, "returnedRows": 2, "quality": {}, "rowsSha256": "a" * 64},
            {"datasetId": "dataset:query-google-organic-landing", "queryId": "query-google-organic-landing", "preset": "google-organic-landing", "dimensionHeaders": ["landingPage", "dateRange"], "metricHeaders": [], "rows": [
                {"dimensions": {"landingPage": "/good", "dateRange": "0"}, "metrics": ga_metrics(sessions=70, engagedSessions=50, engagementRate=.71, keyEvents=8, sessionKeyEventRate=.11)},
                {"dimensions": {"landingPage": "/Variant/", "dateRange": "0"}, "metrics": ga_metrics(sessions=20, engagedSessions=10, engagementRate=.5, keyEvents=1, sessionKeyEventRate=.05)},
            ], "totals": [], "rowCount": 2, "returnedRows": 2, "quality": {}, "rowsSha256": "b" * 64},
            {"datasetId": "dataset:query-google-organic-device", "queryId": "query-google-organic-device", "preset": "google-organic-device", "dimensionHeaders": ["deviceCategory", "dateRange"], "metricHeaders": [], "rows": [
                {"dimensions": {"deviceCategory": "mobile", "dateRange": "0"}, "metrics": ga_metrics(sessions=60, engagementRate=.6, keyEvents=7)}
            ], "totals": [], "rowCount": 1, "returnedRows": 1, "quality": {}, "rowsSha256": "c" * 64},
        ]
        queries = [{"queryId": f"query-{preset}", "preset": preset, "provider": "analytics-data", "method": "data.report.run", "apiChannel": "v1beta", "requestIds": [], "request": filter_payload(), "returnPropertyQuota": True, "responseQuality": {}} for preset in ("google-organic-overview", "google-organic-landing", "google-organic-device")]
        report = {"schemaVersion": 2, "artifactType": "report", "generatedAt": "2026-09-17T10:00:00Z", "reportId": "report-cross-source-example", "reportSha256": "", "profileId": PROFILE, "property": PROPERTY, "timezone": "Asia/Bangkok", "currency": "USD", "status": "ready", "qualityTier": "reliable_for_description", "periods": PERIODS, "propertyContext": {"timeZone": "Asia/Bangkok", "currencyCode": "USD", "baselineRef": None, "measurementPlanRef": None, "qualityTier": "descriptive", "language": "en", "webStream": {"name": STREAM, "type": "WEB_DATA_STREAM", "defaultOrigin": "https://example.test", "streamId": "300", "contentSha256": "d" * 64}}, "datasets": datasets, "queries": queries, "facts": [], "calculations": [], "interpretations": [], "limitations": [], "recommendations": [], "questions": []}
        report["reportSha256"] = report_sha256(report)
        return self._write("ga4-report.json", report)

    def _search(self) -> Path:
        quality = {"queryPresent": False, "queryRemoved": False, "fragmentRemoved": False, "redacted": False, "normalizationRevision": "search-console-page-v1"}
        query_quality = {**quality, "queryPresent": True, "queryRemoved": True}
        datasets = [
            {"datasetId": "dataset:sc-overview-total-current", "queryId": "sc-overview-total-current", "preset": "overview", "periodLabel": "current", "searchType": "web", "dataState": "final", "dimensions": [], "aggregationType": "auto", "metadata": {}, "rows": [{"dimensions": {}, "metrics": {"clicks": 100.0, "impressions": 5000.0, "ctr": .02, "position": 8.0}}], "returnedRows": 1, "truncated": False, "completeness": "complete_summary", "privacyRedactions": 0, "rowsSha256": "a" * 64},
            {"datasetId": "dataset:sc-pages-current", "queryId": "sc-pages-current", "preset": "pages", "periodLabel": "current", "searchType": "web", "dataState": "final", "dimensions": ["page"], "aggregationType": "byPage", "metadata": {}, "rows": [
                {"dimensions": {"page": "https://example.test/good"}, "dimensionQuality": {"page": quality}, "metrics": {"clicks": 50.0, "impressions": 2000.0, "ctr": .01, "position": 7.0}},
                {"dimensions": {"page": "https://example.test/private"}, "dimensionQuality": {"page": query_quality}, "metrics": {"clicks": 5.0, "impressions": 200.0, "ctr": .025, "position": 9.0}},
                {"dimensions": {"page": "https://example.test/variant"}, "dimensionQuality": {"page": quality}, "metrics": {"clicks": 3.0, "impressions": 50.0, "ctr": .06, "position": 4.0}},
            ], "returnedRows": 3, "truncated": False, "completeness": "top_rows_only", "privacyRedactions": 1, "rowsSha256": "b" * 64},
            {"datasetId": "dataset:sc-pages-previous", "queryId": "sc-pages-previous", "preset": "pages", "periodLabel": "previous", "searchType": "web", "dataState": "final", "dimensions": ["page"], "aggregationType": "byPage", "metadata": {}, "rows": [], "returnedRows": 0, "truncated": False, "completeness": "empty", "privacyRedactions": 0, "rowsSha256": "c" * 64},
            {"datasetId": "dataset:sc-devices-current", "queryId": "sc-devices-current", "preset": "devices", "periodLabel": "current", "searchType": "web", "dataState": "final", "dimensions": ["device"], "aggregationType": "auto", "metadata": {}, "rows": [{"dimensions": {"device": "MOBILE"}, "metrics": {"clicks": 80.0, "impressions": 4000.0, "ctr": .02, "position": 8.0}}], "returnedRows": 1, "truncated": False, "completeness": "complete_summary", "privacyRedactions": 0, "rowsSha256": "d" * 64},
            {"datasetId": "dataset:sc-devices-previous", "queryId": "sc-devices-previous", "preset": "devices", "periodLabel": "previous", "searchType": "web", "dataState": "final", "dimensions": ["device"], "aggregationType": "auto", "metadata": {}, "rows": [], "returnedRows": 0, "truncated": False, "completeness": "empty", "privacyRedactions": 0, "rowsSha256": "e" * 64},
        ]
        report = {"schemaVersion": 2, "artifactType": "search-console-report", "generatedAt": "2026-09-17T10:00:00Z", "reportId": "search-console-report-cross-example", "reportSha256": "", "profileId": PROFILE, "site": SITE, "propertyIdentity": {"selectionKey": SITE}, "timezone": "America/Los_Angeles", "status": "ready", "qualityTier": "directional_only", "periods": [{**item, "dataState": "final"} for item in PERIODS], "datasets": datasets, "queries": [], "budget": {"automaticRetries": 0}, "facts": [], "calculations": [], "interpretations": [], "limitations": [{"code": "TOP_ROWS_ONLY", "severity": "warning", "message": "Page rows are top rows.", "evidenceRef": "dataset:sc-pages-current"}], "recommendations": [], "questions": [], "mutationPerformed": False}
        report["reportSha256"] = search_console_report_sha256(report)
        return self._write("sc-report.json", report)

    def _request(self) -> Path:
        ga4, search = self._ga4(), self._search()
        request = {"schemaVersion": 1, "artifactType": "cross-source-analysis-request", "createdAt": "2026-09-17T10:30:00Z", "requestId": "cross-source-request-test1234", "projectRoot": str(self.root), "profileId": PROFILE, "property": PROPERTY, "webStream": STREAM, "site": SITE, "question": "What happens before and after organic visits?", "language": "en", "ga4Report": {"path": str(ga4), "fileSha256": hashlib.sha256(ga4.read_bytes()).hexdigest(), "artifactSha256": json.loads(ga4.read_text())["reportSha256"]}, "searchConsoleReport": {"path": str(search), "fileSha256": hashlib.sha256(search.read_bytes()).hexdigest(), "artifactSha256": json.loads(search.read_text())["reportSha256"]}, "analyses": ["overview-handoff", "landing-opportunities", "device-context"], "expectedPeriods": {item["label"]: {"from": item["from"], "to": item["to"]} for item in PERIODS}, "verifiedOrigin": "https://example.test", "baselineRef": None, "measurementPlanRef": None, "inspectionReportRef": None, "contentSha256": ""}
        request["contentSha256"] = cross_source_request_sha256(request)
        return self._write("cross-request.json", request)

    def test_strict_url_policy_does_not_fuzzy_join(self) -> None:
        self.assertTrue(origin_belongs_to_site("https://www.example.test", SITE))
        self.assertEqual(normalize_ga4_landing("https://example.test/a", "https://example.test")["state"], "invalid_or_redacted")
        quality = {"queryPresent": True, "queryRemoved": True, "fragmentRemoved": False, "redacted": False}
        self.assertEqual(normalize_search_console_page("https://example.test/a", quality, "https://example.test")["state"], "query_ambiguous")

    def test_plan_and_run_are_offline_exact_and_tamper_evident(self) -> None:
        planned = self.service.plan(self._request())
        self.assertEqual(planned["status"], "ready")
        self.assertFalse(planned["plan"]["networkUsed"])
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertFalse(result["report"]["networkUsed"])
        counts = result["report"]["mappingSummary"]["counts"]
        self.assertEqual(counts["exact"], 1)
        self.assertEqual(counts["query_ambiguous"], 1)
        self.assertEqual(counts["path_variant"], 2)
        self.assertIn("different definitions", result["report"]["interpretations"][-1]["statement"])
        self.assertLessEqual(len(result["report"]["recommendations"]), 5)
        plain = self.service.show(Path(result["artifact"]["path"]), "ru")["plain"]
        self.assertIn("Короткий ответ", plain)

    def test_source_file_hash_change_blocks_run(self) -> None:
        planned = self.service.plan(self._request())
        ga_path = Path(planned["plan"]["request"]["ga4Report"]["path"])
        ga_path.write_text(ga_path.read_text() + " ", encoding="utf-8")
        with self.assertRaises(AdvisorError) as caught:
            self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(caught.exception.code, "CROSS_SOURCE_SOURCE_TAMPERED")


if __name__ == "__main__":
    unittest.main()
