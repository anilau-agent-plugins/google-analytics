from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.google_analytics_cli.baseline_audit import BaselineService
from scripts.google_analytics_cli.contracts import validate_artifact
from scripts.google_analytics_cli.errors import AdvisorError, EXIT_NETWORK
from scripts.google_analytics_cli.http import JsonResponse


class FixtureTransport:
    def __init__(self, fail_data=False): self.calls = []; self.fail_data = fail_data
    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if url.endswith("/dataStreams?pageSize=200"):
            data = {"dataStreams": [{"name": "properties/123/dataStreams/456", "type": "WEB_DATA_STREAM", "webStreamData": {"measurementId": "G-TEST123"}}]}
        elif url.endswith("/keyEvents?pageSize=200"):
            data = {"keyEvents": []}
        elif url.endswith("/customDimensions?pageSize=200"):
            data = {"customDimensions": []}
        elif url.endswith("/customMetrics?pageSize=200"):
            data = {"customMetrics": []}
        elif url.endswith("/dataRetentionSettings"):
            data = {"eventDataRetention": "FOURTEEN_MONTHS"}
        elif url.endswith("/metadata"):
            data = {"metrics": [{"apiName": "eventCount"}, {"apiName": "keyEvents"}]}
        elif url.endswith(":checkCompatibility"):
            data = {"dimensionCompatibilities": [], "metricCompatibilities": []}
        elif url.endswith(":runReport"):
            if self.fail_data:
                raise AdvisorError("QUOTA_LIMITED", "Synthetic quota limit.", EXIT_NETWORK)
            data = {"rows": [{"dimensionValues": [{"value": "page_view"}], "metricValues": [{"value": "10"}, {"value": "0"}]}], "rowCount": 1, "metadata": {"subjectToThresholding": False}, "propertyQuota": {}}
        elif url.endswith("/v1beta/properties/123"):
            data = {"name": "properties/123", "displayName": "Example", "timeZone": "UTC", "currencyCode": "USD"}
        else:
            raise AssertionError(f"Unexpected URL: {url}")
        return JsonResponse(200, data, "request-fixture", {})


class FakeAuth:
    def __init__(self, fail_data=False): self.json_transport = FixtureTransport(fail_data)
    def access_token(self, profile): return profile or "profile-active", "access-value", {}


class BaselineAuditTests(unittest.TestCase):
    def test_complete_synthetic_read_only_audit_writes_valid_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "index.html").write_text("G-TEST123", encoding="utf-8")
            auth = FakeAuth()
            result = BaselineService(auth=auth).audit(
                "profile-fixture", root, property_name="properties/123",
                stream_name="properties/123/dataStreams/456", gtm_container=None,
            )
            audit_path = Path(result["artifact"]["path"])
            validation = validate_artifact("baseline-report", audit_path)
            snapshot_paths = list((root / ".google-analytics-advisor" / "snapshots").glob("*.json"))
        self.assertTrue(validation["valid"])
        self.assertEqual(len(snapshot_paths), 3)
        self.assertFalse(result["mutationPerformed"])
        self.assertTrue(all(method in {"GET", "POST"} for method, _, _ in auth.json_transport.calls))
        post_urls = [url for method, url, _ in auth.json_transport.calls if method == "POST"]
        self.assertTrue(all(url.endswith((":checkCompatibility", ":runReport")) for url in post_urls))
        self.assertNotIn("secret", " ".join(url for _, url, _ in auth.json_transport.calls).lower())

    def test_data_failure_produces_valid_partial_audit_and_keeps_other_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "index.html").write_text("G-TEST123", encoding="utf-8")
            result = BaselineService(auth=FakeAuth(fail_data=True)).audit(
                "profile-fixture", root, property_name="properties/123",
                stream_name="properties/123/dataStreams/456", gtm_container=None,
            )
            validation = validate_artifact("baseline-report", Path(result["artifact"]["path"]))
            snapshots = list((root / ".google-analytics-advisor" / "snapshots").glob("*.json"))
        self.assertTrue(validation["valid"])
        self.assertEqual(result["audit"]["completeness"], "partial")
        self.assertEqual(len(snapshots), 2)
        self.assertIn("QUOTA_LIMITED", {item["code"] for item in result["audit"]["limitations"]})


if __name__ == "__main__":
    unittest.main()
