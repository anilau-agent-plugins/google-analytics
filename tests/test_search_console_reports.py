from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.search_console_report_renderer import render_search_console_report
from scripts.google_analytics_cli.search_console_report_service import (
    SearchConsoleReportService,
    resolve_search_console_periods,
    search_console_request_sha256,
)


PROFILE = "profile-0123456789abcdef"
SITE = "sc-domain:example.com"
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


class FakeAuth:
    def __init__(self, *, ready: bool = True):
        self.ready = ready

    def status(self, profile_id):
        return {"profileId": profile_id, "capabilities": {"searchConsole": {"status": "ready" if self.ready else "authorization_required"}}}

    def access_token(self, profile_id):
        return profile_id, "private-token", {"clientRef": "client-fixture", "identity": {"sub": "subject-fixture"}}


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.empty = False
        self.malformed = False
        self.preliminary = False
        self.error: AdvisorError | None = None

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if self.error and "searchAnalytics/query" in url:
            raise self.error
        if url.endswith("/webmasters/v3/sites"):
            return JsonResponse(200, {"siteEntry": [{"siteUrl": SITE, "permissionLevel": "SITE_OWNER"}]}, "site-request", {})
        if "searchAnalytics/query" not in url:
            raise AssertionError(url)
        if self.malformed:
            return JsonResponse(200, {"rows": {}}, "bad-request", {})
        payload = kwargs["payload"]
        dimensions = payload.get("dimensions", [])
        rows = []
        if not self.empty:
            if dimensions == ["date"]:
                rows = [{"keys": [payload["endDate"]], "clicks": 10, "impressions": 200, "ctr": 0.05, "position": 4.2}]
            elif dimensions == ["query"]:
                rows = [{"keys": ["analytics setup"], "clicks": 4, "impressions": 120, "ctr": 0.033, "position": 5.1}]
            elif dimensions == ["page"]:
                rows = [{"keys": ["https://example.com/page?private=1"], "clicks": 5, "impressions": 130, "ctr": 0.038, "position": 4.8}]
            elif dimensions == ["device"]:
                rows = [{"keys": ["MOBILE"], "clicks": 6, "impressions": 120, "ctr": 0.05, "position": 4.0}]
            elif dimensions == ["country"]:
                rows = [{"keys": ["tha"], "clicks": 6, "impressions": 120, "ctr": 0.05, "position": 4.0}]
            elif dimensions == ["searchAppearance"]:
                rows = [{"keys": ["WEB_RESULT"], "clicks": 7, "impressions": 140, "ctr": 0.05, "position": 4.0}]
            elif dimensions == ["hour"]:
                rows = [{"keys": ["2026-09-17T03:00:00-07:00"], "clicks": 1, "impressions": 20, "ctr": 0.05}]
            else:
                is_previous = payload["endDate"] < "2026-08-20"
                rows = [{"keys": [], "clicks": 80 if is_previous else 100, "impressions": 4000 if is_previous else 5000, "ctr": 0.02, "position": 6.0}]
        metadata = {"first_incomplete_date": payload["endDate"]} if self.preliminary else {}
        return JsonResponse(200, {"rows": rows, "responseAggregationType": "byProperty", "metadata": metadata}, f"request-{len(self.calls)}", {})


class SearchConsoleReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.transport = FakeTransport()
        self.service = SearchConsoleReportService(auth=FakeAuth(), transport=self.transport, now=lambda: NOW)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, *, presets=None, data_state="final", search_type="web", period=None, comparisons=None, filters=None) -> Path:
        value = {
            "schemaVersion": 1, "artifactType": "search-console-report-request",
            "createdAt": "2026-09-17T10:00:00Z", "requestId": f"sc-request-{len(list(self.root.glob('request-*.json')))}",
            "projectRoot": str(self.root), "profileId": PROFILE, "site": SITE,
            "question": "What changed in organic search?", "language": "en",
            "period": period or {"mode": "last-complete-days", "days": 28, "from": None, "to": None},
            "comparisons": ["previous-period"] if comparisons is None else comparisons,
            "presets": presets or ["overview"], "searchType": search_type,
            "dataState": data_state, "filters": filters or [], "contentSha256": "",
        }
        value["contentSha256"] = search_console_request_sha256(value)
        path = self.root / f"request-{len(list(self.root.glob('request-*.json')))}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_catalog_is_bounded_and_explains_api_limitations(self) -> None:
        result = self.service.catalog(PROFILE, SITE)
        self.assertEqual(result["defaults"]["preset"], "overview")
        self.assertEqual(result["limits"]["maxRequests"], 20)
        self.assertTrue(any("GenAI" in item for item in result["limitations"]))
        self.assertEqual(len(self.transport.calls), 1)
        self.assertFalse(result["mutationPerformed"])

    def test_default_final_period_uses_pacific_and_conservative_completed_day(self) -> None:
        request = json.loads(self.request().read_text())
        periods, limitations = resolve_search_console_periods(request, now=lambda: NOW)
        self.assertEqual(periods[0]["to"], "2026-09-14")
        self.assertEqual(periods[0]["from"], "2026-08-18")
        self.assertEqual(periods[1]["to"], "2026-08-17")
        self.assertFalse(limitations)

    def test_pacific_fallback_preserves_dates_without_external_tzdata(self) -> None:
        request = json.loads(self.request().read_text())
        with patch(
            "scripts.google_analytics_cli.search_console_report_service.ZoneInfo",
            side_effect=ZoneInfoNotFoundError,
        ):
            periods, _ = resolve_search_console_periods(request, now=lambda: NOW)
        self.assertEqual(periods[0]["to"], "2026-09-14")

    def test_plan_is_immutable_bounded_and_does_not_read_performance(self) -> None:
        result = self.service.plan(PROFILE, SITE, self.request())
        self.assertEqual(result["status"], "ready")
        plan = result["plan"]
        self.assertLessEqual(plan["budget"]["plannedRequests"], 20)
        self.assertEqual(plan["budget"]["automaticRetries"], 0)
        self.assertTrue(all(item["operationId"] == "searchconsole.searchanalytics.query" for item in plan["queries"]))
        self.assertTrue(all(item["rowLimit"] <= 1000 and item["maxPages"] <= 2 for item in plan["queries"]))
        self.assertFalse(any("searchAnalytics/query" in call["url"] for call in self.transport.calls))

    def test_oversized_preset_suite_creates_a_blocked_bounded_plan(self) -> None:
        result = self.service.plan(
            PROFILE, SITE,
            self.request(
                presets=["overview", "queries", "pages", "devices", "countries", "search-appearance"],
                comparisons=["previous-period", "previous-year"],
            ),
        )
        self.assertEqual(result["status"], "blocked")
        self.assertLessEqual(len(result["plan"]["queries"]), 20)
        self.assertTrue(result["plan"]["blockers"])

    def test_overview_run_preserves_evidence_top_rows_and_no_retries(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request())
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertIn(result["status"], {"ready", "partial"})
        report = result["report"]
        self.assertTrue(report["facts"])
        self.assertTrue(report["calculations"])
        self.assertTrue(any(item["code"] == "TOP_ROWS_ONLY" for item in report["limitations"]))
        query_calls = [call for call in self.transport.calls if "searchAnalytics/query" in call["url"]]
        self.assertTrue(query_calls)
        self.assertTrue(all(call["max_attempts"] == 1 for call in query_calls))
        self.assertTrue(all(item["automaticRetries"] == 0 for item in report["queries"]))
        self.assertFalse(report["mutationPerformed"])

    def test_search_appearance_uses_provider_value_in_second_step(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request(presets=["search-appearance"], comparisons=[]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        details = [item for item in result["report"]["datasets"] if item.get("appearanceValue")]
        self.assertEqual(details[0]["appearanceValue"], "WEB_RESULT")
        evidence = next(item for item in result["report"]["queries"] if item.get("providerDerivedFilter"))
        self.assertTrue(evidence["providerDerivedFilter"])
        groups = evidence["request"]["dimensionFilterGroups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["filters"][-1]["expression"], "WEB_RESULT")

    def test_preliminary_marker_is_preserved(self) -> None:
        self.transport.preliminary = True
        planned = self.service.plan(PROFILE, SITE, self.request(data_state="all", comparisons=[]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["report"]["qualityTier"], "preliminary")
        self.assertTrue(any(item["code"] == "PRELIMINARY_DATA" for item in result["report"]["limitations"]))

    def test_hourly_mode_is_three_days_and_position_is_unavailable_for_discover(self) -> None:
        request = self.request(
            presets=["recent-hourly"], data_state="hourly_all", search_type="discover",
            period={"mode": "last-complete-days", "days": 3, "from": None, "to": None}, comparisons=[],
        )
        planned = self.service.plan(PROFILE, SITE, request)
        result = self.service.run(Path(planned["artifact"]["path"]))
        hourly = next(item for item in result["report"]["datasets"] if item["preset"] == "recent-hourly")
        self.assertIsNone(hourly["rows"][0]["metrics"]["position"])
        self.assertFalse(hourly["periodLabel"] != "current")

    def test_discover_query_preset_is_omitted_not_fabricated(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request(presets=["queries"], search_type="discover", comparisons=[]))
        self.assertTrue(any("unavailable" in item for item in planned["plan"]["limitations"]))
        self.assertFalse(any(item["preset"] == "queries" for item in planned["plan"]["queries"]))

    def test_empty_probe_stops_details(self) -> None:
        self.transport.empty = True
        planned = self.service.plan(PROFILE, SITE, self.request())
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["status"], "empty")
        query_calls = [call for call in self.transport.calls if "searchAnalytics/query" in call["url"]]
        self.assertEqual(len(query_calls), 1)
        self.assertTrue(any(item["code"] == "SEARCH_CONSOLE_DATA_UNAVAILABLE" for item in result["report"]["limitations"]))

    def test_tampered_plan_and_wrong_site_are_blocked(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request())
        path = Path(planned["artifact"]["path"])
        value = json.loads(path.read_text())
        value["site"] = "sc-domain:other.example"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AdvisorError) as caught:
            self.service.run(path)
        self.assertIn(caught.exception.code, {"ARTIFACT_VALIDATION_FAILED", "SEARCH_CONSOLE_REPORT_PLAN_TAMPERED"})

    def test_quota_error_is_not_retried(self) -> None:
        self.transport.error = AdvisorError("QUOTA_LIMITED", "limited", 5, retryable=False)
        planned = self.service.plan(PROFILE, SITE, self.request())
        with self.assertRaises(AdvisorError) as caught:
            self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_QUOTA_EXCEEDED")
        query_calls = [call for call in self.transport.calls if "searchAnalytics/query" in call["url"]]
        self.assertEqual(len(query_calls), 1)

    def test_malformed_response_fails_closed(self) -> None:
        self.transport.malformed = True
        planned = self.service.plan(PROFILE, SITE, self.request())
        with self.assertRaises(AdvisorError) as caught:
            self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_RESPONSE_INVALID")

    def test_request_semantics_block_hourly_mismatch_and_long_range(self) -> None:
        with self.assertRaises(AdvisorError):
            self.service.plan(PROFILE, SITE, self.request(presets=["recent-hourly"], data_state="final"))
        value = json.loads(self.request().read_text())
        value["period"] = {"mode": "explicit", "days": None, "from": "2025-01-01", "to": "2026-09-01"}
        value["contentSha256"] = search_console_request_sha256(value)
        path = self.root / "request-long.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AdvisorError) as caught:
            self.service.plan(PROFILE, SITE, path)
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_REPORT_REQUEST_INVALID")

    def test_plain_language_renderer_is_bilingual_and_offline(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request())
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertIn("Google Search Console", render_search_console_report(result["report"], "en"))
        russian = render_search_console_report(result["report"], "ru")
        self.assertIn("Что происходит", russian)
        self.assertIn("Клики (clicks)", russian)


if __name__ == "__main__":
    unittest.main()
