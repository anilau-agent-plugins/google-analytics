from __future__ import annotations

import unittest

from scripts.google_analytics_cli.analytics_data import AnalyticsDataClient
from scripts.google_analytics_cli.http import JsonResponse
from scripts.google_analytics_cli.tag_manager import TagManagerClient


class FakeExecutor:
    def __init__(self): self.calls = []
    def execute(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        data = {
            "data.metadata.get": {"metrics": [{"apiName": "eventCount"}, {"apiName": "keyEvents"}]},
            "data.compatibility.check": {"metricCompatibilities": []},
            "data.report.run": {"rows": [{"dimensionValues": [{"value": "purchase"}]}], "rowCount": 1, "propertyQuota": {}},
            "gtm.workspaces.list": {"workspace": []},
            "gtm.version_headers.list": {"containerVersionHeader": []},
            "gtm.live_version.get": {},
        }.get(operation, {})
        return JsonResponse(200, data, None, {})


class Stage5ClientTests(unittest.TestCase):
    def test_data_diagnostic_is_bounded_and_never_calls_measurement_protocol_secrets(self) -> None:
        executor = FakeExecutor()
        result = AnalyticsDataClient(executor).event_diagnostic("properties/123")
        operations = [item[0] for item in executor.calls]
        self.assertEqual(operations, ["data.metadata.get", "data.compatibility.check", "data.report.run"])
        self.assertNotIn("secret", " ".join(operations).lower())
        payload = executor.calls[-1][1]["payload"]
        self.assertEqual(payload["dateRanges"], [{"startDate": "28daysAgo", "endDate": "yesterday"}])
        self.assertTrue(payload["returnPropertyQuota"])
        self.assertTrue(result["bounded"])

    def test_gtm_calls_are_serialized_and_deep_scope_is_bounded(self) -> None:
        executor = FakeExecutor()
        clock = [0.0]
        sleeps = []
        def monotonic(): return clock[0]
        def sleep(value): sleeps.append(value); clock[0] += value
        client = TagManagerClient(executor, sleep=sleep, monotonic=monotonic)
        client.container_baseline("accounts/1/containers/2")
        self.assertEqual([item[0] for item in executor.calls], [
            "gtm.workspaces.list", "gtm.version_headers.list", "gtm.live_version.get"
        ])
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(value >= 4.1 for value in sleeps))


if __name__ == "__main__":
    unittest.main()
