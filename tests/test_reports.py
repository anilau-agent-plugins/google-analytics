from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.measurement_policy import plan_content_sha256
from scripts.google_analytics_cli.report_periods import comparison, resolve_periods
from scripts.google_analytics_cli.report_service import ReportService, report_plan_sha256, report_request_sha256
from scripts.google_analytics_cli.report_analysis import redact_text
from scripts.google_analytics_cli.report_renderer import render_report


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
PROFILE = "profile-0123456789abcdef"
PROPERTY = "properties/200"


class FakeAuth:
    def access_token(self, profile_id: str | None = None):
        return profile_id, "access-token-not-serialized", {"identity": {"email": "test@example.invalid"}}


class FakeReportTransport:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.sampled = False
        self.thresholded = False
        self.other = False
        self.restricted = False
        self.redact_event = False
        self.low_quota = False
        self.incompatible = False
        self.extra_incompatible = False
        self.empty = False
        self.metadata = {
            "dimensions": [{"apiName": item} for item in (
                "date", "sessionDefaultChannelGroup", "firstUserDefaultChannelGroup", "landingPage",
                "pagePath", "pageTitle", "deviceCategory", "country", "eventName",
                "streamId", "sessionSource", "sessionMedium",
            )],
            "metrics": [{"apiName": item, "type": "TYPE_INTEGER", "blockedReasons": []} for item in (
                "activeUsers", "newUsers", "sessions", "engagedSessions", "eventCount", "keyEvents",
                "screenPageViews", "ecommercePurchases", "itemsPurchased",
            )] + [{"apiName": item, "type": "TYPE_FLOAT", "blockedReasons": []} for item in (
                "engagementRate", "averageSessionDuration", "sessionKeyEventRate", "userKeyEventRate",
                "totalRevenue", "purchaseRevenue", "grossPurchaseRevenue", "refundAmount", "userEngagementDuration",
            )],
        }

    def request(self, method: str, url: str, **kwargs: Any) -> JsonResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        if method == "GET" and url.endswith("/v1beta/properties/200"):
            return JsonResponse(200, {"name": PROPERTY, "timeZone": "Asia/Bangkok", "currencyCode": "USD"}, "property-1", {})
        if method == "GET" and url.endswith("/v1beta/properties/200/metadata"):
            metadata = json.loads(json.dumps(self.metadata))
            if self.restricted:
                next(item for item in metadata["metrics"] if item["apiName"] == "totalRevenue")["blockedReasons"] = ["NO_REVENUE_METRICS"]
            return JsonResponse(200, metadata, "metadata-1", {})
        if method == "GET" and "/v1beta/properties/200/dataStreams/" in url:
            stream_name = url.split("/v1beta/", 1)[1]
            return JsonResponse(200, {"name": stream_name, "type": "WEB_DATA_STREAM", "webStreamData": {"defaultUri": "https://Example.Test:443/path", "measurementId": "G-TEST"}}, "stream-1", {})
        if method == "POST" and url.endswith(":checkCompatibility"):
            payload = kwargs["payload"]
            data = {
                "dimensionCompatibilities": [{"dimensionMetadata": {"apiName": item["name"]}, "compatibility": "COMPATIBLE"} for item in payload.get("dimensions", [])],
                "metricCompatibilities": [{"metricMetadata": {"apiName": item["name"]}, "compatibility": "COMPATIBLE"} for item in payload.get("metrics", [])],
            }
            if self.incompatible and data["metricCompatibilities"]:
                data["metricCompatibilities"][0]["compatibility"] = "INCOMPATIBLE"
            if self.extra_incompatible:
                data["dimensionCompatibilities"].append({"dimensionMetadata": {"apiName": "cohortNthDay"}, "compatibility": "INCOMPATIBLE"})
                data["metricCompatibilities"].append({"metricMetadata": {"apiName": "grossItemRevenue"}, "compatibility": "INCOMPATIBLE"})
            return JsonResponse(200, data, "compatibility-1", {})
        if method == "POST" and url.endswith(":runReport"):
            return JsonResponse(200, self._core(kwargs["payload"]), f"report-{len(self.calls)}", {})
        if method == "POST" and url.endswith(":runRealtimeReport"):
            return JsonResponse(200, self._realtime(kwargs["payload"]), "realtime-1", {})
        if method == "POST" and url.endswith(":runFunnelReport"):
            return JsonResponse(200, {"funnelTable": {"dimensionHeaders": [{"name": "funnelStepName"}], "metricHeaders": [{"name": "activeUsers", "type": "TYPE_INTEGER"}], "rows": [{"dimensionValues": [{"value": "generate_lead"}], "metricValues": [{"value": "10"}]}], "rowCount": 1, "metadata": {"samplingMetadatas": []}}, "propertyQuota": self._quota()}, "funnel-1", {})
        raise AssertionError(f"Unexpected request: {method} {url}")

    def _quota(self) -> dict[str, Any]:
        remaining = 50 if self.low_quota else 1000
        return {"tokensPerHour": {"consumed": 1, "remaining": remaining}, "tokensPerProjectPerHour": {"consumed": 1, "remaining": remaining}}

    def _core(self, payload: dict[str, Any]) -> dict[str, Any]:
        dimensions = [item["name"] for item in payload.get("dimensions", [])]
        metrics = [item["name"] for item in payload.get("metrics", [])]
        if not dimensions:
            dimensions = ["dateRange"]
            dimension_values = [["0"], ["1"]] if len(payload.get("dateRanges", [])) > 1 else [["0"]]
        else:
            value = "person@example.com" if self.redact_event and dimensions == ["eventName"] else "Organic Search"
            dimension_values = [[value]]
        rows = []
        for row_index, values in enumerate(dimension_values):
            rows.append({"dimensionValues": [{"value": value} for value in values], "metricValues": [{"value": str(max(0, 100 - row_index * 20 - index))} for index, _ in enumerate(metrics)]})
        metadata: dict[str, Any] = {"subjectToThresholding": self.thresholded, "dataLossFromOtherRow": self.other, "timeZone": "Asia/Bangkok", "currencyCode": "USD"}
        if self.empty:
            rows = []
            metadata["emptyReason"] = "No rows matched the selected complete period."
        if self.sampled:
            metadata["samplingMetadatas"] = [{"samplesReadCount": "500", "samplingSpaceSize": "1000"}]
        if self.restricted and "totalRevenue" in metrics:
            metadata["schemaRestrictionResponse"] = {"activeMetricRestrictions": [{"metricName": "totalRevenue", "restrictedMetricTypes": ["REVENUE_DATA"]}]}
        return {
            "dimensionHeaders": [{"name": item} for item in dimensions],
            "metricHeaders": [{"name": item, "type": "TYPE_FLOAT" if "Rate" in item or "Revenue" in item or "Duration" in item else "TYPE_INTEGER"} for item in metrics],
            "rows": rows, "rowCount": len(rows), "metadata": metadata, "propertyQuota": self._quota(),
        }

    def _realtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {"dimensionHeaders": [{"name": "eventName"}], "metricHeaders": [{"name": "activeUsers", "type": "TYPE_INTEGER"}, {"name": "eventCount", "type": "TYPE_INTEGER"}], "rows": [{"dimensionValues": [{"value": "page_view"}], "metricValues": [{"value": "2"}, {"value": "4"}]}], "rowCount": 1, "propertyQuota": self._quota()}


class ReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.transport = FakeReportTransport()
        self.service = ReportService(auth=FakeAuth(), transport=self.transport, now=lambda: NOW, env={})

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _request(self, presets: list[str] | None = None, **changes: Any) -> Path:
        value: dict[str, Any] = {
            "schemaVersion": 1, "artifactType": "report-request", "createdAt": "2026-08-19T10:00:00Z",
            "requestId": "report-request-test", "projectRoot": str(self.root), "profileId": PROFILE,
            "property": PROPERTY, "question": "What changed?", "language": "en",
            "period": {"mode": "last-complete-days", "days": 28, "from": None, "to": None},
            "comparisons": ["previous-period"], "presets": presets or ["overview"],
            "includeToday": False, "experimentalFunnel": False, "alphaDisclosureAccepted": False,
            "baselineRef": None, "measurementPlanRef": None, "customCore": None, "contentSha256": "",
        }
        value.update(changes)
        value["contentSha256"] = report_request_sha256(value)
        path = self.root / f"request-{len(list(self.root.glob('request-*.json')))}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def _plan(self, request: Path) -> dict[str, Any]:
        return self.service.plan(PROFILE, PROPERTY, request)

    def test_default_periods_and_zero_comparison_are_safe(self) -> None:
        request = json.loads(self._request().read_text())
        periods, limitations = resolve_periods(request, "Asia/Bangkok", now=lambda: NOW)
        self.assertEqual(periods[0], {"label": "current", "from": "2026-07-22", "to": "2026-08-18", "complete": True})
        self.assertEqual(periods[1], {"label": "previous", "from": "2026-06-24", "to": "2026-07-21", "complete": True})
        self.assertFalse(limitations)
        self.assertEqual(comparison(5, 0)["state"], "from-zero")
        self.assertIsNone(comparison(5, 0)["relative"])

    def test_iso_report_date_is_not_mistaken_for_a_phone_number(self) -> None:
        self.assertEqual(redact_text("2026-07-31"), ("2026-07-31", False))
        self.assertEqual(redact_text("+1 202 555 0123"), ("[redacted]", True))

    def test_today_is_incomplete_and_leap_day_year_comparison_is_omitted(self) -> None:
        today_request = json.loads(self._request(includeToday=True).read_text())
        today_periods, today_limitations = resolve_periods(today_request, "Asia/Bangkok", now=lambda: NOW)
        self.assertFalse(today_periods[0]["complete"])
        self.assertTrue(any("incomplete" in item.lower() for item in today_limitations))
        leap_request = json.loads(self._request(period={"mode": "custom", "days": None, "from": "2024-02-29", "to": "2024-02-29"}, comparisons=["previous-year"]).read_text())
        periods, limitations = resolve_periods(leap_request, "Asia/Bangkok", now=lambda: NOW)
        self.assertEqual([item["label"] for item in periods], ["current"])
        self.assertTrue(any("february 29" in item.lower() for item in limitations))

    def test_plan_is_read_only_immutable_and_metadata_compatible(self) -> None:
        result = self._plan(self._request(["overview", "acquisition", "events"]))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["plan"]["planSha256"], report_plan_sha256(result["plan"]))
        self.assertFalse(result["plan"]["mutationPerformed"])
        self.assertTrue(all(item["operationId"] == "data.report.run" for item in result["plan"]["queries"]))
        self.assertFalse(any(call["method"] in {"PATCH", "PUT", "DELETE"} for call in self.transport.calls))

    def test_incompatible_query_is_blocked_before_run(self) -> None:
        self.transport.incompatible = True
        result = self._plan(self._request(["overview"]))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("incompatible" in item.lower() for item in result["plan"]["blockers"]))

    def test_google_organic_presets_require_and_fingerprint_exact_web_stream(self) -> None:
        request = self._request(
            ["google-organic-overview", "google-organic-landing", "google-organic-device"],
            webStream="properties/200/dataStreams/300",
        )
        planned = self._plan(request)
        self.assertEqual(planned["status"], "ready")
        context = planned["plan"]["propertyContext"]["webStream"]
        self.assertEqual(context["defaultOrigin"], "https://example.test")
        self.assertEqual(context["streamId"], "300")
        for query in planned["plan"]["queries"]:
            encoded = json.dumps(query["payload"]["dimensionFilter"])
            self.assertIn('"streamId"', encoded)
            self.assertIn('"google"', encoded)
            self.assertIn('"organic"', encoded)
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["report"]["propertyContext"]["webStream"], context)

    def test_long_numeric_web_stream_resource_is_not_mistaken_for_a_phone_number(self) -> None:
        request = self._request(
            ["google-organic-overview"],
            webStream="properties/200/dataStreams/12992060055",
        )
        planned = self._plan(request)
        result = self.service.run(Path(planned["artifact"]["path"]))
        encoded_filter = json.dumps(result["report"]["queries"][0]["request"]["dimensionFilter"])
        self.assertIn('"12992060055"', encoded_filter)
        self.assertFalse(any(item["type"] == "privacy" for item in result["report"]["limitations"]))

    def test_phone_shaped_user_text_remains_blocked(self) -> None:
        request = self._request(question="Call +1 202 555 0123")
        with self.assertRaises(AdvisorError) as raised:
            self.service._load_request(request)
        self.assertEqual(raised.exception.code, "REPORT_PRIVACY_REDACTED")

    def test_google_organic_presets_block_missing_or_cross_property_stream(self) -> None:
        with self.assertRaises(AdvisorError):
            self._plan(self._request(["google-organic-overview"]))
        with self.assertRaises(AdvisorError):
            self._plan(self._request(["google-organic-overview"], webStream="properties/999/dataStreams/300"))

    def test_unrequested_incompatible_catalog_fields_do_not_block_report(self) -> None:
        self.transport.extra_incompatible = True
        result = self._plan(self._request(["acquisition", "landing", "device"]))
        self.assertEqual(result["status"], "ready")
        compatibility_calls = [call for call in self.transport.calls if call["url"].endswith(":checkCompatibility")]
        self.assertTrue(compatibility_calls)
        self.assertTrue(all(call["payload"]["compatibilityFilter"] == "COMPATIBLE" for call in compatibility_calls))

    def test_run_preserves_sampling_threshold_other_and_evidence(self) -> None:
        self.transport.sampled = True
        self.transport.thresholded = True
        self.transport.other = True
        planned = self._plan(self._request(["overview"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["status"], "partial")
        quality = result["report"]["datasets"][0]["quality"]
        self.assertEqual(quality["samplingByDateRange"][0]["ratio"], 0.5)
        self.assertTrue(quality["subjectToThresholding"])
        self.assertTrue(quality["dataLossFromOtherRow"])
        self.assertTrue(all(item.get("evidenceRefs") for item in result["report"]["facts"] + result["report"]["calculations"] + result["report"]["recommendations"]))

    def test_privacy_values_are_redacted_before_artifact(self) -> None:
        self.transport.redact_event = True
        planned = self._plan(self._request(["events"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        encoded = json.dumps(result["report"])
        self.assertNotIn("person@example.com", encoded)
        self.assertIn("[redacted]", encoded)
        self.assertTrue(any(item["type"] == "privacy" for item in result["report"]["limitations"]))

    def test_realtime_is_diagnostic_only_and_has_no_business_recommendation(self) -> None:
        planned = self._plan(self._request(["realtime"], comparisons=[]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["report"]["qualityTier"], "diagnostic_only")
        self.assertFalse(result["report"]["recommendations"])
        self.assertTrue(any(item["status"] == "supported" for item in result["report"]["interpretations"]))

    def test_funnel_requires_all_experimental_gates(self) -> None:
        result = self._plan(self._request(["funnel-experimental"]))
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any("feature flag" in item for item in result["plan"]["blockers"]))

    def test_valid_synthetic_funnel_uses_alpha_read_only_contract(self) -> None:
        plan = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan-v2.json").read_text())
        plan["status"] = "approved"
        plan["approvedAt"] = "2026-08-19T09:00:00Z"
        plan["approvalSha256"] = "a" * 64
        plan["funnels"] = [{"id": "lead-funnel", "businessQuestion": "Do users complete the lead flow?", "steps": ["generate_lead", "generate_lead"], "open": False, "directlyFollowed": False, "timeWindow": "28 days", "crossSession": True, "authoritativeCompletion": "generate_lead", "breakdownIntent": None, "limitations": [], "stage10Ready": True}]
        plan["contentSha256"] = plan_content_sha256(plan)
        measurement_path = self.root / "funnel-measurement.json"
        measurement_path.write_text(json.dumps(plan), encoding="utf-8")
        service = ReportService(auth=FakeAuth(), transport=self.transport, now=lambda: NOW, env={"GOOGLE_ANALYTICS_ADVISOR_EXPERIMENTAL_FUNNELS": "1"})
        request = self._request(["funnel-experimental"], measurementPlanRef=str(measurement_path), experimentalFunnel=True, alphaDisclosureAccepted=True)
        planned = service.plan(PROFILE, PROPERTY, request)
        self.assertEqual(planned["status"], "ready")
        self.assertEqual(planned["plan"]["queries"][0]["apiChannel"], "v1alpha")
        result = service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["report"]["datasets"][0]["returnedRows"], 1)
        self.assertFalse(any(call["method"] in {"PATCH", "PUT", "DELETE"} for call in self.transport.calls))

    def test_custom_report_blocks_user_level_and_query_string_fields(self) -> None:
        custom = {"dimensions": ["transactionId"], "metrics": ["sessions"], "filters": [], "rowLimit": 20}
        with self.assertRaises(AdvisorError) as caught:
            self._plan(self._request(["custom-core"], customCore=custom))
        self.assertEqual(caught.exception.code, "REPORT_FIELD_UNAVAILABLE")

    def test_tampered_and_expired_plans_are_blocked(self) -> None:
        planned = self._plan(self._request())
        path = Path(planned["artifact"]["path"])
        value = json.loads(path.read_text())
        value["periods"][0]["from"] = "2026-01-01"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AdvisorError) as caught:
            self.service.run(path)
        self.assertIn(caught.exception.code, {"ARTIFACT_VALIDATION_FAILED", "REPORT_PLAN_TAMPERED"})

    def test_quota_floor_stops_later_optional_queries(self) -> None:
        self.transport.low_quota = True
        planned = self._plan(self._request(["overview", "events"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        report_calls = [call for call in self.transport.calls if call["url"].endswith(":runReport")]
        self.assertEqual(len(report_calls), 1)
        self.assertTrue(any(item["type"] == "quota" for item in result["report"]["limitations"]))
        available = {item["datasetId"] for item in result["report"]["datasets"]}
        for recommendation in result["report"]["recommendations"]:
            self.assertTrue(set(recommendation["evidenceRefs"]).issubset(available))

    def test_restricted_revenue_is_unavailable_not_zero(self) -> None:
        self.transport.restricted = True
        planned = self._plan(self._request(["acquisition"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        headers = [item["name"] for item in result["report"]["datasets"][0]["metricHeaders"]]
        self.assertNotIn("totalRevenue", headers)
        self.assertTrue(any("restricted" in item["message"].lower() for item in result["report"]["limitations"]))

    def test_small_data_and_bilingual_plain_language_rendering(self) -> None:
        planned = self._plan(self._request(["overview"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertTrue(any(item["type"] == "small-data" for item in result["report"]["limitations"]))
        self.assertIn("Google Analytics", render_report(result["report"], "en"))
        self.assertIn("Google Analytics", render_report(result["report"], "ru"))

    def test_empty_property_is_labeled_empty_without_invented_recommendations(self) -> None:
        self.transport.empty = True
        planned = self._plan(self._request(["overview"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["status"], "empty")
        self.assertTrue(any(item["type"] == "empty" for item in result["report"]["limitations"]))
        evidence_ids = {item["datasetId"] for item in result["report"]["datasets"]}
        for recommendation in result["report"]["recommendations"]:
            self.assertTrue(set(recommendation["evidenceRefs"]).issubset(evidence_ids))

    def test_multilingual_dimension_value_is_preserved_when_not_pii(self) -> None:
        value = "Заявка подтверждена 日本語"
        self.assertEqual(redact_text(value), (value, False))

    def test_approved_measurement_plan_enables_business_context(self) -> None:
        plan = json.loads((ROOT / "contracts" / "fixtures" / "valid" / "measurement-plan-v2.json").read_text())
        plan["status"] = "approved"
        plan["approvedAt"] = "2026-08-19T09:00:00Z"
        plan["approvalSha256"] = "a" * 64
        plan["contentSha256"] = plan_content_sha256(plan)
        measurement_path = self.root / "measurement.json"
        measurement_path.write_text(json.dumps(plan), encoding="utf-8")
        planned = self._plan(self._request(["overview", "key-events"], measurementPlanRef=str(measurement_path)))
        self.assertEqual(planned["plan"]["propertyContext"]["qualityTier"], "business-context")


if __name__ == "__main__":
    unittest.main()
