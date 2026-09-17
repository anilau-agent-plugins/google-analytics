from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.google_analytics_cli.errors import AdvisorError
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.search_console_indexing_renderer import render_search_console_indexing_report
from scripts.google_analytics_cli.search_console_indexing_service import (
    SearchConsoleIndexingService,
    inspection_request_sha256,
    validate_inspection_url,
)


PROFILE = "profile-0123456789abcdef"
SITE = "sc-domain:example.com"
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)


class FakeAuth:
    def status(self, profile_id):
        return {"profileId": profile_id, "capabilities": {"searchConsole": {"status": "ready"}}}

    def access_token(self, profile_id):
        return profile_id, "private-token", {"clientRef": "client-fixture", "identity": {"sub": "subject-fixture"}}


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.fail_on_inspection = 0
        self.inspections = 0
        self.inspection_result = None

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        if url.endswith("/webmasters/v3/sites"):
            return JsonResponse(200, {"siteEntry": [{"siteUrl": SITE, "permissionLevel": "SITE_OWNER"}]}, "sites-request", {})
        if "/sitemaps/" in url:
            return JsonResponse(200, {"path": "https://example.com/sitemap.xml", "type": "WEB", "isPending": False, "isSitemapsIndex": False, "lastSubmitted": "2026-09-16T00:00:00Z", "lastDownloaded": "2026-09-17T00:00:00Z", "warnings": 0, "errors": 0, "contents": [{"type": "web", "submitted": 12, "indexed": 10}]}, "sitemap-get", {})
        if "/sitemaps" in url:
            return JsonResponse(200, {"sitemap": [{"path": "https://example.com/sitemap.xml", "type": "WEB", "isPending": False, "isSitemapsIndex": False, "warnings": 1, "errors": 0, "contents": [{"type": "web", "submitted": 12, "indexed": 10}]}, {"path": "https://example.com/index.xml", "type": "SITEMAP", "isPending": True, "isSitemapsIndex": True, "warnings": 0, "errors": 0, "contents": []}]}, "sitemaps-list", {})
        if url.endswith("/v1/urlInspection/index:inspect"):
            self.inspections += 1
            if self.fail_on_inspection and self.inspections == self.fail_on_inspection:
                raise AdvisorError("QUOTA_LIMITED", "limited", 5, retryable=False)
            inspected = kwargs["payload"]["inspectionUrl"]
            payload = self.inspection_result or {"inspectionResult": {"indexStatusResult": {"verdict": "PASS", "coverageState": "Submitted and indexed", "robotsTxtState": "ALLOWED", "indexingState": "INDEXING_ALLOWED", "pageFetchState": "SUCCESSFUL", "lastCrawlTime": "2026-09-16T04:00:00Z", "crawledAs": "MOBILE", "googleCanonical": inspected, "userCanonical": inspected, "sitemap": ["https://example.com/sitemap.xml"], "referringUrls": ["https://example.com/list?private=value"]}, "richResultsResult": {"verdict": "PASS", "detectedItems": [{"richResultType": "Breadcrumbs", "items": [{"name": "Breadcrumb", "issues": []}]}]}}}
            return JsonResponse(200, payload, f"inspect-{self.inspections}", {})
        raise AssertionError(url)


class SearchConsoleIndexingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.transport = FakeTransport()
        self.service = SearchConsoleIndexingService(auth=FakeAuth(), transport=self.transport, now=lambda: NOW)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def request(self, urls=None) -> Path:
        selections = urls or ["https://example.com/", "https://www.example.com/important"]
        value = {
            "schemaVersion": 1, "artifactType": "search-console-inspection-request",
            "createdAt": "2026-09-17T11:00:00Z", "requestId": "search-console-inspection-request-test0001", "contentSha256": "",
            "projectRoot": str(self.root), "profileId": PROFILE, "site": SITE,
            "question": "Are these important pages indexed?", "language": "en", "providerLanguageCode": "en-US",
            "sitemapSnapshotRef": None,
            "urls": [{"url": url, "reason": f"Important page {index + 1}", "sourceKind": "business-context", "evidenceRef": None, "sourceArtifact": None} for index, url in enumerate(selections)],
            "requestedBudget": {"maxUrls": len(selections)},
        }
        value["contentSha256"] = inspection_request_sha256(value)
        path = self.root / f"request-{len(list(self.root.glob('request-*.json')))}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_property_membership_is_exact_and_pre_network(self) -> None:
        self.assertTrue(validate_inspection_url("https://shop.example.com/a", SITE, "domain")["member"])
        self.assertTrue(validate_inspection_url("https://example.com/base/page", "https://example.com/base/", "url_prefix")["member"])
        for url, site, kind in [
            ("https://example.com.evil/", SITE, "domain"),
            ("http://example.com/base/", "https://example.com/base/", "url_prefix"),
            ("https://example.com/other", "https://example.com/base/", "url_prefix"),
            ("https://user@example.com/", SITE, "domain"),
            ("https://example.com/#fragment", SITE, "domain"),
            ("https://example.com/?email=user@example.com", SITE, "domain"),
        ]:
            with self.subTest(url=url):
                with self.assertRaises(AdvisorError):
                    validate_inspection_url(url, site, kind)

    def test_sitemap_list_is_bounded_and_ignores_deprecated_indexed(self) -> None:
        result = self.service.sitemaps(PROFILE, SITE, self.root)
        snapshot = result["snapshot"]
        self.assertEqual(snapshot["totals"]["returned"], 2)
        self.assertTrue(any(item["code"] == "DEPRECATED_INDEXED_FIELD_IGNORED" for item in snapshot["limitations"]))
        self.assertNotIn("indexed", repr(snapshot["entries"]))
        sitemap_calls = [item for item in self.transport.calls if "/sitemaps" in item["url"]]
        self.assertEqual(len(sitemap_calls), 1)
        self.assertEqual(sitemap_calls[0]["max_attempts"], 1)

    def test_sitemap_get_requires_exact_snapshot_path_and_encodes_feedpath(self) -> None:
        listed = self.service.sitemaps(PROFILE, SITE, self.root)
        result = self.service.sitemap(PROFILE, SITE, "https://example.com/sitemap.xml", Path(listed["artifact"]["path"]), self.root)
        self.assertEqual(result["snapshot"]["mode"], "get")
        get_call = next(item for item in self.transport.calls if "/sitemaps/" in item["url"])
        self.assertIn("https%3A%2F%2Fexample.com%2Fsitemap.xml", get_call["url"])
        with self.assertRaises(AdvisorError) as caught:
            self.service.sitemap(PROFILE, SITE, "https://evil.example/sitemap.xml", Path(listed["artifact"]["path"]), self.root)
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_SITEMAP_PARENT_MISMATCH")

    def test_nested_sitemap_list_requires_exact_index_from_snapshot(self) -> None:
        listed = self.service.sitemaps(PROFILE, SITE, self.root)
        nested = self.service.sitemaps(
            PROFILE, SITE, self.root, sitemap_index="https://example.com/index.xml",
            snapshot_path=Path(listed["artifact"]["path"]),
        )
        self.assertEqual(nested["snapshot"]["mode"], "index-list")
        nested_call = [item for item in self.transport.calls if "sitemapIndex=" in item["url"]][-1]
        self.assertIn("https%3A%2F%2Fexample.com%2Findex.xml", nested_call["url"])
        with self.assertRaises(AdvisorError):
            self.service.sitemaps(
                PROFILE, SITE, self.root, sitemap_index="https://example.com/not-returned.xml",
                snapshot_path=Path(listed["artifact"]["path"]),
            )

    def test_plan_is_immutable_bounded_and_does_not_read_inspection(self) -> None:
        result = self.service.plan(PROFILE, SITE, self.request())
        plan = result["plan"]
        self.assertEqual(plan["budget"], {"plannedUrlCalls": 2, "maxUrlCalls": 10, "automaticRetries": 0})
        self.assertTrue(plan["singleUse"])
        self.assertFalse(plan["inspectionDataRead"])
        self.assertFalse(any("urlInspection" in item["url"] for item in self.transport.calls))
        shown = self.service.show_plan(Path(result["artifact"]["path"]))
        self.assertEqual([item["url"] for item in shown["urls"]], ["https://example.com/", "https://www.example.com/important"])

    def test_run_is_sequential_single_use_and_preserves_sample_limitations(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request())
        plan_path = Path(planned["artifact"]["path"])
        result = self.service.run(plan_path)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["report"]["results"]), 2)
        self.assertEqual(result["report"]["budget"]["automaticRetries"], 0)
        inspection_calls = [item for item in self.transport.calls if "urlInspection" in item["url"]]
        self.assertTrue(all(item["max_attempts"] == 1 for item in inspection_calls))
        self.assertTrue(any(item["code"] == "INDEXED_VERSION_ONLY" for item in result["report"]["limitations"]))
        self.assertNotIn("private=value", repr(result["report"]))
        with self.assertRaises(AdvisorError) as caught:
            self.service.run(plan_path)
        self.assertEqual(caught.exception.code, "SEARCH_CONSOLE_INSPECTION_PLAN_ALREADY_USED")

    def test_partial_failure_stops_and_is_not_retried(self) -> None:
        self.transport.fail_on_inspection = 2
        planned = self.service.plan(PROFILE, SITE, self.request(["https://example.com/", "https://example.com/two", "https://example.com/three"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["report"]["results"]), 1)
        self.assertEqual(result["report"]["failure"]["code"], "SEARCH_CONSOLE_INSPECTION_QUOTA_EXCEEDED")
        self.assertEqual(len([item for item in self.transport.calls if "urlInspection" in item["url"]]), 2)

    def test_malformed_first_response_creates_failed_evidence(self) -> None:
        self.transport.inspection_result = {"inspectionResult": {"indexStatusResult": []}}
        planned = self.service.plan(PROFILE, SITE, self.request(["https://example.com/"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["report"]["results"], [])
        self.assertEqual(result["report"]["failure"]["code"], "SEARCH_CONSOLE_INSPECTION_RESPONSE_INVALID")

    def test_unknown_verdict_outside_canonical_and_deprecated_mobile_are_limited(self) -> None:
        self.transport.inspection_result = {"inspectionResult": {
            "indexStatusResult": {"verdict": "NEW_PROVIDER_VALUE", "coverageState": "Excluded", "robotsTxtState": "ALLOWED", "indexingState": "INDEXING_ALLOWED", "pageFetchState": "SUCCESSFUL", "lastCrawlTime": "2026-09-16T04:00:00Z", "googleCanonical": "https://outside.example/page", "userCanonical": "https://example.com/page"},
            "mobileUsabilityResult": {"verdict": "PASS", "issues": []},
            "richResultsResult": {"verdict": "FAIL", "detectedItems": [{"richResultType": "Product", "items": [{"name": "Product", "issues": [{"issueMessage": "Missing price", "severity": "ERROR"}]}]}]},
        }}
        planned = self.service.plan(PROFILE, SITE, self.request(["https://example.com/page"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        inspected = result["report"]["results"][0]
        self.assertTrue(inspected["googleCanonicalOutsideSelectedProperty"])
        self.assertTrue(inspected["mobileUsabilityResult"]["deprecated"])
        codes = {item["code"] for item in result["report"]["limitations"]}
        self.assertIn("UNKNOWN_PROVIDER_VERDICT", codes)
        self.assertIn("MOBILE_USABILITY_DEPRECATED", codes)
        self.assertTrue(any("rich-result error" in item["problem"] for item in result["report"]["recommendations"]))

    def test_expired_and_tampered_plans_are_blocked(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request(["https://example.com/"]))
        path = Path(planned["artifact"]["path"])
        future = SearchConsoleIndexingService(auth=FakeAuth(), transport=self.transport, now=lambda: NOW + timedelta(minutes=31))
        with self.assertRaises(AdvisorError) as expired:
            future.run(path)
        self.assertEqual(expired.exception.code, "SEARCH_CONSOLE_INSPECTION_PLAN_EXPIRED")
        value = json.loads(path.read_text(encoding="utf-8"))
        value["site"] = "sc-domain:other.example"
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(AdvisorError) as tampered:
            self.service.show_plan(path)
        self.assertIn(tampered.exception.code, {"ARTIFACT_VALIDATION_FAILED", "SEARCH_CONSOLE_INSPECTION_PLAN_TAMPERED"})

    def test_renderer_is_bilingual_and_explicit_about_indexed_version(self) -> None:
        planned = self.service.plan(PROFILE, SITE, self.request(["https://example.com/"]))
        result = self.service.run(Path(planned["artifact"]["path"]))
        english = render_search_console_indexing_report(result["report"], "en")
        russian = render_search_console_indexing_report(result["report"], "ru")
        self.assertIn("not a live test", english)
        self.assertIn("не live-проверка", russian)
        self.assertIn("Причина выбора", russian)


if __name__ == "__main__":
    unittest.main()
